"""Platform-wide settings — read by anyone, written only by a System Admin
(task_02_13).

`max_review_retries` is the canonical example (spec §7.9): a hard
platform limit on how many times an agent may rework its output. A
tenant cannot loosen it — `set_platform_setting` raises unless the actor
is a System Admin. When a setting has never been written, the read
helpers fall back to the platform default.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.models import PlatformSetting, User

# Setting keys.
MAX_REVIEW_RETRIES_KEY = "max_review_retries"

# Platform default for max_review_retries when the setting is unset.
# Kept in lockstep with agent_runtime.safeguards.DEFAULT_MAX_REVIEW_RETRIES
# (the two packages deliberately do not import one another).
DEFAULT_MAX_REVIEW_RETRIES = 3


class PlatformSettingForbiddenError(PermissionError):
    """Raised when a non-System-Admin attempts to write a platform setting."""


async def get_platform_setting(session: AsyncSession, key: str, *, default: Any = None) -> Any:
    """Read a platform setting; return `default` when it has never been set."""
    row = await session.get(PlatformSetting, key)
    return row.value if row is not None else default


async def set_platform_setting(
    session: AsyncSession,
    key: str,
    value: Any,
    *,
    actor: User,
) -> PlatformSetting:
    """Write a platform setting. Only a System Admin may do so.

    Raises `PlatformSettingForbiddenError` for any other actor — a Tenant
    Admin included. The caller owns the transaction (this flushes).
    """
    if not actor.is_system_admin:
        raise PlatformSettingForbiddenError(
            f"only a System Admin may set the platform setting '{key}'"
        )

    row = await session.get(PlatformSetting, key)
    if row is None:
        row = PlatformSetting(key=key, value=value, updated_by=actor.id)
        session.add(row)
    else:
        row.value = value
        row.updated_by = actor.id
    await session.flush()
    return row


async def get_max_review_retries(session: AsyncSession) -> int:
    """The effective max_review_retries — the platform override, or the
    default. This is what an execution's review-retry budget is built from."""
    value = await get_platform_setting(
        session, MAX_REVIEW_RETRIES_KEY, default=DEFAULT_MAX_REVIEW_RETRIES
    )
    return int(value)


# ---------------------------------------------------------------------------
# Plan approval — double-signature threshold (Plan 03 task_03_25)
# ---------------------------------------------------------------------------
PLAN_DOUBLE_SIGNATURE_THRESHOLD_KEY = "plan_approval_double_signature_threshold"
# Default 0 = single firma always. The operator raises this from the
# admin panel to force a second signer on expensive plans. Value is
# read as a Decimal in the currency of the plan's AI estimate.
DEFAULT_DOUBLE_SIGNATURE_THRESHOLD = "0"


# ---------------------------------------------------------------------------
# Execution time-limit backstop (Plan 06.14 task_06_14_04 / workers-orchestrator-10)
# ---------------------------------------------------------------------------
# Operator-tunable backstop applied per `run_execution` at enqueue time, so a
# change takes effect for new runs without restarting the workers. Generous on
# purpose — the agent-runtime enforces its own, tighter container_run_timeout_s;
# these only catch a truly wedged task. Soft → SoftTimeLimitExceeded the task
# can catch and finalise; hard → SIGKILL of the worker child.
EXECUTION_SOFT_TIME_LIMIT_KEY = "execution_soft_time_limit_s"
EXECUTION_HARD_TIME_LIMIT_KEY = "execution_hard_time_limit_s"
DEFAULT_EXECUTION_SOFT_TIME_LIMIT_S = 1800
DEFAULT_EXECUTION_HARD_TIME_LIMIT_S = 2100


