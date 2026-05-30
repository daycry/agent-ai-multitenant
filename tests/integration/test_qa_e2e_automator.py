"""Integration tests for the QA E2E Automator agent template (Plan 09 task_09_14).

Plan 09 Fase D ships the *QA E2E Automator*: a specialised QA agent that drives
the featured Playwright marketplace tool (task_09_13) to author and run
end-to-end browser tests. It is seeded as a GLOBAL platform agent template under
the EXACT same representation as the eleven Plan 01 built-ins (the ``agents``
table: ``scope='global_builtin'``, ``is_template=true``, ``tenant_id`` = the
platform tenant, bilingual prompts in ``model_config.system_prompts``) — not a
new template system.

These tests verify:
  * The seeded row validates against the existing agent/template schema: it
    lands under the platform tenant with the slug-derived stable id, the QA
    role, ``scope='global_builtin'``, ``agent_type='ai'`` and ``is_template``.
  * It references the Playwright tool by the marketplace listing's stable
    identity (name + version) in ``model_config.marketplace_tools``.
  * It carries a coherent QA system prompt (bilingual, es active) that names
    Playwright + end-to-end testing — not generic prose.
  * Seeding is idempotent (no duplicate row on re-run) and the eleven core
    built-ins are untouched (the QA E2E Automator is the twelfth global_builtin).
  * A NOBYPASSRLS tenant session sees it via the agents_global_builtin_read
    policy (the cross-tenant boundary: a GLOBAL template is readable by every
    tenant, mutable by none).

Tests are synchronous (Alembic's CLI is sync and can't run inside an asyncio
event loop). SA + asyncpg calls inside helpers go through asyncio.run().
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _run_seed_via_async_sa(dsn: str) -> int:
    """Seed the core built-ins + the QA E2E Automator, in dependency order."""
    from api_server.seeds.builtin_agents import seed_builtin_agents
    from api_server.seeds.platform import ensure_platform_tenant
    from api_server.seeds.qa_e2e_automator import seed_qa_e2e_automator
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            await seed_builtin_agents(session)
            touched = await seed_qa_e2e_automator(session)
        return touched
    finally:
        await engine.dispose()


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("TRUNCATE agents, organizations CASCADE")
    finally:
        await conn.close()


def _as_async_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn.replace("postgres://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


async def _fetch_qa_e2e(dsn: str) -> asyncpg.Record | None:
    from api_server.seeds import PLATFORM_TENANT_ID
    from api_server.seeds.qa_e2e_automator import QA_E2E_AUTOMATOR

    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            """
            SELECT id, tenant_id, name, role, scope, agent_type, is_template,
                   review_capability, system_prompt, model_config
              FROM agents
             WHERE id = $1 AND tenant_id = $2
            """,
            QA_E2E_AUTOMATOR.id,
            PLATFORM_TENANT_ID,
        )
    finally:
        await conn.close()


def _model_config(row: asyncpg.Record) -> dict:
    cfg = row["model_config"]
    return cfg if isinstance(cfg, dict) else json.loads(cfg)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_qa_e2e_automator_validates_against_agent_schema(
    alembic_config, migrations_pg_dsn: str
) -> None:
    """The template row is a well-formed global_builtin QA agent template.

    Validating "against the existing agent/template schema" means: the seed
    inserts cleanly through the agents table (NOT NULL columns, the
    scope<->project_id check constraint, the agent_type/role/scope shape) and
    the resulting row carries the platform-template markers.
    """
    from api_server.seeds import PLATFORM_TENANT_ID
    from api_server.seeds.qa_e2e_automator import (
        QA_E2E_AUTOMATOR_NAME,
        QA_E2E_AUTOMATOR_ROLE,
    )

    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed_via_async_sa(_as_async_dsn(migrations_pg_dsn)))

    row = asyncio.run(_fetch_qa_e2e(migrations_pg_dsn))
    assert row is not None, "QA E2E Automator template was not seeded"
    assert row["name"] == QA_E2E_AUTOMATOR_NAME
    assert row["role"] == QA_E2E_AUTOMATOR_ROLE == "qa"
    assert row["scope"] == "global_builtin"
    assert row["agent_type"] == "ai"
    assert row["is_template"] is True
    assert str(row["tenant_id"]) == str(PLATFORM_TENANT_ID)
    # global_builtin => project_id IS NULL (the ck_agents_scope_project_consistency
    # check constraint would have rejected the insert otherwise; assert intent).
    assert row["system_prompt"] and row["system_prompt"].strip()


def test_qa_e2e_automator_references_playwright_tool(
    alembic_config, migrations_pg_dsn: str
) -> None:
    """It references the Playwright marketplace tool by its stable identity."""
    from api_server.marketplace.playwright import (
        PLAYWRIGHT_TOOL_NAME,
        PLAYWRIGHT_TOOL_VERSION,
    )

    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed_via_async_sa(_as_async_dsn(migrations_pg_dsn)))

    row = asyncio.run(_fetch_qa_e2e(migrations_pg_dsn))
    assert row is not None
    cfg = _model_config(row)
    tools = cfg.get("marketplace_tools", [])
    assert isinstance(tools, list) and tools, "no marketplace_tools reference"
    playwright = next((t for t in tools if t.get("name") == PLAYWRIGHT_TOOL_NAME), None)
    assert playwright is not None, "template does not reference the Playwright tool"
    assert playwright["version"] == PLAYWRIGHT_TOOL_VERSION
    assert playwright["kind"] == "tool"
    assert playwright["source"] == "marketplace"


def test_qa_e2e_automator_has_coherent_qa_prompt(alembic_config, migrations_pg_dsn: str) -> None:
    """The curated prompt names Playwright + end-to-end testing, bilingual."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed_via_async_sa(_as_async_dsn(migrations_pg_dsn)))

    row = asyncio.run(_fetch_qa_e2e(migrations_pg_dsn))
    assert row is not None
    cfg = _model_config(row)
    prompts = cfg.get("system_prompts", {})
    assert prompts.get("es"), "missing es prompt"
    assert prompts.get("en"), "missing en prompt"
    # es is the active system_prompt; the two languages are genuinely different.
    assert prompts["es"] == row["system_prompt"]
    assert prompts["es"] != prompts["en"]
    # The prompt is QA-specific and Playwright-driven, not generic.
    assert "Playwright" in row["system_prompt"]
    assert "playwright" in row["system_prompt"]  # the tool name reference
    assert "end-to-end" in prompts["en"].lower()
    assert "spec" in row["system_prompt"].lower()


