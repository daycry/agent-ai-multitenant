"""Integration test — prod-18 task_prod18_design_01.

Migration 0099 adds `projects.slug` + `plans.slug` and backfills existing rows with
a SQL kebab slugify of name/title (ADR 0085). Verifies: existing rows are backfilled
(never NULL/empty) and the migration is reversible (up→down→up).

Sync test functions: ``alembic.command.*`` runs its own ``asyncio.run`` internally,
so the test cannot itself be inside an event loop — asyncpg work is wrapped in
``asyncio.run`` sequentially.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

_PREV = "0098_project_execution_budgets"
_HEAD = "0099_project_plan_slug"


def test_backfill_populates_existing_rows(alembic_config: object, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, _PREV)
    project = uuid4()
    plan = uuid4()

    async def _seed() -> None:
        tenant = uuid4()
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute("TRUNCATE plans, projects, organizations RESTART IDENTITY CASCADE")
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T', 't-slug')", tenant
            )
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name, status)"
                " VALUES ($1, $2, 'Api CI!!', 'active')",
                project,
                tenant,
            )
            await conn.execute(
                "INSERT INTO plans (id, tenant_id, project_id, title, status)"
                " VALUES ($1, $2, $3, 'Plan v1.2 (final)', 'pending_approval')",
                plan,
                tenant,
                project,
            )
        finally:
            await conn.close()

    asyncio.run(_seed())

    command.upgrade(alembic_config, _HEAD)

    async def _read() -> tuple[str, str]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            p = await conn.fetchval("SELECT slug FROM projects WHERE id = $1", project)
            pl = await conn.fetchval("SELECT slug FROM plans WHERE id = $1", plan)
            return p, pl
        finally:
            await conn.close()

    proj_slug, plan_slug = asyncio.run(_read())
    assert proj_slug == "api-ci"
    assert plan_slug == "plan-v1-2-final"


def test_migration_is_reversible(alembic_config: object, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, _HEAD)
    command.downgrade(alembic_config, _PREV)

    async def _slug_cols() -> list:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            return await conn.fetch(
                "SELECT column_name FROM information_schema.columns"
                " WHERE table_name IN ('projects', 'plans') AND column_name = 'slug'"
            )
        finally:
            await conn.close()

    assert asyncio.run(_slug_cols()) == []
    command.upgrade(alembic_config, _HEAD)
