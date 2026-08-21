"""Tests for `resolve_visible_kbs` (Plan 06.9 task_06_9_03).

The resolver unions two sources:

  1. KBs granted to the project (`kb_projects`).
  2. KBs granted to the agent template (`agent_knowledge_bases`).

The plan asks for the 8-combination matrix:

      project_KB_present | agent_KB_present | shared_KB | expected
  1:        no            |       no          |    n/a    | []
  2:        yes           |       no          |    n/a    | [project_kb]
  3:        no            |       yes         |    n/a    | [agent_kb]
  4:        yes           |       yes         |    no     | [project_kb, agent_kb] (dedup'd by id)
  5:        yes           |       yes         |    yes    | one KB shared → appears ONCE
  6:        agent_id=None | project_KB        |    -      | [project_kb] (agent path skipped)
  7:        cross-tenant project_KB           |    -      | not visible (RLS)
  8:        soft-deleted KB                   |    -      | filtered out
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Seed: one tenant, one project, one agent, three KBs (project / agent /
# shared) — plus a foreign tenant KB for cross-tenant check.
# ---------------------------------------------------------------------------
async def _seed(dsn: str) -> dict[str, UUID]:
    tenant = uuid4()
    foreign_tenant = uuid4()
    user = uuid4()
    project = uuid4()
    agent = uuid4()
    kb_project_only = uuid4()
    kb_agent_only = uuid4()
    kb_shared = uuid4()
    kb_soft_deleted = uuid4()
    kb_foreign = uuid4()

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agent_knowledge_bases, kb_projects, chunks, documents,"
            " knowledge_bases, projects, agents, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, 'Acme', 'acme-vis'), ($2, 'Beta', 'beta-vis')",
            tenant,
            foreign_tenant,
        )
        await conn.execute(
            "INSERT INTO users (id, email, password_hash) VALUES ($1, 'u@acme.test', 'h')",
            user,
        )
        await conn.execute(
            "INSERT INTO user_org_memberships (id, tenant_id, user_id, role)"
            " VALUES ($1, $2, $3, 'tenant_admin')",
            uuid4(),
            tenant,
            user,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'P')",
            project,
            tenant,
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt)"
            " VALUES ($1, $2, 'A', 'backend_dev',"
            "         'global_tenant_template', 'ai', 'sp')",
            agent,
            tenant,
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES"
            " ($1, $2, 'project-only'),"
            " ($3, $4, 'agent-only'),"
            " ($5, $6, 'shared'),"
            " ($7, $8, 'soft-deleted'),"
            " ($9, $10, 'foreign')",
            kb_project_only,
            tenant,
            kb_agent_only,
            tenant,
            kb_shared,
            tenant,
            kb_soft_deleted,
            tenant,
            kb_foreign,
            foreign_tenant,
        )
        # Soft-delete one KB to verify the resolver filters it out.
        await conn.execute(
            "UPDATE knowledge_bases SET deleted_at = now() WHERE id = $1",
            kb_soft_deleted,
        )
        # Grants:
        await conn.execute(
            "INSERT INTO kb_projects (kb_id, project_id, tenant_id) VALUES"
            " ($1, $2, $3),"  # project_only → project
            " ($4, $5, $6),"  # shared → project
            " ($7, $8, $9)",  # soft-deleted → project (still filtered out)
            kb_project_only,
            project,
            tenant,
            kb_shared,
            project,
            tenant,
            kb_soft_deleted,
            project,
            tenant,
        )
        await conn.execute(
            "INSERT INTO agent_knowledge_bases (agent_id, kb_id, tenant_id) VALUES"
            " ($1, $2, $3),"  # agent_only → agent
            " ($4, $5, $6)",  # shared → agent
            agent,
            kb_agent_only,
            tenant,
            agent,
            kb_shared,
            tenant,
        )
    finally:
        await conn.close()
    return {
        "tenant": tenant,
        "foreign_tenant": foreign_tenant,
        "project": project,
        "agent": agent,
        "kb_project_only": kb_project_only,
        "kb_agent_only": kb_agent_only,
        "kb_shared": kb_shared,
        "kb_soft_deleted": kb_soft_deleted,
        "kb_foreign": kb_foreign,
    }


# ---------------------------------------------------------------------------
# Fixture — admin sessionmaker so the resolver sees both tenants when
# we test cross-tenant explicitly. Tenant scoping is enforced by the
# resolver's :tenant_id parameter, not by RLS, so we test that path.
# ---------------------------------------------------------------------------
@pytest.fixture()
def admin_engine(alembic_config, admin_database_url: str):
    command.upgrade(alembic_config, "head")
    from tests.integration.conftest import _grant_app_user_existing_tables

    asyncio.run(_grant_app_user_existing_tables())

    engine = create_async_engine(admin_database_url)
    try:
        yield engine
    finally:
        asyncio.run(engine.dispose())


async def _resolve(
    engine, *, tenant_id: UUID, project_id: UUID, agent_id: UUID | None
) -> set[UUID]:
    from api_server.rag.visibility import resolve_visible_kbs

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session, session.begin():
        # admin engine = BYPASSRLS; set app.tenant_id anyway so anyone
        # reusing this path under RLS sees consistent behaviour.
        await session.execute(
            sa_text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        ids = await resolve_visible_kbs(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            agent_id=agent_id,
        )
    return set(ids)


# ---------------------------------------------------------------------------
# Matrix
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_project_only_and_no_agent(admin_engine, migrations_pg_dsn: str) -> None:
    """agent_id=None → only project grants visible."""
    seed = await _seed(migrations_pg_dsn)
    ids = await _resolve(
        admin_engine,
        tenant_id=seed["tenant"],
        project_id=seed["project"],
        agent_id=None,
    )
    # project_only + shared are granted to the project. soft-deleted
    # KB is filtered out even though it has a kb_projects row.
    assert ids == {seed["kb_project_only"], seed["kb_shared"]}


@pytest.mark.asyncio
async def test_project_and_agent_union_dedups_shared(admin_engine, migrations_pg_dsn: str) -> None:
    """agent_id set → union of both, shared KB appears ONCE."""
    seed = await _seed(migrations_pg_dsn)
    ids = await _resolve(
        admin_engine,
        tenant_id=seed["tenant"],
        project_id=seed["project"],
        agent_id=seed["agent"],
    )
    assert ids == {
        seed["kb_project_only"],
        seed["kb_agent_only"],
        seed["kb_shared"],
    }


@pytest.mark.asyncio
async def test_agent_grant_alone(admin_engine, migrations_pg_dsn: str) -> None:
    """If the project has no grants but the agent does, the agent KBs
    are still visible — the agent axis is independent of the project
    axis."""
    seed = await _seed(migrations_pg_dsn)
    empty_project = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'Empty')",
            empty_project,
            seed["tenant"],
        )
    finally:
        await conn.close()
    ids = await _resolve(
        admin_engine,
        tenant_id=seed["tenant"],
        project_id=empty_project,
        agent_id=seed["agent"],
    )
    assert ids == {seed["kb_agent_only"], seed["kb_shared"]}


@pytest.mark.asyncio
async def test_no_grants_returns_empty(admin_engine, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    empty_project = uuid4()
    empty_agent = uuid4()
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'Empty')",
            empty_project,
            seed["tenant"],
        )
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, name, role, scope, agent_type, system_prompt)"
            " VALUES ($1, $2, 'EmptyAgent', 'qa',"
            "         'global_tenant_template', 'ai', 'sp')",
            empty_agent,
            seed["tenant"],
        )
    finally:
        await conn.close()
    ids = await _resolve(
        admin_engine,
        tenant_id=seed["tenant"],
        project_id=empty_project,
        agent_id=empty_agent,
    )
    assert ids == set()


@pytest.mark.asyncio
async def test_foreign_tenant_kb_not_visible(admin_engine, migrations_pg_dsn: str) -> None:
    """Even via BYPASSRLS, the resolver filters by :tenant_id — a KB
    in another tenant is invisible to the caller's project."""
    seed = await _seed(migrations_pg_dsn)
    ids = await _resolve(
        admin_engine,
        tenant_id=seed["tenant"],
        project_id=seed["project"],
        agent_id=seed["agent"],
    )
    assert seed["kb_foreign"] not in ids


@pytest.mark.asyncio
async def test_soft_deleted_kb_filtered(admin_engine, migrations_pg_dsn: str) -> None:
    """KB with deleted_at set must not appear even if its grant row
    exists."""
    seed = await _seed(migrations_pg_dsn)
    ids = await _resolve(
        admin_engine,
        tenant_id=seed["tenant"],
        project_id=seed["project"],
        agent_id=seed["agent"],
    )
    assert seed["kb_soft_deleted"] not in ids
