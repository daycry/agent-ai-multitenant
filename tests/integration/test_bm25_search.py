"""BM25 chunk search via pg_trgm + tsvector (Plan 04 task_04_16).

Asserts text-relevance retrieval against the chunks corpus seeded in
`_rag_helpers.seed_rag_corpus`. The KB-visibility filter (only chunks
of KBs granted to the project surface) is exercised in
`test_kb_visibility_blocks_ungranted_project`."""

from __future__ import annotations

from uuid import UUID

import pytest
from alembic import command
from api_server.rag.search import bm25_chunks
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
async def test_query_about_asyncpg_returns_asyncpg_chunks(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)
    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        ids = await bm25_chunks(
            session,
            # Single token — `plainto_tsquery` ANDs tokens, and the
            # `simple` config lowercases `PostgreSQL` to a single
            # `postgresql` token (not `postgres`).
            query="asyncpg",
            tenant_id=seeded["tenant_id"],
            project_id=seeded["project_id"],
            limit=10,
        )
    finally:
        await session.close()
        await engine.dispose()

    # The asyncpg-mentioning chunk must surface (it's the only chunk
    # carrying that token in the fixture).
    expected = seeded["chunks_by_content"][
        "Project uses asyncpg, not psycopg3, for PostgreSQL access."
    ]
    assert expected in ids


@pytest.mark.asyncio
async def test_blank_query_returns_empty(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)
    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        assert (
            await bm25_chunks(
                session,
                query="   ",
                tenant_id=seeded["tenant_id"],
                project_id=seeded["project_id"],
            )
            == []
        )
    finally:
        await session.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_kb_visibility_blocks_ungranted_project(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """The same query from a project that lacks the grant must return
    nothing — explicit grants are the only way for a project to see
    a KB."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)
    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        ids = await bm25_chunks(
            session,
            query="asyncpg postgres",
            tenant_id=seeded["tenant_id"],
            project_id=seeded["other_project_id"],
        )
    finally:
        await session.close()
        await engine.dispose()
    assert ids == []


@pytest.mark.asyncio
async def test_limit_caps_results(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)
    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        ids = await bm25_chunks(
            session,
            query="the system uses",
            tenant_id=seeded["tenant_id"],
            project_id=seeded["project_id"],
            limit=2,
        )
    finally:
        await session.close()
        await engine.dispose()
    assert len(ids) <= 2


# ---------------------------------------------------------------------------
# P0-4 (investigación 2026-07-11): la ruta BM25 de agentes/planning usaba el
# tokenizador 'simple' (sin unaccent ni stemming español) mientras el preview
# del dueño ya usaba public.es_unaccent — y el índice GIN de chunks estaba en
# 'simple', así que el preview ni siquiera tenía índice. Unificado: ambas rutas
# y el índice usan public.es_unaccent (migración 0107).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_spanish_accents_and_stemming_match(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from uuid import uuid4 as _uuid4

    import asyncpg as _asyncpg

    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)

    # Un chunk en castellano CON acentos, insertado en la KB granted.
    conn = await _asyncpg.connect(migrations_pg_dsn)
    doc_id, chunk_id = _uuid4(), _uuid4()
    try:
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, 'Guía ES', 'guia.md', 'text/markdown', 'kb/x', 10, 'indexed')",
            doc_id,
            seeded["tenant_id"],
            seeded["kb_id"],
        )
        await conn.execute(
            "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content)"
            " VALUES ($1, $2, $3, 0,"
            "         'Guía de categorización de métricas del proyecto.')",
            chunk_id,
            seeded["tenant_id"],
            doc_id,
        )
    finally:
        await conn.close()

    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        # Query SIN acentos y con inflexión distinta: solo casa con
        # unaccent + spanish_stem (es_unaccent), jamás con 'simple'.
        ids = await bm25_chunks(
            session,
            query="categorizacion metrica",
            tenant_id=seeded["tenant_id"],
            project_id=seeded["project_id"],
            limit=10,
        )
    finally:
        await session.close()
        await engine.dispose()
    assert chunk_id in ids


@pytest.mark.asyncio
async def test_chunks_fts_index_uses_es_unaccent(schema_at_head, migrations_pg_dsn: str) -> None:
    """El índice GIN debe usar la MISMA configuración que las queries; si no,
    la búsqueda cae a seq scan silencioso."""
    import asyncpg as _asyncpg

    conn = await _asyncpg.connect(migrations_pg_dsn)
    try:
        indexdef = await conn.fetchval(
            "SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_chunks_content_fts'"
        )
    finally:
        await conn.close()
    assert indexdef is not None
    assert "es_unaccent" in indexdef