async def get_execution_time_limits(session: AsyncSession) -> tuple[int, int]:
    """Return ``(soft_s, hard_s)`` for a dispatched ``run_execution``.

    Reads the operator overrides from platform settings, falling back to the
    defaults. Guarantees ``soft < hard`` (bumps hard if misconfigured) so
    Celery never rejects the limits."""
    soft = int(
        await get_platform_setting(
            session, EXECUTION_SOFT_TIME_LIMIT_KEY, default=DEFAULT_EXECUTION_SOFT_TIME_LIMIT_S
        )
    )
    hard = int(
        await get_platform_setting(
            session, EXECUTION_HARD_TIME_LIMIT_KEY, default=DEFAULT_EXECUTION_HARD_TIME_LIMIT_S
        )
    )
    if hard <= soft:
        hard = soft + 300
    return soft, hard


async def get_double_signature_threshold(session: AsyncSession) -> str:
    """Threshold (string-decimal) above which an AI cost estimate
    triggers the double-signature path. Returned as a string so the
    caller picks the right Decimal precision for its currency."""
    value = await get_platform_setting(
        session,
        PLAN_DOUBLE_SIGNATURE_THRESHOLD_KEY,
        default=DEFAULT_DOUBLE_SIGNATURE_THRESHOLD,
    )
    return str(value)


# ---------------------------------------------------------------------------
# Scheduled price-catalog sync (Plan 11 task_11_18)
# ---------------------------------------------------------------------------
# The price-sync beat job (workers.sync_model_prices) checks this flag at the
# top of every run, so a System Admin can turn the scheduled sync OFF (or back
# ON) from the admin panel and it takes effect on the next fire without
# restarting Celery beat. The CADENCE is a separate, operator-tunable knob
# (WORKERS_PRICE_SYNC_CRON read by the beat process at boot) — this flag is the
# live enable/disable lever. Default ON: keeping prices fresh is the desired
# behaviour, and an unconfirmed >10% spike is held for manual confirm anyway.
PRICE_SYNC_ENABLED_KEY = "price_sync_enabled"
DEFAULT_PRICE_SYNC_ENABLED = True


async def get_price_sync_enabled(session: AsyncSession) -> bool:
    """Whether the scheduled price-catalog sync is currently enabled.

    Read by the ``workers.sync_model_prices`` beat task before it does any
    work; when False the run is a no-op (it never fetches the feed or writes
    the catalog). A System Admin flips this from the admin panel — only a
    System Admin may write a platform setting (``set_platform_setting``).
    """
    value = await get_platform_setting(
        session, PRICE_SYNC_ENABLED_KEY, default=DEFAULT_PRICE_SYNC_ENABLED
    )
    return bool(value)


# ---------------------------------------------------------------------------
# Scheduled exchange-rates fetch (Plan 11.1 task_11_1_02)
# ---------------------------------------------------------------------------
# Live enable/disable lever for the daily FX-fetcher job
# (workers.fetch_exchange_rates). The beat task reads it at the top of every
# run; OFF makes the run a no-op (no feed fetch, no catalog write) without
# restarting Celery beat — mirroring the price-sync / backup enable levers. The
# CADENCE (cron) is a separate operator-tunable knob (WORKERS_FX_FETCH_CRON read
# by the beat process at boot), NOT this flag. Default ON: an unattended
# platform should keep its display-currency rates fresh (USD stays canonical).
FX_FETCH_ENABLED_KEY = "fx_fetch_enabled"
DEFAULT_FX_FETCH_ENABLED = True

# The FX rate SOURCE the fetcher uses, selectable by a System Admin from the
# admin panel (Plan 11.1 decision: "fuente por defecto ECB, configurable por
# System Admin"). Read live by the FX-fetcher beat task so a change takes effect
# on the next fire without a restart. Only ECB is wired today; the key is a
# free-form string so a future source (e.g. a paid feed) needs no schema change,
# and an unknown value falls back to ECB rather than crashing the run.
FX_SOURCE_KEY = "fx_source"
DEFAULT_FX_SOURCE = "ecb"
# The FX sources the fetcher knows how to fetch. Kept in lockstep with
# workers.fx_fetcher.FX_FETCHER_SOURCES (the two packages deliberately do not
# import one another at module load).
FX_SOURCES = ("ecb",)


