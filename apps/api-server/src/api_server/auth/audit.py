"""Append-only audit log helper.

Called from sensitive flows (login, tenant CRUD, membership changes,
etc.). Writes go through the BYPASSRLS admin engine so System Admin
actions with tenant_id=NULL land cleanly past the audit_log RLS
policy.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import insert
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from api_server.db.models import AuditLog


async def write_audit_log(
    session: AsyncSession,
    *,
    action: str,
    actor_user_id: UUID | None,
    tenant_id: UUID | None = None,
    resource_type: str | None = None,
    resource_id: UUID | None = None,
    changes: dict[str, Any] | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> None:
    """Insert a row in audit_log. Must be called inside an open
    transaction on `session`."""
    await session.execute(
        insert(AuditLog).values(
            id=uuid7(),
            tenant_id=tenant_id,
            user_id=actor_user_id,
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            changes=changes,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    )
