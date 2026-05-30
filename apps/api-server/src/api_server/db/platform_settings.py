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
