"""Ensure the platform tenant exists before any other seed runs.

The platform tenant is a regular row in `organizations` reserved for
catalog content (built-in agents, skills, tools, teams, project
templates). Tenant sessions never see this row -- it's hidden by the
`org_self_only` RLS policy on organizations -- but its rows ARE visible
through the per-table `<table>_builtin_read` / `agents_global_builtin_read`
policies that key off scope/is_builtin rather than tenant_id.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.seeds import (
    PLATFORM_TENANT_ID,
    PLATFORM_TENANT_NAME,
    PLATFORM_TENANT_SLUG,
)


async def ensure_platform_tenant(session: AsyncSession) -> None:
    """Insert the platform organization if absent. Idempotent.

    Caller must hold an AsyncSession bound to the BYPASSRLS admin
    engine -- a tenant session can't write to `organizations`.
    """
    await session.execute(
        text(
            """
            INSERT INTO organizations (id, name, slug, is_active)
            VALUES (:id, :name, :slug, true)
            ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
            """
        ),
        {
            "id": str(PLATFORM_TENANT_ID),
            "name": PLATFORM_TENANT_NAME,
            "slug": PLATFORM_TENANT_SLUG,
        },
    )