async def get_fx_fetch_enabled(session: AsyncSession) -> bool:
    """Whether the scheduled exchange-rates fetch is currently enabled.

    Read by the ``workers.fetch_exchange_rates`` beat task before it does any
    work; when False the run is a no-op (it never fetches the feed or writes the
    catalog). A System Admin flips this from the admin panel — only a System
    Admin may write a platform setting (``set_platform_setting``).
    """
    value = await get_platform_setting(
        session, FX_FETCH_ENABLED_KEY, default=DEFAULT_FX_FETCH_ENABLED
    )
    return bool(value)


async def get_fx_source(session: AsyncSession) -> str:
    """The configured FX rate source (default ECB).

    Read live by the FX-fetcher beat task so a System-Admin source change from
    the admin panel takes effect on the next run. An unknown / unset value
    falls back to the default ECB source (the fetcher never crashes on a typo).
    """
    value = await get_platform_setting(session, FX_SOURCE_KEY, default=DEFAULT_FX_SOURCE)
    source = str(value).strip().lower()
    return source if source in FX_SOURCES else DEFAULT_FX_SOURCE


# ---------------------------------------------------------------------------
# Budget alert thresholds (Plan 11.1 task_11_1_04)
# ---------------------------------------------------------------------------
# The percentages-of-budget at which the consumption evaluator (task_11_1_05)
# fires an alert via the Plan 10 notifier. PLATFORM-GLOBAL + configurable by a
# System Admin (Plan 11.1 decision): the same thresholds apply to every tenant
# and project, and a tenant cannot loosen them. Default [80, 90, 100]: warn at
# 80% and 90%, then 100% is the auto-pause trigger (task_11_1_06). Stored as a
# JSON array of ints. The 100% entry is what arms the auto-pause, so it is
# always present in the effective list even if a System Admin drops it.
BUDGET_ALERT_THRESHOLDS_KEY = "budget_alert_thresholds"
DEFAULT_BUDGET_ALERT_THRESHOLDS: tuple[int, ...] = (80, 90, 100)

# A threshold is a percentage of the budget. Below 1% is meaningless noise; we
# allow above 100% (an over-budget escalation alert is legitimate).
_BUDGET_THRESHOLD_MIN = 1
_BUDGET_THRESHOLD_MAX = 1000
# The 100% mark always stays in the effective list — it is the auto-pause arm.
_BUDGET_PAUSE_THRESHOLD = 100


class InvalidBudgetThresholdsError(ValueError):
    """Raised when a proposed budget-alert-threshold list fails validation
    (empty, a non-int entry, or a value outside the [1, 1000] range)."""


def validate_budget_alert_thresholds(values: list[int]) -> list[int]:
    """Validate + normalise a budget-alert-threshold list.

    Returns the de-duplicated, ascending list with the mandatory 100% pause
    arm guaranteed present. Raises :class:`InvalidBudgetThresholdsError` for an
    empty list, a non-int (``bool`` is rejected too), or an out-of-range value.
    """
    if not values:
        raise InvalidBudgetThresholdsError("at least one alert threshold is required")
    cleaned: set[int] = set()
    for v in values:
        # bool is an int subclass — reject it so True/False can't sneak in.
        if isinstance(v, bool) or not isinstance(v, int):
            raise InvalidBudgetThresholdsError(f"threshold {v!r} must be an integer")
        if v < _BUDGET_THRESHOLD_MIN or v > _BUDGET_THRESHOLD_MAX:
            raise InvalidBudgetThresholdsError(
                f"threshold {v} must be between "
                f"{_BUDGET_THRESHOLD_MIN} and {_BUDGET_THRESHOLD_MAX}"
            )
        cleaned.add(v)
    cleaned.add(_BUDGET_PAUSE_THRESHOLD)  # auto-pause arm always present
    return sorted(cleaned)


