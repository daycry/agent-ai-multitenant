"""Regression: chunks of a soft-deleted KB must not be retrievable
(Plan 06.11 task_06_11_02).

`resolve_visible_kbs` filters `knowledge_bases.deleted_at IS NULL`, but
`visibility_filter_clause` (the clause the actual chunk search uses via
`bm25_chunks` / `vector_chunks`) did NOT — so a soft-deleted KB kept
feeding the RAG. This test pins the chunk-level behaviour.
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


async def _seed(dsn: str) -> dict[str, UUID]:
    tenant = uuid4()
    project = uuid4()
    kb = uuid4()
    document = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE agent_knowledge_bases, kb_projects, chunks, documents,"
            " knowledge_bases, projects, agents, user_org_memberships,"
            " organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Acme', 'acme-sd')",
            tenant,
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, 'P')",
            project,
            tenant,
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, 'KB')",
            kb,
            tenant,
        )
        await conn.execute(
            "INSERT INTO kb_projects (kb_id, project_id, tenant_id) VALUES ($1, $2, $3)",
            kb,
            project,
            tenant,
        )
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, 'Doc', 'doc.md', 'text/markdown', 'k', 1, 'indexed')",
            document,
            tenant,
            kb,
        )
        await conn.execute(
            "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content)"
            " VALUES ($1, $2, $3, 0, 'asyncpg connection pooling guide')",
            uuid4(),
            tenant,
            document,
        )
    finally:
        await conn.close()
    return {"tenant": tenant, "project": project, "kb": kb, "document": document}


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


async def _bm25(engine, *, tenant: UUID, project: UUID, query: str) -> list[UUID]:
    from api_server.rag.search import bm25_chunks

    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    async with sessionmaker() as session, session.begin():
        await session.execute(
            sa_text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant)},
        )
        return await bm25_chunks(session, query=query, tenant_id=tenant, project_id=project)


@pytest.mark.asyncio
async def test_chunk_visible_before_delete(admin_engine, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    ids = await _bm25(admin_engine, tenant=seed["tenant"], project=seed["project"], query="asyncpg")
    assert len(ids) == 1


@pytest.mark.asyncio
async def test_chunk_hidden_after_kb_soft_delete(admin_engine, migrations_pg_dsn: str) -> None:
    seed = await _seed(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE knowledge_bases SET deleted_at = now() WHERE id = $1", seed["kb"]
        )
    finally:
        await conn.close()

    ids = await _bm25(admin_engine, tenant=seed["tenant"], project=seed["project"], query="asyncpg")
    assert ids == []


@pytest.mark.asyncio
async def test_chunk_hidden_after_document_soft_delete(
    admin_engine, migrations_pg_dsn: str
) -> None:
    seed = await _seed(migrations_pg_dsn)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE documents SET deleted_at = now() WHERE id = $1", seed["document"]
        )
    finally:
        await conn.close()

    ids = await _bm25(admin_engine, tenant=seed["tenant"], project=seed["project"], query="asyncpg")
    assert ids == []
