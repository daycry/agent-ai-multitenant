"""Integration tests for the Technical Writer built-in agent (task_07_05).

The Technical Writer is one of the eleven built-in agents seeded under the
platform tenant. Plan 07 gives it an explicit post-plan responsibility:
maintain the canonical 7-folder ``/docs`` structure and, at plan close,
generate the changelog, ADRs and reference updates in the project language.

These tests verify the seeded row exists under the platform tenant with the
expected role/slug + a non-empty curated system_prompt, that it is wired to
the relevant ``docs`` skills via ``agent_skills``, and that re-seeding is
idempotent (no duplicate rows, no errors).

Tests are synchronous (Alembic's CLI is sync and can't run inside an asyncio
event loop). SA + asyncpg calls inside helpers go through asyncio.run().
"""

from __future__ import annotations

import asyncio
import json

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

# The Technical Writer's stable identity and the docs-skills it must carry.
TECH_WRITER_SLUG = "technical-writer"
TECH_WRITER_NAME = "Technical Writer"
TECH_WRITER_ROLE = "technical_writer"
EXPECTED_SKILL_SLUGS = {
    "structured-writing",
    "mermaid-diagrams",
    "adr-authoring",
    "runbook-authoring",
    "api-documentation",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _run_seed_via_async_sa(dsn: str) -> int:
    """Seed agents + skills + the agent<->skill wiring, in dependency order."""
    from api_server.seeds.builtin_agents import (
        seed_builtin_agent_skills,
        seed_builtin_agents,
    )
    from api_server.seeds.builtin_skills import seed_builtin_skills
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            await seed_builtin_agents(session)
            await seed_builtin_skills(session)
            links = await seed_builtin_agent_skills(session)
        return links
    finally:
        await engine.dispose()


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        # agent_skills is dropped by CASCADE off agents/skills.
        await conn.execute("TRUNCATE agents, skills, organizations CASCADE")
    finally:
        await conn.close()


def _as_async_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn.replace("postgres://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


async def _fetch_tech_writer(dsn: str) -> asyncpg.Record | None:
    from api_server.seeds import PLATFORM_TENANT_ID
    from api_server.seeds.builtin_agents import _agent_id

    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchrow(
            """
            SELECT id, tenant_id, name, role, scope, agent_type,
                   system_prompt, model_config
              FROM agents
             WHERE id = $1 AND tenant_id = $2
            """,
            _agent_id(TECH_WRITER_SLUG),
            PLATFORM_TENANT_ID,
        )
    finally:
        await conn.close()


async def _fetch_tech_writer_skill_slugs(dsn: str) -> set[str]:
    from api_server.seeds.builtin_agents import _agent_id

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT s.id
              FROM agent_skills as_link
              JOIN skills s ON s.id = as_link.skill_id
             WHERE as_link.agent_id = $1
            """,
            _agent_id(TECH_WRITER_SLUG),
        )
    finally:
        await conn.close()

    from api_server.seeds.builtin_skills import _skill_id

    slug_by_id = {_skill_id(slug): slug for slug in EXPECTED_SKILL_SLUGS}
    return {slug_by_id[r["id"]] for r in rows if r["id"] in slug_by_id}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
def test_tech_writer_seeded_under_platform_tenant(alembic_config, migrations_pg_dsn: str) -> None:
    """The Technical Writer row exists under the platform tenant with the
    expected slug-derived id, role, scope and a non-empty system_prompt."""
    from api_server.seeds import PLATFORM_TENANT_ID

    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed_via_async_sa(_as_async_dsn(migrations_pg_dsn)))

    row = asyncio.run(_fetch_tech_writer(migrations_pg_dsn))
    assert row is not None, "Technical Writer agent was not seeded"
    assert row["name"] == TECH_WRITER_NAME
    assert row["role"] == TECH_WRITER_ROLE
    assert row["scope"] == "global_builtin"
    assert row["agent_type"] == "ai"
    assert str(row["tenant_id"]) == str(PLATFORM_TENANT_ID)
    assert row["system_prompt"] and row["system_prompt"].strip()
    # The curated prompt names the post-plan responsibility, not just generic
    # "write docs" prose.
    prompt = row["system_prompt"]
    assert "07-changelog" in prompt
    assert "05-architecture-decisions" in prompt


def test_tech_writer_carries_bilingual_prompts(alembic_config, migrations_pg_dsn: str) -> None:
    """Both es + en prompts ride along in model_config, with es active."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed_via_async_sa(_as_async_dsn(migrations_pg_dsn)))

    row = asyncio.run(_fetch_tech_writer(migrations_pg_dsn))
    assert row is not None
    cfg = (
        row["model_config"]
        if isinstance(row["model_config"], dict)
        else json.loads(row["model_config"])
    )
    prompts = cfg.get("system_prompts", {})
    assert prompts.get("es"), "missing es prompt"
    assert prompts.get("en"), "missing en prompt"
    assert prompts["es"] == row["system_prompt"]
    # The two languages are genuinely different text, not a copy-paste.
    assert prompts["es"] != prompts["en"]


def test_tech_writer_wired_to_docs_skills(alembic_config, migrations_pg_dsn: str) -> None:
    """The agent_skills junction links the Technical Writer to exactly the
    curated docs skills."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed_via_async_sa(_as_async_dsn(migrations_pg_dsn)))

    slugs = asyncio.run(_fetch_tech_writer_skill_slugs(migrations_pg_dsn))
    assert slugs == EXPECTED_SKILL_SLUGS


def test_reseed_is_idempotent(alembic_config, migrations_pg_dsn: str) -> None:
    """A second seed run touches the same single row and the same skill
    links -- no duplicates, no errors."""
    from api_server.seeds.builtin_agents import _agent_id

    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    sa_dsn = _as_async_dsn(migrations_pg_dsn)
    links1 = asyncio.run(_run_seed_via_async_sa(sa_dsn))
    links2 = asyncio.run(_run_seed_via_async_sa(sa_dsn))
    assert links1 == links2

    async def _counts() -> tuple[int, int]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            agents = int(
                await conn.fetchval(
                    "SELECT count(*) FROM agents WHERE id = $1",
                    _agent_id(TECH_WRITER_SLUG),
                )
            )
            skill_links = int(
                await conn.fetchval(
                    "SELECT count(*) FROM agent_skills WHERE agent_id = $1",
                    _agent_id(TECH_WRITER_SLUG),
                )
            )
            return agents, skill_links
        finally:
            await conn.close()

    agents, skill_links = asyncio.run(_counts())
    assert agents == 1, "re-seed must not duplicate the agent row"
    assert skill_links == len(EXPECTED_SKILL_SLUGS)


def test_tech_writer_visible_to_tenant_sessions(alembic_config, migrations_pg_dsn: str) -> None:
    """A NOBYPASSRLS tenant session sees the built-in via the
    agents_global_builtin_read policy regardless of its tenant_id."""
    from uuid import uuid4

    from api_server.seeds.builtin_agents import _agent_id

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
                    _agent_id(TECH_WRITER_SLUG),
                )
        finally:
            await conn.close()

    name = asyncio.run(_seed_tenant_and_fetch())
    assert name == TECH_WRITER_NAME


def test_only_tech_writer_carries_docs_skills(alembic_config, migrations_pg_dsn: str) -> None:
    """Negative: no OTHER built-in agent got accidentally wired to the
    CURATED DOCS skills. (El candado antiguo pinneaba el total global de
    agent_skills == las del writer; hoy otros equipos built-in — CI4 —
    también seedean skills propias, así que el invariante real es que las
    cinco skills de documentación pertenecen SOLO al Technical Writer.)"""
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_seed_via_async_sa(_as_async_dsn(migrations_pg_dsn)))

    from api_server.seeds.builtin_role_capabilities import ROLE_DEFAULT_SKILLS
    from api_server.seeds.builtin_skills import _skill_id

    # Las capacidades por rol (tanda inteligencia) reparten a propósito
    # algunas skills de docs (structured-writing→PM, adr-authoring y
    # mermaid-diagrams→arquitecto). El invariante vigente: las skills de
    # documentación que NINGÚN otro rol tiene mapeado siguen siendo
    # EXCLUSIVAS del Technical Writer.
    shared_elsewhere = {
        slug
        for role, slugs in ROLE_DEFAULT_SKILLS.items()
        if role != "technical_writer"
        for slug in slugs
    }
    exclusive = EXPECTED_SKILL_SLUGS - shared_elsewhere
    assert exclusive, "el seed dejó de tener skills exclusivas del writer — revisar el reparto"
    exclusive_ids = [_skill_id(slug) for slug in exclusive]

    async def _owners() -> set[str]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            rows = await conn.fetch(
                "SELECT DISTINCT a.name FROM agent_skills ask"
                " JOIN agents a ON a.id = ask.agent_id"
                " WHERE ask.skill_id = ANY($1::uuid[])",
                exclusive_ids,
            )
            return {r["name"] for r in rows}
        finally:
            await conn.close()

    assert asyncio.run(_owners()) == {TECH_WRITER_NAME}