async def get_budget_alert_thresholds(session: AsyncSession) -> list[int]:
    """The effective budget-alert thresholds (ascending ints).

    Reads the System-Admin override, falling back to the platform default
    ``[80, 90, 100]``. Always normalised + guaranteed to include the 100% pause
    arm; a stored value that somehow fails validation falls back to the default
    rather than crashing the evaluator."""
    value = await get_platform_setting(
        session,
        BUDGET_ALERT_THRESHOLDS_KEY,
        default=list(DEFAULT_BUDGET_ALERT_THRESHOLDS),
    )
    try:
        return validate_budget_alert_thresholds(list(value))
    except (InvalidBudgetThresholdsError, TypeError):
        return list(DEFAULT_BUDGET_ALERT_THRESHOLDS)


async def set_budget_alert_thresholds(
    session: AsyncSession,
    values: list[int],
    *,
    actor: User,
) -> list[int]:
    """Persist the budget-alert thresholds (System Admin only).

    Validates the list FIRST (raising :class:`InvalidBudgetThresholdsError`
    before any write); ``set_platform_setting`` re-checks the actor is a System
    Admin. Returns the normalised list actually stored."""
    normalised = validate_budget_alert_thresholds(values)
    await set_platform_setting(session, BUDGET_ALERT_THRESHOLDS_KEY, normalised, actor=actor)
    return normalised


# ---------------------------------------------------------------------------
# Scheduled credential rotation (Plan 15 task_15_17)
# ---------------------------------------------------------------------------
# Live enable/disable lever for the periodic Vault credential-rotation job
# (workers.rotate_credentials). The beat task reads it at the top of every run;
# OFF makes the run a no-op (no Vault writes, no lease churn) without restarting
# Celery beat — mirroring the price-sync / backup enable levers. The CADENCE
# (cron) is a separate operator-tunable knob (WORKERS_CRED_ROTATION_CRON read by
# the beat process at boot), NOT this flag. Default ON: an unattended production
# platform should keep its static secrets fresh + its dynamic leases short-lived.
CRED_ROTATION_ENABLED_KEY = "cred_rotation_enabled"
DEFAULT_CRED_ROTATION_ENABLED = True


async def get_cred_rotation_enabled(session: AsyncSession) -> bool:
    """Whether the scheduled credential-rotation job is currently enabled.

    Read by the ``workers.rotate_credentials`` beat task before it does any
    work; when False the run is a no-op (no Vault writes, no lease renewal). A
    System Admin flips this from the admin panel — only a System Admin may write
    a platform setting (``set_platform_setting``).
    """
    value = await get_platform_setting(
        session, CRED_ROTATION_ENABLED_KEY, default=DEFAULT_CRED_ROTATION_ENABLED
    )
    return bool(value)


# ---------------------------------------------------------------------------
# Scheduled backup (Plan 12 task_12_01 / task_12_04)
# ---------------------------------------------------------------------------
# Live enable/disable lever for the daily backup job, flipped by a System Admin
# from the admin panel (task_12_04). The beat task reads it at the top of every
# run; OFF makes the run a no-op without restarting Celery beat. Default ON —
# an unattended platform should be backing itself up. The CADENCE (cron) and
# the time WINDOW are separate operator-tunable knobs (`backup_cron` setting /
# WORKERS_* envs), NOT this flag.
BACKUP_ENABLED_KEY = "backup_enabled"
DEFAULT_BACKUP_ENABLED = True

# Operator-tunable cron for the daily backup, read by the beat process at boot
# (mirrors price_sync) AND re-read live by the backup beat task. Default daily
# at 03:00 (Plan 12: "Backup automático diario 03:00"). Stored as a 5-field
# cron string.
BACKUP_CRON_KEY = "backup_cron"
DEFAULT_BACKUP_CRON = "0 3 * * *"

