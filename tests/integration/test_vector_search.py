"""Vector chunk search via pgvector + HNSW (Plan 04 task_04_17).

Uses :class:`HashEmbedder` to compute a deterministic query vector,
asserts the same chunk's stored vector ranks first (cosine distance
0 → top of the list), and verifies the KB-visibility filter blocks
ungranted projects."""

from __future__ import annotations

from uuid import UUID

import pytest
from alembic import command
from api_server.ingestion.embeddings import HashEmbedder
from api_server.rag.search import vector_chunks
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ._rag_helpers import seed_rag_corpus

pytestmark = pytest.mark.integration


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


async def _open_tenant_session(app_database_url: str, tenant_id: UUID):
    engine = create_async_engine(app_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(tenant_id)},
    )
    return engine, session


@pytest.mark.asyncio
async def test_query_vector_identical_to_stored_vector_ranks_first(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """If we embed text X and search with the same vector, the chunk
    whose stored vector is exactly that (cosine distance 0) must be
    the first hit."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)

    target_text = "RAG combines BM25 text search with vector similarity."
    target_id = seeded["chunks_by_content"][target_text]
    embedder = HashEmbedder()
    qvec = (await embedder.embed([target_text]))[0]

    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        ids = await vector_chunks(
            session,
            query_embedding=qvec,
            tenant_id=seeded["tenant_id"],
            project_id=seeded["project_id"],
            limit=5,
        )
    finally:
        await session.close()
        await engine.dispose()

    assert ids, "vector search returned nothing"
    assert ids[0] == target_id


@pytest.mark.asyncio
async def test_no_query_embedding_returns_empty(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)
    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        ids = await vector_chunks(
            session,
            query_embedding=None,
            tenant_id=seeded["tenant_id"],
            project_id=seeded["project_id"],
        )
    finally:
        await session.close()
        await engine.dispose()
    assert ids == []


@pytest.mark.asyncio
async def test_kb_visibility_blocks_ungranted_project(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)
    embedder = HashEmbedder()
    qvec = (await embedder.embed(["irrelevant"]))[0]

    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        ids = await vector_chunks(
            session,
            query_embedding=qvec,
            tenant_id=seeded["tenant_id"],
            project_id=seeded["other_project_id"],
        )
    finally:
        await session.close()
        await engine.dispose()
    assert ids == []
