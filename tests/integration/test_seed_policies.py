"""Integration tests for the built-in approval-policy seed (task_01_14)."""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

EXPECTED_POLICY_NAMES = {"Sandbox", "Desarrollo", "Producción", "Cliente Externo"}


async def _run_seed(dsn: str) -> int:
    from api_server.seeds.builtin_approval_policies import (
        seed_builtin_approval_policies,
    )
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            return await seed_builtin_approval_policies(session)
    finally:
        await engine.dispose()


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE approval_policy_templates, organizations CASCADE")
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
def test_seed_creates_four_builtin_policies(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    n = asyncio.run(_run_seed(_as_async_dsn(migrations_pg_dsn)))
    assert n == 4

    async def _names() -> set[str]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            rows = await conn.fetch(
                "SELECT name FROM approval_policy_templates WHERE is_builtin = true"
            )
            return {r[0] for r in rows}
        finally:
            await conn.close()

    assert asyncio.run(_names()) == EXPECTED_POLICY_NAMES


def test_sandbox_is_all_auto_and_customer_is_all_human(
    alembic_config, migrations_pg_dsn: str
) -> None:
    """End-of-spectrum templates: Sandbox lets everything pass; Cliente
    Externo gates everything. These are the load-bearing presets."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed(_as_async_dsn(migrations_pg_dsn)))

    async def _fetch() -> dict[str, dict[str, str]]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            rows = await conn.fetch(
                "SELECT name, categories FROM approval_policy_templates WHERE is_builtin = true"
            )
            out: dict[str, dict[str, str]] = {}
            for r in rows:
                raw = r["categories"]
                obj = raw if isinstance(raw, dict) else json.loads(raw)
                out[r["name"]] = obj.get("categories", {})
            return out
        finally:
            await conn.close()

    policies = asyncio.run(_fetch())
    sandbox = policies["Sandbox"]
    customer = policies["Cliente Externo"]
    assert set(sandbox.values()) == {"auto"}, "Sandbox must be all auto"
    assert set(customer.values()) == {"human_required"}, (
        "Cliente Externo must be all human_required"
    )
    assert sandbox.keys() == customer.keys(), "All templates must enumerate the same categories"


def test_all_thirteen_categories_present(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed(_as_async_dsn(migrations_pg_dsn)))

    async def _fetch_one() -> dict[str, str]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            row = await conn.fetchrow(
                "SELECT categories FROM approval_policy_templates"
                " WHERE name = 'Sandbox' AND is_builtin = true"
            )
            raw = row["categories"]
            obj = raw if isinstance(raw, dict) else json.loads(raw)
            return obj.get("categories", {})
        finally:
            await conn.close()

    cats = asyncio.run(_fetch_one())
    assert len(cats) == 13, f"expected 13 categories, got {len(cats)}: {sorted(cats)}"


def test_seed_is_idempotent(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    sa_dsn = _as_async_dsn(migrations_pg_dsn)
    n1 = asyncio.run(_run_seed(sa_dsn))
    n2 = asyncio.run(_run_seed(sa_dsn))
    assert n1 == n2 == 4

    async def _count() -> int:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            return int(
                await conn.fetchval(
                    "SELECT count(*) FROM approval_policy_templates WHERE is_builtin = true"
                )
            )
        finally:
            await conn.close()

    assert asyncio.run(_count()) == 4


def test_seeded_policies_visible_to_tenant_sessions(alembic_config, migrations_pg_dsn: str) -> None:
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
                    await conn.fetchval(
                        "SELECT count(*) FROM approval_policy_templates WHERE is_builtin = true"
                    )
                )
        finally:
            await conn.close()

    assert asyncio.run(_seed_tenant_and_count()) == 4
