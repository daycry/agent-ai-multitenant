"""Integration tests for the built-in CodeIgniter 4 team seed
(plan codeigniter-4-builtin-team, task ci4_seeders).

Verifies the whole CI4 built-in fabric the seeders materialize:

  * The ``codeigniter-4`` team exists with ``is_builtin=true``, 10 members,
    a single leader (``ci4-pm``) and the roster's assignment_priority 10..100.
  * The 10 ``ci4-*`` agents land with ``scope='global_builtin'``,
    ``tenant_id=PLATFORM``, ``is_template=true`` and a ``model_config`` that
    carries bilingual ``system_prompts`` but does NOT pin provider/model
    (they inherit the platform default, ADR 0055 / f87ca62).
  * Each ``ci4-*`` agent is wired to its built-in tools via ``agent_tools``.
  * The 8 ``codeigniter-4-*`` KBs are ``is_builtin=true`` and the catalog
    ingestion fills each with >0 chunks under the platform tenant.
  * The ``codeigniter-4-app`` project template points at the ``codeigniter-4``
    team and its ``default_kb_grants`` resolve to the 8 CI4 KB ids.
  * Cross-tenant isolation: a tenant project that was GRANTED a CI4 KB
    (``kb_projects`` row — what template adoption materializes) sees it via
    the RAG visibility resolver, while a tenant project WITHOUT a grant does
    not.
  * The eleven core built-in agents stay intact (CI4 is a separate loader).

Tests are synchronous around ``asyncio.run`` like the sibling seed tests
(Alembic's CLI is sync). DB isolation: the conftest recreates
``agentic_platform_test``; nothing here touches the dev database.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

CI4_AGENT_SLUGS = (
    "ci4-pm",
    "ci4-architect",
    "ci4-backend",
    "ci4-dba",
    "ci4-frontend",
    "ci4-auth-security",
    "ci4-i18n",
    "ci4-qa",
    "ci4-reviewer",
    "ci4-devops",
)

CI4_KB_SLUGS = (
    "codeigniter-4-conventions",
    "codeigniter-4-architecture",
    "codeigniter-4-doctrine-data",
    "codeigniter-4-testing",
    "codeigniter-4-security",
    "codeigniter-4-i18n",
    "codeigniter-4-frontend",
    "codeigniter-4-ci-cd",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _as_async_dsn(dsn: str) -> str:
    if dsn.startswith("postgresql+asyncpg://"):
        return dsn
    return dsn.replace("postgres://", "postgresql+asyncpg://", 1).replace(
        "postgresql://", "postgresql+asyncpg://", 1
    )


async def _truncate(dsn: str) -> None:
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " kb_categories, agent_tools, tools, team_members, teams,"
            " projects, agents, organizations RESTART IDENTITY CASCADE"
        )
    finally:
        await conn.close()


async def _run_full_seed(dsn: str) -> dict[str, int]:
    """Seed the full FK chain CI4 depends on, in runner order.

    agents (core) -> ci4 agents -> tools -> ci4 agent_tools -> teams ->
    kb categories -> kbs -> catalog ingestion (HashEmbedder) -> project
    templates. Returns the per-seed counts for assertions.
    """
    from api_server.ingestion.embeddings import HashEmbedder
    from api_server.seeds.builtin_agents import seed_builtin_agents
    from api_server.seeds.builtin_kb_categories import seed_builtin_kb_categories
    from api_server.seeds.builtin_kbs import seed_builtin_kbs
    from api_server.seeds.builtin_project_templates import (
        seed_builtin_project_templates,
    )
    from api_server.seeds.builtin_teams import seed_builtin_teams
    from api_server.seeds.builtin_tools import seed_builtin_tools
    from api_server.seeds.catalog_ingestion import seed_catalog_ingestion
    from api_server.seeds.ci4_team import (
        seed_ci4_agent_tools,
        seed_ci4_agents,
        seed_ci4_project_template,
        seed_ci4_team,
    )
    from api_server.seeds.platform import ensure_platform_tenant
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(dsn, pool_pre_ping=False)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        async with sm() as session, session.begin():
            await ensure_platform_tenant(session)
            n_core_agents = await seed_builtin_agents(session)
            n_ci4_agents = await seed_ci4_agents(session)
            await seed_builtin_tools(session)
            n_ci4_agent_tools = await seed_ci4_agent_tools(session)
            n_core_teams = await seed_builtin_teams(session)
            await seed_ci4_team(session)
            await seed_builtin_kb_categories(session)
            n_kbs = await seed_builtin_kbs(session)
            catalog = await seed_catalog_ingestion(session, embedder=HashEmbedder())
            n_core_templates = await seed_builtin_project_templates(session)
            await seed_ci4_project_template(session)
        return {
            "core_agents": n_core_agents,
            "ci4_agents": n_ci4_agents,
            "ci4_agent_tools": n_ci4_agent_tools,
            "core_teams": n_core_teams,
            "kbs": n_kbs,
            "catalog_docs": len(catalog),
            "core_templates": n_core_templates,
        }
    finally:
        await engine.dispose()


# ---------------------------------------------------------------------------
# Team
# ---------------------------------------------------------------------------
def test_codeigniter_4_team_has_ten_members_and_pm_leader(
    alembic_config, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    counts = asyncio.run(_run_full_seed(_as_async_dsn(migrations_pg_dsn)))

    assert counts["core_agents"] == 11
    assert counts["ci4_agents"] == 10

    async def _inspect() -> dict[str, object]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            team = await conn.fetchrow(
                "SELECT id, is_builtin FROM teams WHERE name = 'CodeIgniter 4'"
            )
            assert team is not None
            members = await conn.fetch(
                """
                SELECT a.name AS agent_name, tm.is_team_leader, tm.assignment_priority,
                       tm.role_in_team
                  FROM team_members tm
                  JOIN agents a ON a.id = tm.agent_id
                 WHERE tm.team_id = $1
                 ORDER BY tm.assignment_priority
                """,
                team["id"],
            )
            return {"is_builtin": team["is_builtin"], "members": members}
        finally:
            await conn.close()

    result = asyncio.run(_inspect())
    assert result["is_builtin"] is True
    members = result["members"]  # type: ignore[assignment]
    assert len(members) == 10
    # Exactly one leader, and it is ci4-pm (priority 10).
    leaders = [m for m in members if m["is_team_leader"]]
    assert len(leaders) == 1
    assert leaders[0]["agent_name"] == "CodeIgniter 4 — Project Manager"
    assert leaders[0]["assignment_priority"] == 10
    # Priorities span the roster 10..100 in steps of 10.
    assert [m["assignment_priority"] for m in members] == list(range(10, 101, 10))


# ---------------------------------------------------------------------------
# Agents — scope, template flag, model_config without provider/model
# ---------------------------------------------------------------------------
def test_ci4_agents_are_global_builtin_without_pinned_model(
    alembic_config, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_full_seed(_as_async_dsn(migrations_pg_dsn)))

    async def _fetch() -> list[asyncpg.Record]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            return await conn.fetch("""
                SELECT name, scope, tenant_id, is_template, system_prompt, model_config
                  FROM agents
                 WHERE name LIKE 'CodeIgniter 4 —%'
                """)
        finally:
            await conn.close()

    from api_server.seeds import PLATFORM_TENANT_ID

    rows = asyncio.run(_fetch())
    assert len(rows) == 10
    for row in rows:
        assert row["scope"] == "global_builtin", row["name"]
        assert row["tenant_id"] == PLATFORM_TENANT_ID, row["name"]
        assert row["is_template"] is True, row["name"]
        assert row["system_prompt"], row["name"]
        cfg = (
            row["model_config"]
            if isinstance(row["model_config"], dict)
            else json.loads(row["model_config"])
        )
        # Bilingual prompts present; ES mirrors the active system_prompt.
        prompts = cfg.get("system_prompts", {})
        assert "es" in prompts and "en" in prompts, row["name"]
        assert prompts["es"] == row["system_prompt"]
        # The agents do NOT pin provider/model: they inherit the platform
        # default (ADR 0055). If either were present the dispatch would not
        # fall back to the default.
        assert "provider" not in cfg, row["name"]
        assert "model" not in cfg, row["name"]


def test_ci4_agents_carry_tool_assignments(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    counts = asyncio.run(_run_full_seed(_as_async_dsn(migrations_pg_dsn)))

    # Every CI4 agent gets at least the base tool set, so the total > 0 and
    # matches the seeder's reported link count.
    assert counts["ci4_agent_tools"] > 0

    async def _inspect() -> tuple[int, list[tuple[str, int]]]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            total = int(
                await conn.fetchval("""
                    SELECT count(*)
                      FROM agent_tools at
                      JOIN agents a ON a.id = at.agent_id
                     WHERE a.name LIKE 'CodeIgniter 4 —%'
                    """)
            )
            per_agent = await conn.fetch("""
                SELECT a.name, count(at.tool_id) AS n
                  FROM agents a
                  LEFT JOIN agent_tools at ON at.agent_id = a.id
                 WHERE a.name LIKE 'CodeIgniter 4 —%'
                 GROUP BY a.name
                """)
            return total, [(r["name"], int(r["n"])) for r in per_agent]
        finally:
            await conn.close()

    total, per_agent = asyncio.run(_inspect())
    assert total == counts["ci4_agent_tools"]
    # No CI4 agent ends up with zero tools (every one carries the base set).
    for name, n in per_agent:
        assert n > 0, name


# ---------------------------------------------------------------------------
# KBs + catalog ingestion
# ---------------------------------------------------------------------------
def test_ci4_kbs_are_builtin_and_ingested(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_full_seed(_as_async_dsn(migrations_pg_dsn)))

    from api_server.seeds import PLATFORM_TENANT_ID
    from api_server.seeds.builtin_kbs import kb_id_for_slug
    from api_server.seeds.catalog_ingestion import catalog_document_id_for_slug

    async def _inspect() -> None:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            for slug in CI4_KB_SLUGS:
                kb_id = kb_id_for_slug(slug)
                kb = await conn.fetchrow(
                    "SELECT is_builtin, tenant_id FROM knowledge_bases WHERE id = $1",
                    kb_id,
                )
                assert kb is not None, slug
                assert kb["is_builtin"] is True, slug
                assert kb["tenant_id"] == PLATFORM_TENANT_ID, slug
                # Catalog ingestion produced one indexed document + >0 chunks
                # under the platform tenant.
                document_id = catalog_document_id_for_slug(slug)
                n_docs = await conn.fetchval(
                    "SELECT count(*) FROM documents"
                    " WHERE id = $1 AND kb_id = $2 AND tenant_id = $3 AND status = 'indexed'",
                    document_id,
                    kb_id,
                    PLATFORM_TENANT_ID,
                )
                assert n_docs == 1, slug
                n_chunks = await conn.fetchval(
                    "SELECT count(*) FROM chunks WHERE document_id = $1 AND tenant_id = $2",
                    document_id,
                    PLATFORM_TENANT_ID,
                )
                assert n_chunks > 0, slug
        finally:
            await conn.close()

    asyncio.run(_inspect())


# ---------------------------------------------------------------------------
# Project template
# ---------------------------------------------------------------------------
def test_codeigniter_4_app_template_grants_eight_kbs(
    alembic_config, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_full_seed(_as_async_dsn(migrations_pg_dsn)))

    from api_server.seeds.builtin_kbs import kb_id_for_slug

    async def _inspect() -> dict[str, object]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            row = await conn.fetchrow("""
                SELECT p.default_kb_grants, t.name AS team_name, t.is_builtin
                  FROM projects p
                  JOIN teams t ON t.id = p.team_id
                 WHERE p.name = 'Plantilla: App CodeIgniter 4'
                   AND p.is_template = true
                """)
            assert row is not None
            return {
                "grants": list(row["default_kb_grants"]),
                "team_name": row["team_name"],
                "is_builtin": row["is_builtin"],
            }
        finally:
            await conn.close()

    result = asyncio.run(_inspect())
    assert result["team_name"] == "CodeIgniter 4"
    assert result["is_builtin"] is True
    grants = result["grants"]  # type: ignore[assignment]
    assert set(grants) == set(CI4_KB_SLUGS)
    # Every grant slug resolves to an existing built-in KB id (no dangling
    # reference — kb_id_for_slug never fails and the row exists).
    conn_check_ids = {kb_id_for_slug(s) for s in grants}
    assert len(conn_check_ids) == 8


# ---------------------------------------------------------------------------
# Cross-tenant visibility (what template adoption materializes)
# ---------------------------------------------------------------------------
@pytest.mark.cross_tenant
def test_ci4_kb_visible_only_after_grant(alembic_config, migrations_pg_dsn: str) -> None:
    """A CI4 built-in KB feeds the RAG of a tenant project ONLY when that
    project was granted it (the kb_projects row template adoption creates).
    A second tenant project without the grant does NOT see it — isolation
    intact."""
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_full_seed(_as_async_dsn(migrations_pg_dsn)))

    from api_server.rag.visibility import resolve_visible_kbs
    from api_server.seeds.builtin_kbs import kb_id_for_slug
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    kb_id = kb_id_for_slug("codeigniter-4-conventions")
    tenant_granted = uuid4()
    project_granted = uuid4()
    tenant_ungranted = uuid4()
    project_ungranted = uuid4()

    async def _setup_and_resolve() -> tuple[bool, bool]:
        # Two tenant orgs, each with a real (non-template) project; only the
        # first gets a kb_projects grant to the CI4 KB.
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            for tid, name in ((tenant_granted, "G"), (tenant_ungranted, "U")):
                await conn.execute(
                    "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                    tid,
                    name,
                    str(tid)[:8],
                )
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name, status)"
                " VALUES ($1, $2, 'pg', 'active')",
                project_granted,
                tenant_granted,
            )
            await conn.execute(
                "INSERT INTO projects (id, tenant_id, name, status)"
                " VALUES ($1, $2, 'pu', 'active')",
                project_ungranted,
                tenant_ungranted,
            )
            await conn.execute(
                "INSERT INTO kb_projects (kb_id, project_id, tenant_id) VALUES ($1, $2, $3)",
                kb_id,
                project_granted,
                tenant_granted,
            )
        finally:
            await conn.close()

        engine = create_async_engine(_as_async_dsn(migrations_pg_dsn), pool_pre_ping=False)
        try:
            sm = async_sessionmaker(engine, expire_on_commit=False)
            async with sm() as session:
                visible_granted = await resolve_visible_kbs(
                    session, tenant_id=tenant_granted, project_id=project_granted
                )
                visible_ungranted = await resolve_visible_kbs(
                    session, tenant_id=tenant_ungranted, project_id=project_ungranted
                )
            return (kb_id in visible_granted, kb_id in visible_ungranted)
        finally:
            await engine.dispose()

    granted_sees, ungranted_sees = asyncio.run(_setup_and_resolve())
    assert granted_sees is True
    assert ungranted_sees is False


# ---------------------------------------------------------------------------
# Core agent count stays stable + idempotency
# ---------------------------------------------------------------------------
def test_eleven_core_agents_stay_intact(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))
    asyncio.run(_run_full_seed(_as_async_dsn(migrations_pg_dsn)))

    async def _counts() -> tuple[int, int]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            total_builtin = int(
                await conn.fetchval("SELECT count(*) FROM agents WHERE scope = 'global_builtin'")
            )
            ci4 = int(
                await conn.fetchval(
                    "SELECT count(*) FROM agents WHERE name LIKE 'CodeIgniter 4 —%'"
                )
            )
            return total_builtin, ci4
        finally:
            await conn.close()

    total_builtin, ci4 = asyncio.run(_counts())
    # 11 core + 10 CI4 = 21 global_builtin AI agents (qa_e2e_automator and the
    # human templates are NOT seeded by this test's chain).
    assert ci4 == 10
    assert total_builtin == 21


def test_ci4_seed_is_idempotent(alembic_config, migrations_pg_dsn: str) -> None:
    command.upgrade(alembic_config, "head")
    asyncio.run(_truncate(migrations_pg_dsn))

    sa_dsn = _as_async_dsn(migrations_pg_dsn)
    first = asyncio.run(_run_full_seed(sa_dsn))
    second = asyncio.run(_run_full_seed(sa_dsn))
    assert first == second

    async def _counts() -> tuple[int, int, int, int]:
        conn = await asyncpg.connect(migrations_pg_dsn)
        try:
            ci4_agents = int(
                await conn.fetchval(
                    "SELECT count(*) FROM agents WHERE name LIKE 'CodeIgniter 4 —%'"
                )
            )
            ci4_members = int(
                await conn.fetchval(
                    "SELECT count(*) FROM team_members tm JOIN teams t ON t.id = tm.team_id"
                    " WHERE t.name = 'CodeIgniter 4'"
                )
            )
            ci4_tools = int(
                await conn.fetchval(
                    "SELECT count(*) FROM agent_tools at JOIN agents a ON a.id = at.agent_id"
                    " WHERE a.name LIKE 'CodeIgniter 4 —%'"
                )
            )
            ci4_kbs = int(
                await conn.fetchval(
                    "SELECT count(*) FROM knowledge_bases"
                    " WHERE name LIKE 'CodeIgniter 4 —%' AND is_builtin = true"
                )
            )
            return ci4_agents, ci4_members, ci4_tools, ci4_kbs
        finally:
            await conn.close()

    ci4_agents, ci4_members, ci4_tools, ci4_kbs = asyncio.run(_counts())
    assert ci4_agents == 10
    assert ci4_members == 10
    assert ci4_tools == first["ci4_agent_tools"]
    assert ci4_kbs == 8
