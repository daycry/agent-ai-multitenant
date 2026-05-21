"""Integration tests for the built-in project-templates seed (task_01_13)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

EXPECTED_TEMPLATE_NAMES = {
    "Plantilla: API REST",
    "Plantilla: Webapp Full-Stack",
    "Plantilla: Data Pipeline",
    "Plantilla: Migración Legacy",
    "Plantilla: Investigación + Especificación",
    "Plantilla: DevOps Bootstrap",
    "Plantilla: Suite E2E",
    "Plantilla: Modernización de Documentación",
}


async def _run_full_seed(dsn: str) -> int:
    """Seeds agents -> teams -> project templates (FK chain)."""
    from api_server.seeds.builtin_agents import seed_builtin_agents
    from api_server.seeds.builtin_project_templates import (
        seed_builtin_project_templates,
    )
    from api_server.seeds.builtin_teams import seed_builtin_teams
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            await seed_builtin_agents(session)
            await seed_builtin_teams(session)
            return await seed_builtin_project_templates(session)
    finally:
        await engine.dispose()


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE projects, team_members, teams, agents, organizations CASCADE")
    finally:
        await conn.close()


def _as_async_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn.replace("postgres://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_seed_creates_eight_project_templates(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    n = asyncio.run(_run_full_seed(_as_async_dsn(migrations_pg_dsn)))
    assert n == 8

    async def _names() -> set[str]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            rows = await conn.fetch("SELECT name FROM projects WHERE is_template = true")
            return {r[0] for r in rows}
        finally:
            await conn.close()

    assert asyncio.run(_names()) == EXPECTED_TEMPLATE_NAMES


def test_every_template_points_to_a_builtin_team(alembic_config, migrations_pg_dsn: str) -> None:
    """team_id FK -> teams.id, and every referenced team is a built-in."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_full_seed(_as_async_dsn(migrations_pg_dsn)))

    async def _check() -> int:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            row = await conn.fetchrow(
                """
                SELECT count(*)
                  FROM projects p
                  JOIN teams t ON t.id = p.team_id
                 WHERE p.is_template = true
                   AND t.is_builtin = true
                """
            )
            return int(row[0])
        finally:
            await conn.close()

    assert asyncio.run(_check()) == 8


def test_seed_is_idempotent(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    sa_dsn = _as_async_dsn(migrations_pg_dsn)
    n1 = asyncio.run(_run_full_seed(sa_dsn))
    n2 = asyncio.run(_run_full_seed(sa_dsn))
    assert n1 == n2 == 8

    async def _count() -> int:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            return int(
                await conn.fetchval("SELECT count(*) FROM projects WHERE is_template = true")
            )
        finally:
            await conn.close()

    assert asyncio.run(_count()) == 8


def test_templates_visible_to_tenant_sessions(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_full_seed(_as_async_dsn(migrations_pg_dsn)))

    from tests.integration.conftest import (
        PG_APP_PASSWORD,
        PG_APP_USER,
        PG_HOST,
        PG_PORT,
        PG_TEST_DB,
    )

    tenant_id = uuid4()
    app_dsn = f"postgresql://{PG_APP_USER}:{PG_APP_PASSWORD}" f"@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"

    async def _seed_tenant_and_count() -> int:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            await conn.execute(
                "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)"
                " ON CONFLICT DO NOTHING",
                tenant_id,
                "T",
                "t",
            )
        finally:
            await conn.close()

        conn = await asyncpg.connect(app_dsn)
        try:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(tenant_id),
                )
                return int(
                    await conn.fetchval("SELECT count(*) FROM projects WHERE is_template = true")
                )
        finally:
            await conn.close()

    assert asyncio.run(_seed_tenant_and_count()) == 8
