"""Repository for tenant-defined custom chat modes (Plan 03 task_03_08).

Thin wrapper that loads the active custom modes for a tenant in a
single round-trip and returns the {name -> CustomModeSpec} dict that
`api_server.chat.modes.resolve_mode_config` expects. The endpoint /
agent loop then doesn't need to know about the ORM shape.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.chat.modes import CustomModeSpec
from api_server.db.custom_chat_mode import CustomChatMode, row_to_spec


async def load_tenant_custom_modes(
    session: AsyncSession, tenant_id: UUID
) -> dict[str, CustomModeSpec]:
    """Return all live (non-deleted) custom modes the tenant owns,
    keyed by mode name. Empty dict if the tenant defines none.

    The tenant filter is duplicated even though RLS already does it —
    a session opened against the migrations user (BYPASSRLS) used by
    integration tests would otherwise mix rows from every tenant.
    """
    result = await session.execute(
        select(CustomChatMode).where(
            CustomChatMode.tenant_id == tenant_id,
            CustomChatMode.deleted_at.is_(None),
        )
    )
    return {row.name: row_to_spec(row) for row in result.scalars().all()}


__all__ = ["load_tenant_custom_modes"]
