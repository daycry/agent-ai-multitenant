"""Integration tests for the built-in teams seed (task_01_12)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

EXPECTED_TEAM_NAMES = {
    "Equipo Full-Stack Web",
    "Equipo Backend / API",
    "Equipo Research & Spec",
    "Equipo DevOps & Platform",
    "Equipo Data",
}


async def _run_full_seed(dsn: str) -> tuple[int, int]:
    """Seeds agents first (FK target) then teams. Returns (agents, teams)."""
    from api_server.seeds.builtin_agents import seed_builtin_agents
    from api_server.seeds.builtin_teams import seed_builtin_teams
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            n_agents = await seed_builtin_agents(session)
            n_teams = await seed_builtin_teams(session)
        return n_agents, n_teams
    finally:
        await engine.dispose()


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE team_members, teams, agents, organizations CASCADE")
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
def test_seed_creates_five_builtin_teams(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    n_agents, n_teams = asyncio.run(_run_full_seed(_as_async_dsn(migrations_pg_dsn)))
    assert n_agents == 11
    assert n_teams == 5

    async def _fetch_names() -> set[str]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            rows = await conn.fetch("SELECT name FROM teams WHERE is_builtin = true")
            return {r[0] for r in rows}
        finally:
            await conn.close()

    assert asyncio.run(_fetch_names()) == EXPECTED_TEAM_NAMES


def test_seed_is_idempotent_and_member_counts_stable(
    alembic_config, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    sa_dsn = _as_async_dsn(migrations_pg_dsn)
    asyncio.run(_run_full_seed(sa_dsn))
    asyncio.run(_run_full_seed(sa_dsn))

    async def _fetch_counts() -> tuple[int, int]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            n_teams = int(await conn.fetchval("SELECT count(*) FROM teams WHERE is_builtin = true"))
            n_members = int(
                await conn.fetchval("""
                    SELECT count(*) FROM team_members tm
                      JOIN teams t ON tm.team_id = t.id
                     WHERE t.is_builtin = true
                    """)
            )
            return n_teams, n_members
        finally:
            await conn.close()

    n_teams, n_members = asyncio.run(_fetch_counts())
    assert n_teams == 5
    # 6 + 6 + 4 + 4 + 4 = 24 members across the five teams.
    assert n_members == 24


def test_every_team_has_exactly_one_leader(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_full_seed(_as_async_dsn(migrations_pg_dsn)))

    async def _leader_counts() -> list[tuple[str, int]]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            rows = await conn.fetch("""
                SELECT t.name, count(*) FILTER (WHERE tm.is_team_leader)
                  FROM teams t
                  LEFT JOIN team_members tm ON tm.team_id = t.id
                 WHERE t.is_builtin = true
                 GROUP BY t.name
                """)
            return [(r[0], int(r[1])) for r in rows]
        finally:
            await conn.close()

    for name, count in asyncio.run(_leader_counts()):
        assert count == 1, f"team {name!r} has {count} leaders (expected 1)"


def test_seeded_teams_visible_to_tenant_sessions(alembic_config, migrations_pg_dsn: str) -> None:
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
    app_dsn = f"postgresql://{PG_APP_USER}:{PG_APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"

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
                    await conn.fetchval("SELECT count(*) FROM teams WHERE is_builtin = true")
                )
        finally:
            await conn.close()

    assert asyncio.run(_seed_tenant_and_count()) == 5
