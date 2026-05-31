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


# ---------------------------------------------------------------------------
# Restore UI (Plan 12 Phase C — task_12_12)
# ---------------------------------------------------------------------------
# A System Admin restores from a backup bundle through the admin panel: pick a
# backup, PREVIEW its manifest (full restore) or the tenant-scoped tables (a
# per-tenant restore), then TRIGGER a restore. Restore is LONG + DESTRUCTIVE, so
# the trigger ENQUEUES a Celery background job (never runs inline) and the UI
# polls the job's status. The trigger requires a DOUBLE confirmation token (the
# bundle id for a full restore; ``<tenant_id>@<backup_id>`` for a per-tenant one)
# so a destructive restore can never fire from a single click.


class BackupListItem(BaseModel):
    """One backup available to restore — local on disk and/or at a remote.

    ``backup_id`` is the bundle id (the timestamped directory / file name). The
    manifest summary (``encrypted`` / ``created_at`` / ``total_size_bytes``) comes
    from a LOCAL bundle's ``manifest.json``; a backup present only at a remote
    destination has those fields ``None`` until it is downloaded. ``locations``
    lists where the bundle was found (``"local"`` + each destination name)."""

    model_config = _BASE_CONFIG

    backup_id: str
    encrypted: bool | None = None
    created_at: str | None = None
    total_size_bytes: int | None = None
    locations: list[str] = Field(default_factory=list)


class BackupListResponse(BaseModel):
    """The backups available to restore (local + remote), newest first."""

    model_config = _BASE_CONFIG

    backups: list[BackupListItem]


class BackupArtifactPreview(BaseModel):
    """One artifact in a bundle's manifest (preview pane)."""

    model_config = _BASE_CONFIG

    name: str
    kind: str
    size_bytes: int
    source: str | None = None


class BackupPreviewResponse(BaseModel):
    """The contents of a backup bundle's manifest, for the preview pane.

    ``per_tenant_available`` tells the UI whether a SELECTIVE per-tenant restore
    is offered for this bundle (it is, when the bundle captured the database dump
    a per-tenant restore filters); ``tenant_scoped_tables`` is the FK-ordered list
    of tables a per-tenant restore would touch, so the operator sees the blast
    radius before confirming."""

    model_config = _BASE_CONFIG

    backup_id: str
    encrypted: bool
    created_at: str | None = None
    status: str | None = None
    total_size_bytes: int
    artifacts: list[BackupArtifactPreview]
    per_tenant_available: bool
    tenant_scoped_tables: list[str] = Field(default_factory=list)


class RestoreTriggerRequest(BaseModel):
    """System Admin triggers a restore (full or per-tenant) — DESTRUCTIVE.

    A restore is destructive, so this requires a DOUBLE confirmation: ``confirm``
    MUST equal the engine's expected token, which the UI builds from the
    operator's second confirmation — the bundle id for a FULL restore, or
    ``<tenant_id>@<backup_id>`` for a per-tenant one. ``tenant_id`` is set ONLY
    for a per-tenant restore (a full restore leaves it ``None``). The endpoint
    re-derives the EXPECTED token server-side and 422s on a mismatch before
    enqueueing anything — the token is never trusted blindly."""

    model_config = _BASE_CONFIG

    backup_id: str = Field(min_length=1, max_length=128)
    tenant_id: str | None = Field(
        default=None,
        description="Set for a SELECTIVE per-tenant restore; None for a full restore.",
    )
    confirm: str = Field(
        min_length=1,
        description=(
            "The double-confirmation token: the bundle id for a full restore, "
            "or '<tenant_id>@<backup_id>' for a per-tenant restore."
        ),
    )


class RestoreTriggerResponse(BaseModel):
    """The enqueued restore job — the UI polls ``job_id`` for status."""

    model_config = _BASE_CONFIG

    job_id: str
    backup_id: str
    tenant_id: str | None = None
    # "full" | "per_tenant" — which kind of restore was enqueued.
    kind: str


class RestoreJobStatus(BaseModel):
    """A restore background job's pollable status (Plan 12 task_12_12).

    ``state`` is Celery's task state (``PENDING`` / ``PROGRESS`` / ``SUCCESS`` /
    ``FAILURE``). ``progress`` carries the ``{phase, message}`` the job reported
    while in-flight; ``result`` is the engine's result dict on success; ``error``
    is a non-leaky message on failure."""

    model_config = _BASE_CONFIG

    job_id: str
    state: str
    progress: dict[str, Any] | None = None
    result: dict[str, Any] | None = None
    error: str | None = None
