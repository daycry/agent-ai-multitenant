"""`/admin/backup/schedule` — the configurable backup schedule (Plan 12 task_12_04).

A System Admin configures the daily backup cadence (cron), the live enable
flag, and the local retention window from the admin panel — NOT a hardcoded
cron. The three values live in ``platform_settings`` (``backup_enabled`` /
``backup_cron`` / ``backup_retention_days``); the backup beat task
(``workers.run_daily_backup``) reads them live so a change takes effect on the
next run without restarting Celery.

Read/write split (mirrors the notifications platform-settings pattern):

  - ``GET  /admin/backup/schedule``   read the schedule (any authenticated
    member — the panel renders the current values; no secret is involved).
  - ``PUT  /admin/backup/schedule``   set it (System Admin only, BYPASSRLS
    ``get_admin_session``; ``set_platform_setting`` re-checks the actor is a
    System Admin so a Tenant Admin can never reach the write).

The cron + retention window are validated server-side
(``db.platform_settings.validate_*``); an invalid value is a clean 422 with no
write. Each successful write is audited (``backup.schedule_updated``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.audit import write_audit_log
from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    get_tenant_session,
    require_system_admin,
    require_tenant_member,
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
    BackupConnectivityResult,
    BackupDestination,
    BackupDestinationsResponse,
    BackupDestinationsUpdate,
    BackupScheduleResponse,
    BackupScheduleUpdate,
)

router = APIRouter(prefix="/admin/backup", tags=["admin", "backup"])

_AUDIT_SCHEDULE_UPDATED = "backup.schedule_updated"
_AUDIT_DESTINATIONS_UPDATED = "backup.destinations_updated"
_AUDIT_CONNECTIVITY_TESTED = "backup.destination_connectivity_tested"


@router.get("/schedule", response_model=BackupScheduleResponse)
async def read_backup_schedule(
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> BackupScheduleResponse:
    """The current backup schedule (enabled + cron + retention_days).

    Readable by any authenticated member so the admin panel can render the
    current values; ``platform_settings`` carries no RLS, so this is a plain
    global read. When unset, the platform defaults apply (enabled, daily 03:00,
    7-day retention).
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> BackupDestinationsResponse:
    """The configured remote backup destinations (NON-secret config only).

    Readable by any authenticated member so the panel can render the list;
    ``platform_settings`` carries no RLS, so this is a plain global read. The
    stored config never holds a credential, so this read can never expose one."""
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
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
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
        result = destination.test_connectivity()
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
