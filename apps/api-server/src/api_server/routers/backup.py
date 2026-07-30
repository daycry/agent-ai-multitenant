"""`/admin/backup/schedule` — the configurable backup schedule (Plan 12 task_12_04).

A System Admin configures the daily backup cadence (cron), the live enable
flag, and the local retention window from the admin panel — NOT a hardcoded
cron. The three values live in ``platform_settings`` (``backup_enabled`` /
``backup_cron`` / ``backup_retention_days``); the backup beat task
(``workers.run_daily_backup``) reads them live so a change takes effect on the
next run without restarting Celery.

Read/write split (mirrors the notifications platform-settings pattern):

  - ``GET  /admin/backup/schedule``   read the schedule (System Admin only —
    the whole ``/admin`` surface is System-Admin + hardened; prod-09
    task_prod09_01 closed the hole where this read accepted any tenant member).
  - ``PUT  /admin/backup/schedule``   set it (System Admin only, BYPASSRLS
    ``get_admin_session``; ``set_platform_setting`` re-checks the actor is a
    System Admin so a Tenant Admin can never reach the write).

The router is mounted under ``/admin``, so ``api_server.main`` attaches
:func:`api_server.auth.admin_hardening.require_hardened_system_admin` at mount
time: in staging/prod every route here also needs MFA + an allowlisted source IP
+ a session younger than the short admin TTL. That matters most for the
DESTRUCTIVE ``POST /admin/backup/restore``.

The cron + retention window are validated server-side
(``db.platform_settings.validate_*``); an invalid value is a clean 422 with no
write. Each successful write is audited (``backup.schedule_updated``).
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.audit import write_audit_log
from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    require_system_admin,
)
from api_server.db.models import User
from api_server.db.platform_settings import (
    InvalidBackupDestinationError,
    InvalidBackupScheduleError,
    get_backup_destinations,
    get_backup_schedule,
    set_backup_destinations,
    set_backup_schedule,
)
from api_server.schemas.backup import (
    BackupArtifactPreview,
    BackupConnectivityResult,
    BackupDestination,
    BackupDestinationsResponse,
    BackupDestinationsUpdate,
    BackupListItem,
    BackupListResponse,
    BackupPreviewResponse,
    BackupScheduleResponse,
    BackupScheduleUpdate,
    RestoreJobStatus,
    RestoreTriggerRequest,
    RestoreTriggerResponse,
)

router = APIRouter(prefix="/admin/backup", tags=["admin", "backup"])

_AUDIT_SCHEDULE_UPDATED = "backup.schedule_updated"
_AUDIT_DESTINATIONS_UPDATED = "backup.destinations_updated"
_AUDIT_CONNECTIVITY_TESTED = "backup.destination_connectivity_tested"
_AUDIT_RESTORE_TRIGGERED = "backup.restore_triggered"


@router.get("/schedule", response_model=BackupScheduleResponse)
async def read_backup_schedule(
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> BackupScheduleResponse:
    """The current backup schedule (enabled + cron + retention_days).

    System-Admin only, like the rest of ``/admin/backup``: this used to accept
    any authenticated tenant member (``require_tenant_member``), which leaked
    the platform's backup cadence and retention window — operational
    intelligence about when the platform is least defended — to every tenant
    user, on an endpoint whose sibling is a destructive restore (authz-1).
    ``platform_settings`` carries no RLS, so this is a plain global read; when
    unset, the platform defaults apply (enabled, daily 03:00, 7-day retention).
    """
    enabled, cron, retention_days = await get_backup_schedule(session)
    return BackupScheduleResponse(enabled=enabled, cron=cron, retention_days=retention_days)


@router.put("/schedule", response_model=BackupScheduleResponse)
async def update_backup_schedule(
    payload: BackupScheduleUpdate,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> BackupScheduleResponse:
    """Set the backup schedule (System Admin only).

    Validates the cron + retention window before any write (a bad value is a
    422, nothing persisted). RBAC: ``require_system_admin`` (a tenant caller is
    403) on the BYPASSRLS admin session; ``set_backup_schedule`` re-checks the
    actor is a System Admin. The change is audited.
    """
    actor = await session.get(User, principal.user_id)
    if actor is None:  # pragma: no cover - a valid session always has a user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="actor not found")

    try:
        enabled, cron, retention_days = await set_backup_schedule(
            session,
            enabled=payload.enabled,
            cron=payload.cron,
            retention_days=payload.retention_days,
            actor=actor,
        )
    except InvalidBackupScheduleError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    await write_audit_log(
        session,
        action=_AUDIT_SCHEDULE_UPDATED,
        actor_user_id=principal.user_id,
        tenant_id=None,
        resource_type="platform_setting",
        resource_id=None,
        changes={"enabled": enabled, "cron": cron, "retention_days": retention_days},
    )
    return BackupScheduleResponse(enabled=enabled, cron=cron, retention_days=retention_days)


# ---------------------------------------------------------------------------
# Remote backup destinations (Plan 12 Phase B — task_12_09)
# ---------------------------------------------------------------------------
# A System Admin manages the list of remote destinations (S3, B2, SFTP/NAS,
# rclone) the bundle is uploaded to after a successful, verified backup, and can
# probe each one's connectivity. The stored config is NON-secret only — the
# CREDENTIALS live in the workers' secret seam (Vault/env) and are NEVER part of
# any request/response here, so a destination can never be created with, nor read
# back exposing, a credential.


@router.get("/destinations", response_model=BackupDestinationsResponse)
async def read_backup_destinations(
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> BackupDestinationsResponse:
    """The configured remote backup destinations (NON-secret config only).

    System-Admin only (it used to accept any tenant member): the config holds no
    credential, but it DOES name the buckets/hosts every tenant's data is copied
    to — a map of the off-site copies, which is not a tenant user's business
    (authz-1). ``platform_settings`` carries no RLS, so this is a plain global
    read."""
    items = await get_backup_destinations(session)
    return BackupDestinationsResponse(
        destinations=[BackupDestination.model_validate(item) for item in items]
    )


@router.put("/destinations", response_model=BackupDestinationsResponse)
async def update_backup_destinations(
    payload: BackupDestinationsUpdate,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> BackupDestinationsResponse:
    """Replace the full destination list (System Admin only).

    Validates EVERY item before any write (unknown type, missing required field,
    or any secret-looking field -> 422, nothing persisted). RBAC:
    ``require_system_admin`` (a tenant caller is 403) on the BYPASSRLS admin
    session; ``set_backup_destinations`` re-checks the actor is a System Admin.
    The change is audited (names + types only — never a credential)."""
    actor = await session.get(User, principal.user_id)
    if actor is None:  # pragma: no cover - a valid session always has a user
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="actor not found")

    items = [dest.model_dump() for dest in payload.destinations]
    try:
        normalised = await set_backup_destinations(session, items, actor=actor)
    except InvalidBackupDestinationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    await write_audit_log(
        session,
        action=_AUDIT_DESTINATIONS_UPDATED,
        actor_user_id=principal.user_id,
        tenant_id=None,
        resource_type="platform_setting",
        resource_id=None,
        # Names + types only — the stored config is non-secret, but we still keep
        # the audit trail to identifiers, never the (already non-secret) config.
        changes={"destinations": [{"name": d["name"], "type": d["type"]} for d in normalised]},
    )
    return BackupDestinationsResponse(
        destinations=[BackupDestination.model_validate(item) for item in normalised]
    )


@router.post("/destinations/{name}/test", response_model=BackupConnectivityResult)
async def test_backup_destination(
    name: str,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> BackupConnectivityResult:
    """Run the adapter's ``test_connectivity`` for one destination by name.

    System-Admin only (a connectivity probe resolves the destination's
    credentials from the secret seam). Looks up the destination's NON-secret
    config, builds the right adapter via the workers' registered-by-type factory,
    and runs its cheap reachability/auth probe (``head_bucket`` / SFTP ``stat`` /
    ``rclone lsd``). Returns a typed ok/detail result; the adapter guarantees the
    ``detail`` never carries a credential. The probe is audited (ok flag + name,
    never a secret)."""
    items = await get_backup_destinations(session)
    match = next((item for item in items if item.get("name") == name), None)
    if match is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no backup destination named {name!r}",
        )

    # Lazy import — keep the workers' boto3/paramiko import cost off the
    # api-server hot path; this endpoint is the only one that needs the adapters.
    from workers.backup_destinations import DestinationError, build_destination
    from workers.backup_encryption import EnvSecretsProvider

    # The destination's NON-secret config plus its type/name, as the factory
    # expects. Credentials are resolved by the adapter through the secret seam —
    # NEVER from this dict (validation guarantees it holds no secret).
    factory_config = {"type": match["type"], "name": match["name"], **match.get("config", {})}
    try:
        destination = build_destination(factory_config, secrets=EnvSecretsProvider())
        # `test_connectivity` es SÍNCRONO y hace red: `head_bucket` de boto3, un
        # `stat` de paramiko, `rclone lsd`. Contra un destino inalcanzable se
        # queda esperando el timeout del socket, y ejecutado en el bucle de
        # eventos congela TODAS las requests y WebSockets del api-server
        # (hallazgo api-3). `to_thread` lo saca a un hilo del executor: la
        # petición sigue esperando, el resto de la plataforma no.
        result = await asyncio.to_thread(destination.test_connectivity)
    except DestinationError as exc:
        # A config the factory rejects (shouldn't happen post-validation) maps to
        # a clean not-ok result rather than a 500 — the UI renders FAIL + detail.
        result_ok, result_detail = False, str(exc)
    else:
        result_ok, result_detail = result.ok, result.detail

    await write_audit_log(
        session,
        action=_AUDIT_CONNECTIVITY_TESTED,
        actor_user_id=principal.user_id,
        tenant_id=None,
        resource_type="platform_setting",
        resource_id=None,
        changes={"name": name, "type": match["type"], "ok": result_ok},
    )
    return BackupConnectivityResult(ok=result_ok, detail=result_detail)


# ---------------------------------------------------------------------------
# Restore (Plan 12 Phase C — task_12_12)
# ---------------------------------------------------------------------------
# A System Admin restores from a backup bundle: LIST the available backups
# (local on disk + remote via the destinations), PREVIEW one bundle's manifest
# (with the per-tenant option), and TRIGGER a restore (full or per-tenant).
#
# Restore is LONG + DESTRUCTIVE, so the trigger ENQUEUES a Celery background job
# (workers.run_restore / workers.run_restore_per_tenant) — it NEVER runs the
# restore inline on this request thread — and the UI polls the job's status. A
# DOUBLE confirmation is required: the request carries a `confirm` token the
# endpoint re-derives + checks server-side (the bundle id for a full restore,
# `<tenant_id>@<backup_id>` for a per-tenant one) so a destructive restore can
# never fire from a single click. All four endpoints are System-Admin only.


def _full_confirm_token(backup_id: str) -> str:
    """The expected double-confirm token for a FULL restore (the bundle id)."""
    return backup_id


def _per_tenant_confirm_token(tenant_id: str, backup_id: str) -> str:
    """The expected double-confirm token for a per-tenant restore.

    ``<tenant_id>@<backup_id>`` — mirrors
    ``workers.restore_per_tenant.confirmation_token`` exactly (the two packages
    deliberately do not import one another on the api-server hot path). Binds the
    confirmation to BOTH the tenant and the specific bundle."""
    return f"{tenant_id}@{backup_id}"


@router.get("/restore/backups", response_model=BackupListResponse)
async def list_restore_backups(
    _: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> BackupListResponse:
    """List the backups available to restore (local on disk + remote), newest first.

    System-Admin only — enumerating backups + probing remote destinations is a
    privileged operation. Reads each LOCAL bundle's ``manifest.json`` for its
    summary (encrypted / created_at / size) and merges in any backup found only
    at a configured remote destination (matched by bundle id). A remote-only
    backup carries no manifest summary until it is downloaded (its summary fields
    are ``None``). Read-only — nothing is decrypted, verified, or restored here.
    """
    from api_server.backup_restore import list_local_bundles
    from api_server.config import get_settings

    settings = get_settings()
    local = list_local_bundles(settings.backup_root)

    # Index by bundle id so a backup present both locally + remotely is one row
    # whose ``locations`` lists every place it was found.
    by_id: dict[str, BackupListItem] = {}
    for bundle in local:
        by_id[bundle.backup_id] = BackupListItem(
            backup_id=bundle.backup_id,
            encrypted=bundle.encrypted,
            created_at=bundle.created_at,
            total_size_bytes=bundle.total_size_bytes,
            locations=["local"],
        )

    # Merge remote listings (best-effort per destination — one unreachable
    # destination must not blank the whole list). The destination's bundle file
    # name maps back to a bundle id by stripping a known suffix.
    for entry, dest_name in await _list_remote_backups(session):
        bid = _strip_bundle_suffix(entry)
        existing = by_id.get(bid)
        if existing is None:
            by_id[bid] = BackupListItem(backup_id=bid, locations=[dest_name])
        elif dest_name not in existing.locations:
            existing.locations.append(dest_name)

    backups = sorted(by_id.values(), key=lambda b: b.backup_id, reverse=True)
    return BackupListResponse(backups=backups)


def _strip_bundle_suffix(name: str) -> str:
    """Map a remote object name back to its bundle id.

    A remote bundle is uploaded as a single artifact named after the bundle id
    with a known suffix (``.tar`` / ``.tar.enc`` / ``.tar.gz``); strip it so the
    remote entry lines up with the local bundle directory name."""
    for suffix in (".tar.enc", ".tar.gz", ".tar"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


async def _list_remote_backups(session: AsyncSession) -> list[tuple[str, str]]:
    """List (object_name, destination_name) for every configured + enabled remote.

    Best-effort: a destination that errors (unreachable / bad creds) is logged
    and skipped so one bad remote never blanks the whole backup list. Returns an
    empty list when no destinations are configured."""
    items = await get_backup_destinations(session)
    if not items:
        return []

    from workers.backup_destinations import DestinationError, build_destination
    from workers.backup_encryption import EnvSecretsProvider

    secrets = EnvSecretsProvider()

    def _list_one(item: dict[str, object]) -> list[tuple[str, str]]:
        """Build the adapter and enumerate it. SÍNCRONO y con red — se ejecuta
        en un hilo, nunca en el bucle de eventos (hallazgo api-3): con N destinos
        remotos configurados, uno solo inalcanzable bloqueaba el api-server
        entero durante su timeout de socket."""
        name = str(item["name"])
        factory_config = {
            "type": item["type"],
            "name": item["name"],
            **(item.get("config") or {}),  # type: ignore[dict-item]
        }
        try:
            destination = build_destination(factory_config, secrets=secrets)
            return [(entry.name, name) for entry in destination.list_remote()]
        except DestinationError:
            # One unreachable / misconfigured destination must not fail the list.
            return []
        except Exception:  # pragma: no cover - defensive: never 500 the list
            return []

    out: list[tuple[str, str]] = []
    for item in items:
        if not item.get("enabled", True):
            continue
        out.extend(await asyncio.to_thread(_list_one, item))
    return out


@router.get("/restore/backups/{backup_id}/preview", response_model=BackupPreviewResponse)
async def preview_restore_backup(
    backup_id: str,
    _: AuthPrincipal = Depends(require_system_admin),
    __: AsyncSession = Depends(get_admin_session),
) -> BackupPreviewResponse:
    """Preview a backup bundle's manifest + the per-tenant restore option.

    System-Admin only. Reads the LOCAL bundle's ``manifest.json`` and returns its
    artifacts + whether a SELECTIVE per-tenant restore is offered (it is, when the
    bundle captured the logical dump a per-tenant restore filters) plus the
    FK-ordered tenant-scoped table list (the blast radius). A bundle that is not
    present locally (remote-only, not yet downloaded) is a 404 — the preview reads
    the on-disk manifest only. Read-only: nothing is decrypted or restored.
    """
    from api_server.backup_restore import (
        BackupBundleError,
        load_local_bundle,
        tenant_scoped_tables,
    )
    from api_server.config import get_settings

    settings = get_settings()
    try:
        bundle = load_local_bundle(settings.backup_root, backup_id)
    except BackupBundleError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    per_tenant_available = bundle.has_database_dump
    return BackupPreviewResponse(
        backup_id=bundle.backup_id,
        encrypted=bundle.encrypted,
        created_at=bundle.created_at,
        status=bundle.status,
        total_size_bytes=bundle.total_size_bytes,
        artifacts=[
            BackupArtifactPreview(
                name=a.name, kind=a.kind, size_bytes=a.size_bytes, source=a.source
            )
            for a in bundle.artifacts
        ],
        per_tenant_available=per_tenant_available,
        tenant_scoped_tables=tenant_scoped_tables() if per_tenant_available else [],
    )


@router.post(
    "/restore", response_model=RestoreTriggerResponse, status_code=status.HTTP_202_ACCEPTED
)
async def trigger_restore(
    payload: RestoreTriggerRequest,
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
) -> RestoreTriggerResponse:
    """Trigger a restore (full or per-tenant) — System Admin, DOUBLE confirmation.

    Restore is DESTRUCTIVE, so this requires a double confirmation: the endpoint
    RE-DERIVES the expected ``confirm`` token server-side (the bundle id for a
    full restore; ``<tenant_id>@<backup_id>`` for a per-tenant one) and 422s on a
    mismatch BEFORE enqueueing anything — the token is never trusted blindly. A
    per-tenant restore additionally requires a tenant_id.

    Because a restore is LONG, it runs as a Celery BACKGROUND JOB
    (``workers.run_restore`` / ``workers.run_restore_per_tenant``) — it is NEVER
    run inline here. Returns 202 + the job id the UI polls. The trigger is audited
    (backup id + tenant + kind + actor — never a secret).
    """
    kind = "per_tenant" if payload.tenant_id else "full"

    # -- DOUBLE CONFIRMATION re-derived server-side (fail closed on a mismatch).
    if payload.tenant_id:
        expected = _per_tenant_confirm_token(payload.tenant_id, payload.backup_id)
    else:
        expected = _full_confirm_token(payload.backup_id)
    if payload.confirm != expected:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "restore confirmation token does not match the expected value; "
                "refusing to enqueue a destructive restore"
            ),
        )

    # Enqueue the background job (NEVER run inline). A broker failure surfaces as
    # a 502 — a restore the operator triggered must fail loudly, not silently.
    from api_server.celery_client import enqueue_restore

    try:
        job_id = await enqueue_restore(
            payload.backup_id,
            confirm=payload.confirm,
            tenant_id=payload.tenant_id,
        )
    except Exception as exc:  # broker unreachable
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"could not enqueue the restore job: {exc}",
        ) from exc

    await write_audit_log(
        session,
        action=_AUDIT_RESTORE_TRIGGERED,
        actor_user_id=principal.user_id,
        tenant_id=None,
        resource_type="backup_restore",
        resource_id=None,
        changes={
            "backup_id": payload.backup_id,
            "tenant_id": payload.tenant_id,
            "kind": kind,
            "job_id": job_id,
        },
    )
    return RestoreTriggerResponse(
        job_id=job_id,
        backup_id=payload.backup_id,
        tenant_id=payload.tenant_id,
        kind=kind,
    )


@router.get("/restore/jobs/{job_id}", response_model=RestoreJobStatus)
async def get_restore_job(
    job_id: str,
    _: AuthPrincipal = Depends(require_system_admin),
    __: AsyncSession = Depends(get_admin_session),
) -> RestoreJobStatus:
    """Poll a restore background job's status (System Admin only).

    Reads the Celery job's state from the result backend: ``PENDING`` /
    ``PROGRESS`` (with a ``{phase, message}`` progress meta) / ``SUCCESS`` (with
    the engine's result dict) / ``FAILURE`` (with a non-leaky error string). The
    UI polls this to render the progress/log view."""
    from api_server.celery_client import get_restore_job_status

    snapshot = await get_restore_job_status(job_id)
    return RestoreJobStatus(**snapshot)
