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
    InvalidBackupScheduleError,
    get_backup_schedule,
    set_backup_schedule,
)
from api_server.schemas.backup import BackupScheduleResponse, BackupScheduleUpdate

router = APIRouter(prefix="/admin/backup", tags=["admin", "backup"])

_AUDIT_SCHEDULE_UPDATED = "backup.schedule_updated"


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
