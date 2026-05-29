"""Built-in KB visibility (Plan 06.12 task_06_12_03, ADR 0029).

Una KB built-in (`is_builtin = true`, sembrada bajo PLATFORM_TENANT_ID):

  - es **visible/grantable** a cualquier tenant (policy builtin_read),
  - alimenta el RAG de un tenant SOLO si éste la concedió a su
    proyecto/agente (built-in NO es auto-visible en retrieval),
  - sigue **aislada cross-tenant**: el grant de A no la expone a B.
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

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant_a = uuid4()
    tenant_b = uuid4()
    project_a = uuid4()
    project_b = uuid4()
    kb_builtin = uuid4()  # granted to A
    kb_builtin_ungranted = uuid4()  # built-in but no grant
    doc = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agent_knowledge_bases, kb_projects, chunks, documents,"
            " knowledge_bases, projects, agents, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES"
            " ($1, 'Platform', 'platform-biv'), ($2, 'A', 'a-biv'), ($3, 'B', 'b-biv')",
            _PLATFORM_TENANT_ID,
            tenant_a,
            tenant_b,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'PA'), ($3, $4, 'PB')",
            project_a,
            tenant_a,
            project_b,
            tenant_b,
        )
        # Two built-in KBs under the platform tenant.
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name, is_builtin) VALUES"
            " ($1, $2, 'builtin-granted', true), ($3, $4, 'builtin-ungranted', true)",
            kb_builtin,
            _PLATFORM_TENANT_ID,
            kb_builtin_ungranted,
            _PLATFORM_TENANT_ID,
        )
        # Built-in KB content lives under the platform tenant too.
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, 'D', 'd.md', 'text/markdown', 'k', 1, 'indexed')",
            doc,
            _PLATFORM_TENANT_ID,
            kb_builtin,
        )
        await conn.execute(
            "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content)"
            " VALUES ($1, $2, $3, 0, 'asyncpg pooling built-in guide')",
            uuid4(),
            _PLATFORM_TENANT_ID,
            doc,
        )
        # Tenant A grants the built-in KB to its project.
        await conn.execute(
            "INSERT INTO kb_projects (kb_id, project_id, tenant_id) VALUES ($1, $2, $3)",
            kb_builtin,
            project_a,
            tenant_a,
        )
    finally:
        await conn.close()
    return {
        "tenant_a": tenant_a,
        "tenant_b": tenant_b,
        "project_a": project_a,
        "project_b": project_b,
        "kb_builtin": kb_builtin,
        "kb_builtin_ungranted": kb_builtin_ungranted,
    }


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


async def _resolve(engine, *, tenant_id: UUID, project_id: UUID) -> set[UUID]:
    from api_server.rag.visibility import resolve_visible_kbs

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session, session.begin():
        await session.execute(
            sa_text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        ids = await resolve_visible_kbs(session, tenant_id=tenant_id, project_id=project_id)
    return set(ids)


async def _bm25(engine, *, tenant_id: UUID, project_id: UUID) -> list[UUID]:
    from api_server.rag.search import bm25_chunks

    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as session, session.begin():
        await session.execute(
            sa_text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )
        return await bm25_chunks(
            session, query="asyncpg", tenant_id=tenant_id, project_id=project_id
        )


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_granted_builtin_visible_to_tenant(admin_engine, migrations_pg_dsn: str) -> None:
    s = await _seed(migrations_pg_dsn)
    ids = await _resolve(admin_engine, tenant_id=s["tenant_a"], project_id=s["project_a"])
    assert s["kb_builtin"] in ids


@pytest.mark.asyncio
async def test_ungranted_builtin_not_in_rag(admin_engine, migrations_pg_dsn: str) -> None:
    """Built-in is NOT auto-visible — only when granted."""
    s = await _seed(migrations_pg_dsn)
    ids = await _resolve(admin_engine, tenant_id=s["tenant_a"], project_id=s["project_a"])
    assert s["kb_builtin_ungranted"] not in ids


@pytest.mark.asyncio
async def test_builtin_grant_isolated_cross_tenant(admin_engine, migrations_pg_dsn: str) -> None:
    """Tenant A's grant of a built-in must not expose it to tenant B."""
    s = await _seed(migrations_pg_dsn)
    ids = await _resolve(admin_engine, tenant_id=s["tenant_b"], project_id=s["project_b"])
    assert s["kb_builtin"] not in ids


# ---------------------------------------------------------------------------
# Chunk filter (RAG search)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_granted_builtin_chunks_retrievable(admin_engine, migrations_pg_dsn: str) -> None:
    s = await _seed(migrations_pg_dsn)
    ids = await _bm25(admin_engine, tenant_id=s["tenant_a"], project_id=s["project_a"])
    assert len(ids) == 1


@pytest.mark.asyncio
async def test_builtin_chunks_isolated_cross_tenant(admin_engine, migrations_pg_dsn: str) -> None:
    s = await _seed(migrations_pg_dsn)
    ids = await _bm25(admin_engine, tenant_id=s["tenant_b"], project_id=s["project_b"])
    assert ids == []
