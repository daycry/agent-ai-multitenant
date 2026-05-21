"""Integration tests for the built-in tools seed (task_01_11)."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

EXPECTED_IMPL_TYPES = {"builtin", "python_function", "http_endpoint", "docker_command"}
EXPECTED_SECURITY = {"safe", "sandboxed", "privileged"}


async def _run_seed(dsn: str) -> int:
    from api_server.seeds.builtin_tools import seed_builtin_tools
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            return await seed_builtin_tools(session)
    finally:
        await engine.dispose()


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE tools, organizations CASCADE")
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
def test_seed_creates_tools_in_expected_range(alembic_config, migrations_pg_dsn: str) -> None:
    """Spec asks for 15-20 tools."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    n = asyncio.run(_run_seed(_as_async_dsn(migrations_pg_dsn)))
    assert 15 <= n <= 20, f"expected 15-20 tools, got {n}"


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
            row = await conn.fetchrow("SELECT count(*) FROM tools WHERE is_builtin = true")
            return int(row[0]) if row else 0
        finally:
            await conn.close()

    assert asyncio.run(_count()) == n1


def test_tool_schemas_are_valid_json_objects(alembic_config, migrations_pg_dsn: str) -> None:
    """input_schema / output_schema must each be a JSON object with a
    top-level type='object' -- that's the contract Plan 02's tool
    invoker will rely on for parameter validation."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed(_as_async_dsn(migrations_pg_dsn)))

    async def _fetch() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            return await conn.fetch(
                "SELECT name, input_schema, output_schema FROM tools WHERE is_builtin = true"
            )
        finally:
            await conn.close()

    for row in asyncio.run(_fetch()):
        for key in ("input_schema", "output_schema"):
            raw = row[key]
            obj = raw if isinstance(raw, dict) else json.loads(raw)
            assert obj.get("type") == "object", f"{row['name']}.{key} must declare type='object'"


def test_implementation_types_and_security_levels_are_valid(
    alembic_config, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed(_as_async_dsn(migrations_pg_dsn)))

    async def _fetch() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            return await conn.fetch(
                "SELECT name, implementation_type, security_level FROM tools"
                " WHERE is_builtin = true"
            )
        finally:
            await conn.close()

    rows = asyncio.run(_fetch())
    impl_seen = {r["implementation_type"] for r in rows}
    sec_seen = {r["security_level"] for r in rows}
    assert (
        impl_seen <= EXPECTED_IMPL_TYPES
    ), f"unknown implementation_type: {impl_seen - EXPECTED_IMPL_TYPES}"
    assert sec_seen <= EXPECTED_SECURITY, f"unknown security_level: {sec_seen - EXPECTED_SECURITY}"


def test_seeded_tools_visible_to_tenant_sessions(alembic_config, migrations_pg_dsn: str) -> None:
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
                    await conn.fetchval("SELECT count(*) FROM tools WHERE is_builtin = true")
                )
        finally:
            await conn.close()

    visible = asyncio.run(_seed_tenant_and_count())
    assert 15 <= visible <= 20
