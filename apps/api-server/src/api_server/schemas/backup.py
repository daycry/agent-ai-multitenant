"""Schemas for the configurable backup schedule (Plan 12 task_12_04).

The backup schedule (cron cadence + a live enable flag + the local retention
window) is a PLATFORM setting a System Admin owns from the admin panel — never
a hardcoded cron. These schemas shape the ``/admin/backup/schedule`` payloads;
the router does the RBAC gating and ``db.platform_settings`` does the cron +
retention validation (so the API accepts exactly what Celery beat can schedule).
"""

from __future__ import annotations

from typing import Any, Literal

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


# ---------------------------------------------------------------------------
# Remote backup destinations (Plan 12 Phase B — task_12_09)
# ---------------------------------------------------------------------------
# A System Admin manages the list of remote destinations (S3, B2, SFTP/NAS,
# rclone) the backup bundle is uploaded to after a successful, verified backup.
#
# Secrets are WRITE-ONLY and NEVER echoed: the request payloads carry only the
# NON-secret config (bucket, endpoint, host, path, remote). Credentials live in
# the workers' secret seam (Vault/env) — they are NOT part of any schema here, so
# a destination can never be created with, nor read back exposing, a credential.

# The destination types the API accepts (Plan 12: "S3, B2, SFTP/NAS, rclone").
BackupDestinationType = Literal["s3", "b2", "sftp", "rclone"]


class BackupDestination(BaseModel):
    """One configured remote destination (NON-secret config only).

    ``config`` carries only the type's non-secret knobs (bucket/endpoint/region
    for S3-family, host/port/path for SFTP, remote/path for rclone). The server
    validates that ``config`` holds ONLY allowed non-secret fields — a credential
    can never be stored here, so this is the shape read back to the UI verbatim
    without ever exposing a secret.
    """

    model_config = _BASE_CONFIG

    type: BackupDestinationType
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)


class BackupDestinationsResponse(BaseModel):
    """The full list of configured destinations (NON-secret config only)."""

    model_config = _BASE_CONFIG

    destinations: list[BackupDestination]


class BackupDestinationsUpdate(BaseModel):
    """System Admin replaces the full destination list.

    A whole-list PUT (not per-item PATCH) keeps the contract simple: the client
    sends the desired set, the server validates EVERY item (unknown type, missing
    required field, or any secret-looking field -> 422 with nothing persisted).
    """

    model_config = _BASE_CONFIG

    destinations: list[BackupDestination] = Field(
        default_factory=list,
        max_length=25,
        description="The full set of remote backup destinations (non-secret config only).",
    )


class BackupConnectivityResult(BaseModel):
    """Result of the adapter's ``test_connectivity`` probe for one destination.

    ``ok`` is the headline the admin UI renders; ``detail`` is a short, non-leaky
    human string (the adapter guarantees it never carries a credential)."""

    model_config = _BASE_CONFIG

    ok: bool
    detail: str = ""