def test_qa_e2e_automator_reseed_is_idempotent(alembic_config, migrations_pg_dsn: str) -> None:
    """A second seed run touches the same single row — no duplicates, no errors."""
    from api_server.seeds.qa_e2e_automator import QA_E2E_AUTOMATOR

    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    sa_dsn = _as_async_dsn(migrations_pg_dsn)
    touched1 = asyncio.run(_run_seed_via_async_sa(sa_dsn))
    touched2 = asyncio.run(_run_seed_via_async_sa(sa_dsn))
    assert touched1 == touched2 == 1

    async def _count() -> int:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            return int(
                await conn.fetchval(
                    "SELECT count(*) FROM agents WHERE id = $1", QA_E2E_AUTOMATOR.id
                )
            )
        finally:
            await conn.close()

    assert asyncio.run(_count()) == 1, "re-seed must not duplicate the template row"


def test_qa_e2e_automator_is_twelfth_global_builtin(alembic_config, migrations_pg_dsn: str) -> None:
    """It lands alongside (not instead of) the eleven core built-ins.

    The eleven core agents from Plan 01 stay intact; the QA E2E Automator is
    the twelfth global_builtin row, proving it reuses the same model rather
    than replacing or duplicating a core agent.
    """
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed_via_async_sa(_as_async_dsn(migrations_pg_dsn)))

    async def _count_builtins() -> int:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            return int(
                await conn.fetchval("SELECT count(*) FROM agents WHERE scope = 'global_builtin'")
            )
        finally:
            await conn.close()

    assert asyncio.run(_count_builtins()) == 12


@pytest.mark.cross_tenant
def test_qa_e2e_automator_visible_to_tenant_sessions(
    alembic_config, migrations_pg_dsn: str
) -> None:
    """A NOBYPASSRLS tenant session sees the GLOBAL template via the
    agents_global_builtin_read policy regardless of its tenant_id — the
    cross-tenant boundary: a global template is readable by every tenant,
    owned by none."""
    from api_server.seeds.qa_e2e_automator import QA_E2E_AUTOMATOR

    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed_via_async_sa(_as_async_dsn(migrations_pg_dsn)))

    from tests.integration.conftest import (
        PG_APP_PASSWORD,
        PG_APP_USER,
        PG_HOST,
        PG_PORT,
        PG_TEST_DB,
    )

    tenant_id = uuid4()
    app_dsn = f"postgresql://{PG_APP_USER}:{PG_APP_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_TEST_DB}"

    async def _seed_tenant_and_fetch() -> str | None:
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
                return await conn.fetchval(
                    "SELECT name FROM agents WHERE id = $1 AND scope = 'global_builtin'",
                    QA_E2E_AUTOMATOR.id,
                )
        finally:
            await conn.close()

    name = asyncio.run(_seed_tenant_and_fetch())
    assert name == QA_E2E_AUTOMATOR.name
