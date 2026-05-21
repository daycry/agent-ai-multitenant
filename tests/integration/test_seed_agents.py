"""Integration tests for the built-in agents seed (task_01_09).

Verifies:
  - The seed inserts all eleven agents under the platform tenant with
    scope='global_builtin'.
  - Stable IDs make re-running idempotent (no duplicate rows, no errors).
  - Each agent carries bilingual system_prompts in model_config plus the
    Spanish version as the active `system_prompt`.
  - A regular tenant session sees the eleven agents via the
    `agents_global_builtin_read` SELECT policy.

Tests are synchronous (Alembic's CLI is sync and can't run inside an
asyncio event loop). The SA + asyncpg calls inside helpers go through
asyncio.run() one at a time.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _run_seed_via_async_sa(dsn: str) -> int:
    from api_server.seeds.builtin_agents import seed_builtin_agents
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            n = await seed_builtin_agents(session)
        return n
    finally:
        await engine.dispose()


async def _truncate_agents(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE agents, organizations CASCADE")
    finally:
        await conn.close()


async def _count_builtins(dsn: str) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow("SELECT count(*) FROM agents WHERE scope = 'global_builtin'")
        return int(row[0]) if row else 0
    finally:
        await conn.close()


def _as_async_dsn(dsn: str) -> str:
    """conftest's `migrations_pg_dsn` is asyncpg-style without the SA
    prefix; SA's create_async_engine wants `postgresql+asyncpg://`."""
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn.replace("postgres://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_seed_creates_eleven_builtin_agents(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate_agents(migrations_pg_dsn))

    n = asyncio.run(_run_seed_via_async_sa(_as_async_dsn(migrations_pg_dsn)))
    assert n == 11

    total = asyncio.run(_count_builtins(migrations_pg_dsn))
    assert total == 11


def test_seed_is_idempotent(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate_agents(migrations_pg_dsn))

    sa_dsn = _as_async_dsn(migrations_pg_dsn)
    asyncio.run(_run_seed_via_async_sa(sa_dsn))
    asyncio.run(_run_seed_via_async_sa(sa_dsn))

    total = asyncio.run(_count_builtins(migrations_pg_dsn))
    assert total == 11


def test_seed_populates_bilingual_prompts(alembic_config, migrations_pg_dsn: str) -> None:
    import json

    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate_agents(migrations_pg_dsn))
    asyncio.run(_run_seed_via_async_sa(_as_async_dsn(migrations_pg_dsn)))

    async def _fetch_rows() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            return await conn.fetch(
                """
                SELECT name, system_prompt, model_config
                  FROM agents
                 WHERE scope = 'global_builtin'
                """
            )
        finally:
            await conn.close()

    rows = asyncio.run(_fetch_rows())
    assert len(rows) == 11
    for row in rows:
        assert row["system_prompt"], f"empty system_prompt on {row['name']}"
        cfg = (
            row["model_config"]
            if isinstance(row["model_config"], dict)
            else json.loads(row["model_config"])
        )
        prompts = cfg.get("system_prompts", {})
        assert "es" in prompts, f"missing es prompt for {row['name']}"
        assert "en" in prompts, f"missing en prompt for {row['name']}"
        assert prompts["es"] == row["system_prompt"]


def test_seeded_builtins_visible_to_tenant_sessions(alembic_config, migrations_pg_dsn: str) -> None:
    """The agents_global_builtin_read policy exposes built-ins to every
    tenant session regardless of tenant_id."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate_agents(migrations_pg_dsn))
    asyncio.run(_run_seed_via_async_sa(_as_async_dsn(migrations_pg_dsn)))

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
        # Tenant org goes in via the migrations role first.
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

        # Then a NOBYPASSRLS connection with app.tenant_id set.
        conn = await asyncpg.connect(app_dsn)
        try:
            async with conn.transaction():
                await conn.execute(
                    "SELECT set_config('app.tenant_id', $1, true)",
                    str(tenant_id),
                )
                return int(
                    await conn.fetchval(
                        "SELECT count(*) FROM agents WHERE scope = 'global_builtin'"
                    )
                )
        finally:
            await conn.close()

    visible = asyncio.run(_seed_tenant_and_count())
    assert visible == 11
