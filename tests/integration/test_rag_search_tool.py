"""End-to-end `rag_search` tool (Plan 04 task_04_20).

Exercises the full RAG path against the chunks corpus:

  1. embed the query via :class:`HashEmbedder` (so the vector path
     contributes),
  2. hybrid recall fetches candidates,
  3. :class:`DeterministicReranker` reorders them,
  4. the top-N comes back with both RRF and rerank scores.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from alembic import command
from api_server.ingestion.embeddings import HashEmbedder
from api_server.rag import (
    DeterministicReranker,
    NoopReranker,
    rag_search,
)
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
async def test_rag_search_surfaces_relevant_chunks_with_both_scores(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)

    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        hits = await rag_search(
            session,
            query="asyncpg",
            tenant_id=seeded["tenant_id"],
            project_id=seeded["project_id"],
            limit=5,
            embedder=HashEmbedder(),
            reranker=DeterministicReranker(),
        )
    finally:
        await session.close()
        await engine.dispose()

    assert hits, "rag_search returned nothing"
    # The DeterministicReranker scores by token overlap, so the chunk
    # that mentions `asyncpg` ranks first.
    assert "asyncpg" in hits[0].content.lower()
    # Both score channels populated.
    for hit in hits:
        assert hit.rrf_score > 0
        assert hit.rerank_score is not None


@pytest.mark.asyncio
async def test_rag_search_without_embedder_still_works(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """BM25-only path should still surface results (rerank still
    runs but the rank list is shorter)."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)

    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        hits = await rag_search(
            session,
            query="RAG vector similarity",
            tenant_id=seeded["tenant_id"],
            project_id=seeded["project_id"],
            limit=5,
            embedder=None,
            reranker=NoopReranker(),
        )
    finally:
        await session.close()
        await engine.dispose()

    assert hits
    # Vector ranks must all be None (no embedding path).
    for hit in hits:
        assert hit.vector_rank is None
        assert hit.bm25_rank is not None


@pytest.mark.asyncio
async def test_rag_search_ungranted_project_returns_empty(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """KB-grant boundary enforced end-to-end."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)

    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        hits = await rag_search(
            session,
            query="asyncpg",
            tenant_id=seeded["tenant_id"],
            project_id=seeded["other_project_id"],
            limit=5,
            embedder=HashEmbedder(),
            reranker=NoopReranker(),
        )
    finally:
        await session.close()
        await engine.dispose()
    assert hits == []


@pytest.mark.asyncio
async def test_rag_search_no_match_returns_empty(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)

    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        hits = await rag_search(
            session,
            query="quantumchromodynamics unrelated_token_z",
            tenant_id=seeded["tenant_id"],
            project_id=seeded["project_id"],
            limit=5,
            embedder=None,
            reranker=NoopReranker(),
        )
    finally:
        await session.close()
        await engine.dispose()
    assert hits == []
