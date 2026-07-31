"""Scheduled credential-rotation beat task (Plan 15 task_15_17).

A Celery beat task, ``workers.rotate_credentials``, that runs the credential
rotation cycle (:mod:`workers.credential_rotation`) on a CONFIGURABLE cadence.
Wired to beat by :mod:`workers.beat_schedule` (default weekly Sunday 02:00 UTC,
``WORKERS_CRED_ROTATION_CRON``) and gated by a live ``cred_rotation_enabled``
PLATFORM setting a System Admin flips from the admin panel.

Best-effort, like the other beat tasks (:mod:`workers.maintenance`,
:mod:`workers.backup_task`): a single run failure must not crash beat. The
rotation engine itself NEVER raises (a failure is audited + alerted via the
Plan 10 notifier and the system stays up on its current credentials); this
wrapper additionally guards against an unexpected error before/around the engine
so beat keeps firing on cadence.

Multi-tenancy / RBAC: rotation is platform-global (it rotates the platform's
static secrets + the shared dynamic-DB role). A tenant CANNOT trigger or
schedule it — the schedule lives in the platform beat process and the enable
flag is a platform setting only a System Admin can write.

Secrets are never logged: this wrapper only ever logs the audit's secret-free
log fields (names / lease-ids / counts), never a credential value.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.credential_rotation import (
    CeleryRotationNotifier,
    RotationAudit,
    RotationNotifier,
    RotationStatus,
    VaultRotationClient,
    build_db_role,
    configure_db_secrets_engine_role,
    rotate_credentials,
)
from workers.db import worker_engine

_log = structlog.get_logger("workers.credential_rotation_task")

#: Why a cycle was skipped. Recorded in the audit's ``error`` field (the audit has
#: no dedicated reason column) so the alert an operator receives says WHAT is
#: missing rather than just "skipped".
_SKIP_NO_VAULT = (
    "no live Vault is configured (WORKERS_VAULT_URL / WORKERS_VAULT_TOKEN are "
    "unset), so nothing could be rotated. This cycle rotated NO credential."
)


def _build_vault_client(settings: Settings) -> VaultRotationClient | None:
    """Resolve the REAL Vault rotation client, or ``None`` when Vault is absent.

    This function used to return :class:`FakeVaultRotationClient` unconditionally
    — an in-memory dict — which is how the scheduled job wrote
    ``status=SUCCEEDED`` every week without touching anything (gap2-1, critical;
    the runbook pointed EMERGENCY REVOCATION at it). The fake is no longer
    importable here: a ``SUCCEEDED`` audit can now only come from an hvac client
    talking to a real Vault.

    ``None`` (no Vault wired) is NOT an error and NOT a success — the caller turns
    it into a ``SKIPPED`` cycle with an operator alert. A dev machine without
    Vault therefore gets a loud, honest no-op instead of a silent lie.

    Secrets are never logged: only the URL's presence and the mount name.
    """
    if not settings.vault_url or not settings.vault_token:
        _log.warning(
            "credential_rotation.vault_client.absent",
            has_url=bool(settings.vault_url),
            has_token=bool(settings.vault_token),
        )
        return None

    try:
        import hvac
    except ImportError:  # pragma: no cover - hvac is a declared dependency
        _log.warning("credential_rotation.vault_client.hvac_missing")
        return None

    from workers.credential_rotation_hvac import (
        HvacVaultRotationClient,
        MinioServiceAccountRotator,
    )

    # The MinIO admin binding is OPTIONAL at construction and mandatory at use:
    # without it, rotating `minio` raises instead of writing a KV entry for a
    # credential MinIO never issued (task_prod05_07).
    minio_rotator = None
    if settings.cred_rotation_minio_root_user:
        minio_rotator = MinioServiceAccountRotator(
            endpoint=settings.cred_rotation_minio_url,
            root_user=settings.cred_rotation_minio_root_user,
            root_password=settings.cred_rotation_minio_root_password.get_secret_value(),
        )

    _log.info(
        "credential_rotation.vault_client.hvac",
        db_mount=settings.cred_rotation_db_mount,
        minio_wired=minio_rotator is not None,
    )
    return HvacVaultRotationClient(
        hvac.Client(url=settings.vault_url, token=settings.vault_token),
        minio_rotator=minio_rotator,
    )


def _skipped_summary(notifier: RotationNotifier | None, reason: str) -> dict[str, Any]:
    """Audit + alert a cycle that rotated nothing, and say so in the return value.

    ``ok`` is False and the status is ``skipped``: the two fields an operator (or
    a dashboard) reads must BOTH refuse to look like success. The alert reuses the
    Plan 10 failure lane — "the weekly rotation did not run" is exactly the kind
    of thing that must not sit unnoticed in a log file for a quarter.
    """
    audit = RotationAudit(
        rotated_at=datetime.now(UTC),
        status=RotationStatus.SKIPPED,
        static_secrets=(),
        new_lease_id=None,
        renewed_lease_id=None,
        revoked_lease_id=None,
        error=reason,
    )
    _log.warning("credential_rotation.skipped", **audit.as_log_fields())
    alerted = False
    if notifier is not None:
        try:
            notifier.alert_failure(audit)
            alerted = True
        except Exception as exc:  # pragma: no cover - defensive: beat must not die
            _log.warning("credential_rotation.alert_failed", error=str(exc))
    return {"enabled": True, "ok": False, "alerted": alerted, **audit.as_log_fields()}


@app.task(name="workers.rotate_credentials")  # type: ignore[untyped-decorator]
def rotate_credentials_task() -> dict[str, Any]:
    """Run the scheduled credential-rotation cycle.

    Honours the ``cred_rotation_enabled`` platform setting (a System Admin's
    live OFF switch). Best-effort: a failure is logged, never raised, so beat
    keeps its cadence. Returns a small dict summarising the run.
    """
    settings = get_settings()
    notifier = CeleryRotationNotifier(
        broker_url=settings.broker_url,
        priority_queue="notifications.priority",
    )
    return asyncio.run(
        _rotate_credentials(settings, client=_build_vault_client(settings), notifier=notifier)
    )


async def _rotate_credentials(
    settings: Settings,
    *,
    client: VaultRotationClient | None,
    notifier: RotationNotifier | None,
    previous_lease_id: str | None = None,
) -> dict[str, Any]:
    """Async core: read the live enable flag, configure the role, run the cycle.

    The ``client`` + ``notifier`` are injected so tests drive the whole cycle
    against the in-memory fakes with NO real Vault and NO real broker. A real
    run resolves both from settings (see :func:`rotate_credentials_task`).

    ``client is None`` means the resolver found no live Vault: the cycle is
    SKIPPED with an alert (prod-05 task_prod05_05). It is deliberately NOT
    treated as a failure of the platform and deliberately NOT treated as success:
    the previous behaviour — falling back to an in-memory fake and auditing
    SUCCEEDED — is the bug this whole task exists to remove.
    """
    # Lazy import — avoids paying the api_server import cost on workers that
    # never route the beat schedule (mirrors workers.maintenance / backup_task).
    from api_server.db.platform_settings import get_cred_rotation_enabled

    engine = worker_engine(settings)
    try:
        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as db:
            enabled = await get_cred_rotation_enabled(db)
    except Exception as exc:  # pragma: no cover — defensive: beat must not die
        _log.warning("credential_rotation.enable_read_error", error=str(exc))
        # If we can't even read the flag, default to running: a missed rotation
        # leaves stale credentials in place, which is the worse outcome.
        enabled = True
    finally:
        await engine.dispose()

    if not enabled:
        _log.info("credential_rotation.skipped", reason="disabled")
        return {"enabled": False, "skipped": True}

    # No Vault -> SKIPPED + alert. Checked AFTER the enable flag so an operator
    # who deliberately turned rotation off does not get paged about it.
    if client is None:
        return _skipped_summary(notifier, _SKIP_NO_VAULT)

    # Configure (idempotently) the database secrets-engine role so a service
    # can read short-TTL dynamic creds, then run one rotation cycle. Both are
    # best-effort at the wrapper level; the engine itself never raises.
    role = build_db_role(
        name=settings.cred_rotation_db_role,
        db_connection=settings.cred_rotation_db_connection,
        ttl_s=settings.cred_rotation_db_ttl_s,
        max_ttl_s=settings.cred_rotation_db_max_ttl_s,
    )
    try:
        configure_db_secrets_engine_role(client, role, mount=settings.cred_rotation_db_mount)
    except Exception as exc:  # pragma: no cover — defensive: beat must not die
        _log.warning("credential_rotation.role_config_error", error=str(exc))
        return {"enabled": True, "ok": False, "error": str(exc)}

    outcome = rotate_credentials(
        client,
        db_role_name=settings.cred_rotation_db_role,
        db_mount=settings.cred_rotation_db_mount,
        static_secret_names=tuple(settings.cred_rotation_static_secrets),
        previous_lease_id=previous_lease_id,
        notifier=notifier,
    )

    # The audit's log fields are secret-free; the summary mirrors them so the
    # operator sees the run honoured the configured rotation without exposing a
    # single credential value.
    summary: dict[str, Any] = {
        "enabled": True,
        "ok": outcome.ok,
        "alerted": outcome.alerted,
        **outcome.audit.as_log_fields(),
    }
    return summary