# Local retention window in days (Plan 12: "Retención local 7 días"). Stored
# as a platform setting so a System Admin tunes it from the panel (task_12_04)
# rather than only via the WORKERS_BACKUP_RETENTION_DAYS env. The backup beat
# task reads it live so a change takes effect on the next run without a restart.
# Kept in lockstep with workers.config.Settings.backup_retention_days's default.
BACKUP_RETENTION_DAYS_KEY = "backup_retention_days"
DEFAULT_BACKUP_RETENTION_DAYS = 7

# Validation bounds for the retention window — never a magic literal scattered
# across the codebase. At least one day (a 0-day window would prune the bundle
# we just wrote); a generous upper bound keeps a typo from filling the disk.
BACKUP_RETENTION_DAYS_MIN = 1
BACKUP_RETENTION_DAYS_MAX = 3650


class InvalidBackupScheduleError(ValueError):
    """Raised when a proposed backup schedule fails validation (bad cron
    expression or an out-of-range retention window)."""


def validate_backup_cron(expr: str) -> str:
    """Validate a 5-field cron expression for the backup schedule.

    Returns the normalised (whitespace-collapsed) expression on success.
    Raises :class:`InvalidBackupScheduleError` for a non-5-field string or a
    field Celery's ``crontab`` parser rejects. We delegate the field-syntax
    check to ``celery.schedules.crontab`` — the SAME parser the beat process
    uses (``workers.beat_schedule._parse_cron``) — so a value the API accepts
    is one beat can actually schedule, never a "valid here / rejected there"
    mismatch.
    """
    parts = expr.split()
    if len(parts) != 5:
        raise InvalidBackupScheduleError(
            "cron must have exactly 5 fields: 'minute hour day-of-month month day-of-week'"
        )
    minute, hour, dom, month, dow = parts
    try:
        # Importing here keeps celery out of the api-server import graph for
        # callers that never touch the backup schedule.
        from celery.schedules import crontab

        crontab(
            minute=minute,
            hour=hour,
            day_of_month=dom,
            month_of_year=month,
            day_of_week=dow,
        )
    except Exception as exc:  # celery raises ValueError/KeyError on bad fields
        raise InvalidBackupScheduleError(f"invalid cron expression: {expr!r}") from exc
    return " ".join(parts)


def validate_backup_retention_days(value: int) -> int:
    """Validate the retention window is within [MIN, MAX]. Returns it on
    success; raises :class:`InvalidBackupScheduleError` otherwise."""
    if value < BACKUP_RETENTION_DAYS_MIN or value > BACKUP_RETENTION_DAYS_MAX:
        raise InvalidBackupScheduleError(
            f"retention_days must be between {BACKUP_RETENTION_DAYS_MIN} "
            f"and {BACKUP_RETENTION_DAYS_MAX}"
        )
    return value


async def get_backup_enabled(session: AsyncSession) -> bool:
    """Whether the scheduled daily backup is currently enabled.

    Read by the backup beat task before it does any work; when False the run
    is a no-op (no pg_dump, no tar, no disk writes). Only a System Admin may
    flip it (``set_platform_setting``).
    """
    value = await get_platform_setting(session, BACKUP_ENABLED_KEY, default=DEFAULT_BACKUP_ENABLED)
    return bool(value)


async def get_backup_cron(session: AsyncSession) -> str:
    """The configured backup cron (5-field string). Falls back to the default
    daily-03:00 schedule when unset. The beat process reads this at boot to
    build its schedule; the beat TASK also re-reads it live for its log/summary."""
    value = await get_platform_setting(session, BACKUP_CRON_KEY, default=DEFAULT_BACKUP_CRON)
    return str(value)


async def get_backup_retention_days(session: AsyncSession) -> int:
    """The configured local retention window in days. Falls back to the
    platform default when unset. The backup beat task reads this live so a
    change a System Admin makes from the panel takes effect on the next run
    (it overrides the WORKERS_BACKUP_RETENTION_DAYS env default)."""
    value = await get_platform_setting(
        session, BACKUP_RETENTION_DAYS_KEY, default=DEFAULT_BACKUP_RETENTION_DAYS
    )
    return int(value)


