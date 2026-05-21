"""Integration tests for the built-in skills seed (task_01_10)."""

from __future__ import annotations

import asyncio
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

EXPECTED_CATEGORIES = {"backend", "frontend", "devops", "qa", "research", "docs"}


async def _run_seed(dsn: str) -> int:
    from api_server.seeds.builtin_skills import seed_builtin_skills
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            n = await seed_builtin_skills(session)
        return n
    finally:
        await engine.dispose()


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE skills, organizations CASCADE")
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
def test_seed_creates_builtin_skills_in_expected_range(
    alembic_config, migrations_pg_dsn: str
) -> None:
    """30-40 skills per spec (we ship 33)."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    n = asyncio.run(_run_seed(_as_async_dsn(migrations_pg_dsn)))
    assert 30 <= n <= 40, f"expected 30-40 skills, got {n}"


def test_seed_covers_all_six_categories(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed(_as_async_dsn(migrations_pg_dsn)))

    async def _categories() -> set[str]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            rows = await conn.fetch("SELECT DISTINCT category FROM skills WHERE is_builtin = true")
            return {r[0] for r in rows}
        finally:
            await conn.close()

    cats = asyncio.run(_categories())
    missing = EXPECTED_CATEGORIES - cats
    assert not missing, f"missing categories: {missing}"


def test_seed_is_idempotent(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    sa_dsn = _as_async_dsn(migrations_pg_dsn)
    n1 = asyncio.run(_run_seed(sa_dsn))
    n2 = asyncio.run(_run_seed(sa_dsn))
    assert n1 == n2

    async def _count() -> int:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            row = await conn.fetchrow("SELECT count(*) FROM skills WHERE is_builtin = true")
            return int(row[0]) if row else 0
        finally:
            await conn.close()

    total = asyncio.run(_count())
    assert total == n1


def test_each_skill_has_non_empty_prompt_fragment(alembic_config, migrations_pg_dsn: str) -> None:
    """A skill's whole purpose is its prompt_fragment -- empty would
    mean the seed produced a noop entry."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed(_as_async_dsn(migrations_pg_dsn)))

    async def _fetch() -> list[tuple[str, str]]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            rows = await conn.fetch(
                "SELECT name, prompt_fragment FROM skills WHERE is_builtin = true"
            )
            return [(r["name"], r["prompt_fragment"]) for r in rows]
        finally:
            await conn.close()

    for name, fragment in asyncio.run(_fetch()):
        assert fragment and fragment.strip(), f"empty prompt_fragment on {name!r}"
        assert len(fragment) >= 50, (
            f"prompt_fragment for {name!r} is suspiciously short " f"({len(fragment)} chars)"
        )


def test_seeded_skills_visible_to_tenant_sessions(alembic_config, migrations_pg_dsn: str) -> None:
    """Tenant sessions see built-ins via skills_builtin_read (migration 0005)."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed(_as_async_dsn(migrations_pg_dsn)))

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
                    await conn.fetchval("SELECT count(*) FROM skills WHERE is_builtin = true")
                )
        finally:
            await conn.close()

    visible = asyncio.run(_seed_tenant_and_count())
    assert 30 <= visible <= 40
