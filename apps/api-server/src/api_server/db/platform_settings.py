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