# ---------------------------------------------------------------------------
# Remote backup destinations (Plan 12 Phase B — task_12_09)
# ---------------------------------------------------------------------------
# After a successful, verified backup the bundle is uploaded to each configured
# + enabled remote destination (Plan 12: "destinos remotos opcionales (S3, B2,
# SFTP/NAS, rclone)"). A System Admin manages the list from the admin panel.
#
# What is stored here is the NON-secret config ONLY: a list of
#   {"type": "s3"|"b2"|"sftp"|"rclone", "name", "enabled", "config": {<knobs>}}
# dicts under one platform_settings key. The CREDENTIALS (S3 access key/secret,
# B2 keyId/key, SFTP password/private key, the rclone config blob) are NEVER
# stored here — they live in the workers' secret seam (Vault/env), keyed by each
# adapter's well-known field names. We reject any secret-looking field landing in
# the config so a credential can never be persisted (or echoed back) by accident.
BACKUP_DESTINATIONS_KEY = "backup_destinations"

# The destination types the platform supports. Kept in lockstep with
# workers.backup_destinations.DESTINATION_TYPES (the two packages deliberately
# do not import one another at module load).
BACKUP_DESTINATION_TYPES = ("s3", "b2", "sftp", "rclone")

# The NON-secret config field each destination type requires + the optional ones
# it accepts. Anything outside (required + optional) for a type is rejected — a
# guardrail that also blocks a secret field (access_key, password, ...) from ever
# being stored, because none of them appear in these allow-lists.
_DEST_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "s3": ("bucket",),
    "b2": ("bucket", "region"),
    "sftp": ("host", "username"),
    "rclone": ("remote",),
}
_DEST_OPTIONAL_FIELDS: dict[str, tuple[str, ...]] = {
    "s3": ("prefix", "endpoint_url", "region"),
    "b2": ("prefix",),
    "sftp": ("port", "remote_path", "host_key_policy", "known_hosts_path"),
    "rclone": ("path",),
}

# A destination name must be a short, stable identifier (logs/manifest key).
_DEST_NAME_MAX_LEN = 64
# Cap the number of configured destinations so a runaway client cannot bloat the
# single JSONB row.
_DEST_MAX_COUNT = 25


class InvalidBackupDestinationError(ValueError):
    """Raised when a proposed backup-destination config fails validation
    (unknown type, missing required field, an unexpected/secret-looking field,
    or a duplicate name)."""


