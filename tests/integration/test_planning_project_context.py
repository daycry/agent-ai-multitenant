"""P0-5 (investigación 2026-07-11): el contexto RAG del planning, completo.

`build_project_context` alimentaba el chat de planning con `recall_chunks` SIN
embedder (BM25-only: el path vectorial devolvía siempre []) y SIN `agent_id`
(las KBs granted al agente por rol — `agent_knowledge_bases`, Plan 06.9 — eran
invisibles al planificar). Ahora acepta ambos y los usa vía `rag_search`.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.chat.responder import build_project_context
from api_server.db.domain import Project
from api_server.ingestion.embeddings import HashEmbedder
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


async def _seed_agent_only_kb(dsn: str, *, tenant_id: UUID, project_id: UUID, content: str) -> UUID:
    """Un agente con una KB granted SOLO a él (no al proyecto) con un chunk."""
    agent_id, kb_id, doc_id, chunk_id = uuid4(), uuid4(), uuid4(), uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "INSERT INTO agents"
            " (id, tenant_id, project_id, name, role, system_prompt, memory_scope, scope)"
            " VALUES ($1, $2, $3, 'planner', 'backend_dev', 'x', 'team_shared', 'project_local')",
            agent_id,
            tenant_id,
            project_id,
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, 'KB rol')",
            kb_id,
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO agent_knowledge_bases (agent_id, kb_id, tenant_id) VALUES ($1, $2, $3)",
            agent_id,
            kb_id,
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, 'Rol doc', 'rol.md', 'text/markdown', 'kb/rol', 10, 'indexed')",
            doc_id,
            tenant_id,
            kb_id,
        )
        await conn.execute(
            "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content)"
            " VALUES ($1, $2, $3, 0, $4)",
            chunk_id,
            tenant_id,
            doc_id,
            content,
        )
    finally:
        await conn.close()
    return agent_id


@pytest.mark.asyncio
async def test_agent_role_kbs_feed_planning_context(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)
    marker = "doctrine repositories siguen el patron del equipo"
    agent_id = await _seed_agent_only_kb(
        migrations_pg_dsn,
        tenant_id=seeded["tenant_id"],
        project_id=seeded["project_id"],
        content=marker,
    )

    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        project = await session.get(Project, seeded["project_id"])
        with_agent = await build_project_context(
            session, project, "doctrine repositories", agent_id=agent_id
        )
        without_agent = await build_project_context(session, project, "doctrine repositories")
    finally:
        await session.close()
        await engine.dispose()

    assert any(marker in d for d in with_agent.get("docs", []))
    assert not any(marker in d for d in without_agent.get("docs", []))


@pytest.mark.asyncio
async def test_embedder_enables_vector_path_in_planning_context(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """Un chunk SIN solape léxico con la query pero con embedding = hash(query)
    solo puede aparecer por el path vectorial — antes el planning era BM25-only."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)

    query = "arquitectura hexagonal del billing"
    embedder = HashEmbedder()
    (query_vec,) = await embedder.embed([query])
    vec_str = "[" + ",".join(f"{x:.6f}" for x in query_vec) + "]"
    marker = "zzz contenido sin tokens compartidos qqq"

    conn = await asyncpg.connect(migrations_pg_dsn)
    doc_id, chunk_id = uuid4(), uuid4()
    try:
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, 'Vec doc', 'vec.md', 'text/markdown', 'kb/vec', 10, 'indexed')",
            doc_id,
            seeded["tenant_id"],
            seeded["kb_id"],
        )
        await conn.execute(
            "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content, embedding)"
            " VALUES ($1, $2, $3, 0, $4, $5::vector)",
            chunk_id,
            seeded["tenant_id"],
            doc_id,
            marker,
            vec_str,
        )
    finally:
        await conn.close()

    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        project = await session.get(Project, seeded["project_id"])
        with_embedder = await build_project_context(session, project, query, embedder=embedder)
        without_embedder = await build_project_context(session, project, query)
    finally:
        await session.close()
        await engine.dispose()

    assert any(marker in d for d in with_embedder.get("docs", []))
    assert not any(marker in d for d in without_embedder.get("docs", []))


# ===========================================================================
# P1-11b: backfill de embeddings de CHUNKS — la ingesta deja embedding=NULL si
# Ollama falla al crear, y el re-embed nunca existió (solo el de memorias).
# ===========================================================================
@pytest.mark.asyncio
async def test_chunk_embedding_backfill_fills_nulls(schema_at_head, migrations_pg_dsn: str) -> None:
    import asyncpg as _asyncpg
    from workers.config import Settings as WorkerSettings
    from workers.maintenance import _backfill_chunk_embeddings_async

    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await seed_rag_corpus(migrations_pg_dsn)

    # Un chunk SIN embedding (la ingesta con Ollama caído).
    conn = await _asyncpg.connect(migrations_pg_dsn)
    doc_id, chunk_id = uuid4(), uuid4()
    try:
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, 'Null doc', 'n.md', 'text/markdown', 'kb/n', 10, 'indexed')",
            doc_id,
            seeded["tenant_id"],
            seeded["kb_id"],
        )
        await conn.execute(
            "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content)"
            " VALUES ($1, $2, $3, 0, 'contenido sin vector')",
            chunk_id,
            seeded["tenant_id"],
            doc_id,
        )
    finally:
        await conn.close()

    import os

    result = await _backfill_chunk_embeddings_async(
        settings=(
            WorkerSettings(database_url=os.environ["WORKERS_TEST_ADMIN_URL"])
            if os.environ.get("WORKERS_TEST_ADMIN_URL")
            else WorkerSettings(
                database_url=os.environ.get(
                    "API_SERVER_ADMIN_DATABASE_URL",
                    "postgresql+asyncpg://migrations_user:changeme-migrations-dev-only"
                    "@localhost:15432/agentic_platform_test",
                )
            )
        ),
        embedder_factory=lambda _s: HashEmbedder(),
    )

    assert result["updated"] >= 1
    conn = await _asyncpg.connect(migrations_pg_dsn)
    try:
        remaining = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE id = $1 AND embedding IS NULL", chunk_id
        )
    finally:
        await conn.close()
    assert remaining == 0
