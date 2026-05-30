"""Schemas for the configurable backup schedule (Plan 12 task_12_04).

The backup schedule (cron cadence + a live enable flag + the local retention
window) is a PLATFORM setting a System Admin owns from the admin panel — never
a hardcoded cron. These schemas shape the ``/admin/backup/schedule`` payloads;
the router does the RBAC gating and ``db.platform_settings`` does the cron +
retention validation (so the API accepts exactly what Celery beat can schedule).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

_BASE_CONFIG = ConfigDict(extra="forbid")


class BackupScheduleResponse(BaseModel):
    """The current backup schedule the System Admin configured.

    ``cron`` is a 5-field expression (minute hour day-of-month month
    day-of-week); ``enabled`` is the live OFF switch; ``retention_days`` is the
    local rotation window in days.
    """

    model_config = _BASE_CONFIG

    enabled: bool
    cron: str
    retention_days: int


class BackupScheduleUpdate(BaseModel):
    """System Admin sets the backup schedule.

    The cron string + retention window are validated server-side (the cron via
    the same ``celery.schedules.crontab`` parser the beat process uses) so a
    value the API accepts is one beat can actually schedule. ``retention_days``
    is bounded so a typo cannot wipe the bundle just written nor fill the disk.
    """

    model_config = _BASE_CONFIG

    enabled: bool = Field(
        description="Live enable/disable for the scheduled daily backup.",
    )
    cron: str = Field(
        min_length=1,
        description="5-field cron expression: 'minute hour day-of-month month day-of-week'.",
    )
    retention_days: int = Field(
        ge=1,
        le=3650,
        description="Local retention window in days; bundles older than this are pruned.",
    )