def validate_backup_destinations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Validate + normalise a list of NON-secret destination configs.

    Each item must be ``{"type", "name", "enabled", "config": {...}}`` where
    ``config`` carries ONLY the type's known non-secret knobs. Returns the
    normalised list (stable key order, names trimmed) on success; raises
    :class:`InvalidBackupDestinationError` on any problem WITHOUT persisting.

    The unknown-field rejection is the credential guardrail: every adapter's
    secret field name (``backup_s3_access_key_id``, ``backup_sftp_password``,
    the rclone config blob, …) is outside the per-type allow-list, so a client
    that tries to smuggle a credential into ``config`` is a clean 422 — a secret
    can never reach this table.
    """
    if len(items) > _DEST_MAX_COUNT:
        raise InvalidBackupDestinationError(f"too many destinations (max {_DEST_MAX_COUNT})")
    normalised: list[dict[str, Any]] = []
    seen_names: set[str] = set()
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise InvalidBackupDestinationError(f"destination #{idx} must be an object")
        dest_type = str(item.get("type", "")).strip().lower()
        if dest_type not in BACKUP_DESTINATION_TYPES:
            raise InvalidBackupDestinationError(
                f"destination #{idx} has unknown type {dest_type!r}; "
                f"must be one of {BACKUP_DESTINATION_TYPES}"
            )
        name = str(item.get("name", "")).strip()
        if not name or len(name) > _DEST_NAME_MAX_LEN:
            raise InvalidBackupDestinationError(
                f"destination #{idx} requires a name (1..{_DEST_NAME_MAX_LEN} chars)"
            )
        if name in seen_names:
            raise InvalidBackupDestinationError(f"duplicate destination name {name!r}")
        seen_names.add(name)

        raw_config = item.get("config", {})
        if not isinstance(raw_config, dict):
            raise InvalidBackupDestinationError(f"destination {name!r} config must be an object")
        allowed = set(_DEST_REQUIRED_FIELDS[dest_type]) | set(_DEST_OPTIONAL_FIELDS[dest_type])
        config: dict[str, Any] = {}
        for key, value in raw_config.items():
            if key not in allowed:
                # Outside the non-secret allow-list — either a typo or an
                # attempt to store a credential. Reject either way.
                raise InvalidBackupDestinationError(
                    f"destination {name!r}: field {key!r} is not allowed for "
                    f"type {dest_type!r} (secrets are never stored here)"
                )
            config[key] = value
        for required in _DEST_REQUIRED_FIELDS[dest_type]:
            rv = config.get(required)
            if rv is None or (isinstance(rv, str) and not rv.strip()):
                raise InvalidBackupDestinationError(
                    f"destination {name!r} of type {dest_type!r} is missing "
                    f"required field {required!r}"
                )
        normalised.append(
            {
                "type": dest_type,
                "name": name,
                "enabled": bool(item.get("enabled", True)),
                "config": config,
            }
        )
    return normalised


async def get_backup_destinations(session: AsyncSession) -> list[dict[str, Any]]:
    """The configured remote backup destinations (NON-secret config only).

    Returns the stored list, or ``[]`` when none have been configured. Each item
    is ``{"type", "name", "enabled", "config": {...}}`` — never a credential."""
    value = await get_platform_setting(session, BACKUP_DESTINATIONS_KEY, default=[])
    return list(value) if isinstance(value, list) else []


async def set_backup_destinations(
    session: AsyncSession,
    items: list[dict[str, Any]],
    *,
    actor: User,
) -> list[dict[str, Any]]:
    """Persist the full destination list (System Admin only).

    Validates EVERY item first (raising :class:`InvalidBackupDestinationError`
    before any write); ``set_platform_setting`` re-checks the actor is a System
    Admin. Returns the normalised list. CREDENTIALS are never part of the stored
    config (validation rejects any secret-looking field), so this write — and the
    read-back — can never echo a secret."""
    normalised = validate_backup_destinations(items)
    await set_platform_setting(session, BACKUP_DESTINATIONS_KEY, normalised, actor=actor)
    return normalised


async def get_backup_schedule(session: AsyncSession) -> tuple[bool, str, int]:
    """Return the full backup schedule as ``(enabled, cron, retention_days)``.

    The single read the get-schedule endpoint and the beat task both use, so
    the API surface and the scheduled run agree on the same stored config."""
    return (
        await get_backup_enabled(session),
        await get_backup_cron(session),
        await get_backup_retention_days(session),
    )


async def set_backup_schedule(
    session: AsyncSession,
    *,
    enabled: bool,
    cron: str,
    retention_days: int,
    actor: User,
) -> tuple[bool, str, int]:
    """Persist the full backup schedule (System Admin only).

    Validates the cron + retention window FIRST (raising
    :class:`InvalidBackupScheduleError` on a bad value, before any write), then
    writes all three settings on the actor's session. ``set_platform_setting``
    re-checks the actor is a System Admin, so a non-admin never reaches the
    write. Returns the normalised ``(enabled, cron, retention_days)``."""
    normalised_cron = validate_backup_cron(cron)
    validated_retention = validate_backup_retention_days(retention_days)

    await set_platform_setting(session, BACKUP_ENABLED_KEY, bool(enabled), actor=actor)
    await set_platform_setting(session, BACKUP_CRON_KEY, normalised_cron, actor=actor)
    await set_platform_setting(session, BACKUP_RETENTION_DAYS_KEY, validated_retention, actor=actor)
    return bool(enabled), normalised_cron, validated_retention
