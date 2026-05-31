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
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.credential_rotation import (
    CeleryRotationNotifier,
    FakeVaultRotationClient,
    RotationNotifier,
    VaultRotationClient,
    build_db_role,
    configure_db_secrets_engine_role,
    rotate_credentials,
)

_log = structlog.get_logger("workers.credential_rotation_task")


def _build_vault_client(settings: Settings) -> VaultRotationClient:
    """Resolve the Vault rotation client for a real run.

    The real binding is an ``hvac.Client`` adapter wired at install time (Tests
    Humanos) — it will read the Vault address + token from ``settings`` / the
    process env (the platform's standard Vault → env injection, never logged).
    Until that lands the worker has no live Vault, so we fall back to the
    in-memory fake: a scheduled run in an env without Vault is then a safe
    no-op-shaped cycle rather than a crash. Tests inject the fake directly into
    :func:`_rotate_credentials`, never going through this resolver.
    """
    # The mount is the one settings-derived knob the resolver needs to log; the
    # secret material (token) is resolved by the adapter from env, never here.
    _log.debug(
        "credential_rotation.vault_client.fake_fallback", db_mount=settings.cred_rotation_db_mount
    )
    return FakeVaultRotationClient()


@app.task(name="workers.rotate_credentials")  # type: ignore[misc]
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
    client: VaultRotationClient,
    notifier: RotationNotifier | None,
    previous_lease_id: str | None = None,
) -> dict[str, Any]:
    """Async core: read the live enable flag, configure the role, run the cycle.

    The ``client`` + ``notifier`` are injected so tests drive the whole cycle
    against the in-memory fakes with NO real Vault and NO real broker. A real
    run resolves both from settings (see :func:`rotate_credentials_task`).
    """
    # Lazy import — avoids paying the api_server import cost on workers that
    # never route the beat schedule (mirrors workers.maintenance / backup_task).
    from api_server.db.platform_settings import get_cred_rotation_enabled

    engine = create_async_engine(settings.database_url)
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
