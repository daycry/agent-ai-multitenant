"""Integration tests for the ingestion pipeline (Plan 04 task_04_11).

Drives :func:`ingest_document` end-to-end against the real Postgres
schema using:

  - `InMemoryObjectStorage` for the source bytes,
  - `StaticDoclingClient` returning fixed chunks,
  - `NullAntivirus` (the scan path is exercised in
    `test_clamav_scan.py`, task_04_13),
  - `HashEmbedder` for deterministic 768-d vectors.

Asserts that a `pending` document moves to `indexed`, that the
correct number of `chunks` rows lands with bboxes preserved, and
that failures flip the document to `failed` with a useful message.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.ingestion import (
    DoclingChunk,
    DoclingParseError,
    NullAntivirus,
    ingest_document,
)
from api_server.ingestion.docling import StaticDoclingClient
from api_server.ingestion.embeddings import HashEmbedder
from api_server.storage import InMemoryObjectStorage
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_kb_document(dsn: str, *, payload: bytes) -> dict[str, UUID]:
    tenant_id = uuid4()
    kb_id = uuid4()
    document_id = uuid4()
    project_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " memory_entries, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant Ingest",
            "tenant-ingest",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-ingest",
        )
        await conn.execute(
            "INSERT INTO projects (id, tenant_id, name) VALUES ($1, $2, $3)",
            project_id,
            tenant_id,
            "Ingest Project",
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, $3)",
            kb_id,
            tenant_id,
            "KB Ingest",
        )
        storage_key = f"kb/{tenant_id}/{kb_id}/{document_id}/source.pdf"
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')",
            document_id,
            tenant_id,
            kb_id,
            "Manual",
            "manual.pdf",
            "application/pdf",
            storage_key,
            len(payload),
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "kb_id": kb_id,
        "document_id": document_id,
        "storage_key": UUID(int=0),  # unused placeholder
        "_storage_key_str": storage_key,  # type: ignore[dict-item]
    }


async def _open_tenant_session(app_database_url: str, tenant_id: UUID):
    engine = create_async_engine(app_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(tenant_id)},
    )
    return engine, session


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ingestion_pipeline_persists_chunks_and_marks_indexed(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    payload = b"%PDF-1.4 dummy pdf bytes"
    seeded = await _seed_kb_document(migrations_pg_dsn, payload=payload)

    storage = InMemoryObjectStorage()
    await storage.put_object(
        key=seeded["_storage_key_str"],  # type: ignore[arg-type]
        data=payload,
        content_type="application/pdf",
    )

    docling = StaticDoclingClient(
        chunks=[
            DoclingChunk(
                ordinal=0,
                content="Introduction. The system uses asyncpg.",
                bbox={"page": 0, "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.05},
                metadata={"heading": "Introduction"},
            ),
            DoclingChunk(
                ordinal=1,
                content="Chapter 1. RAG architecture.",
                bbox={"page": 1, "x": 0.1, "y": 0.3, "w": 0.8, "h": 0.05},
                metadata={"heading": "Chapter 1"},
            ),
        ]
    )

    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        result = await ingest_document(
            session,
            document_id=seeded["document_id"],
            storage=storage,
            antivirus=NullAntivirus(),
            docling=docling,
            embedder=HashEmbedder(),
            redis=None,
        )
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()

    assert result.status == "indexed"
    assert result.chunks_persisted == 2
    assert result.error_message is None

    # Re-check the DB out-of-band.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        doc_row = await conn.fetchrow(
            "SELECT status, page_count, error_message, indexed_at" " FROM documents WHERE id = $1",
            seeded["document_id"],
        )
        assert doc_row["status"] == "indexed"
        assert doc_row["page_count"] == 2
        assert doc_row["indexed_at"] is not None
        assert doc_row["error_message"] is None
        chunk_rows = await conn.fetch(
            "SELECT ordinal, content, embedding IS NOT NULL AS has_embedding, bbox"
            " FROM chunks WHERE document_id = $1 ORDER BY ordinal",
            seeded["document_id"],
        )
        assert len(chunk_rows) == 2
        assert chunk_rows[0]["has_embedding"] is True
        assert "asyncpg" in chunk_rows[0]["content"]
        assert chunk_rows[1]["ordinal"] == 1
    finally:
        await conn.close()

    # Docling client saw exactly one call.
    assert len(docling.calls) == 1
    assert docling.calls[0]["filename"] == "manual.pdf"


# ---------------------------------------------------------------------------
# Docling failure
# ---------------------------------------------------------------------------
class _ExplodingDoclingClient:
    async def convert(self, **kwargs):  # type: ignore[no-untyped-def]
        raise DoclingParseError("docling-serve crashed mid-parse")

    async def aclose(self) -> None:  # pragma: no cover
        pass


@pytest.mark.asyncio
async def test_docling_failure_marks_document_failed(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    payload = b"useless bytes"
    seeded = await _seed_kb_document(migrations_pg_dsn, payload=payload)

    storage = InMemoryObjectStorage()
    await storage.put_object(
        key=seeded["_storage_key_str"],  # type: ignore[arg-type]
        data=payload,
        content_type="application/pdf",
    )

    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        result = await ingest_document(
            session,
            document_id=seeded["document_id"],
            storage=storage,
            antivirus=NullAntivirus(),
            docling=_ExplodingDoclingClient(),  # type: ignore[arg-type]
            embedder=HashEmbedder(),
            redis=None,
        )
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()

    assert result.status == "failed"
    assert "docling-serve crashed" in (result.error_message or "")

    # No chunks landed.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", seeded["document_id"]
        )
        assert n == 0
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Missing source object in storage
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_source_object_marks_failed(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed_kb_document(migrations_pg_dsn, payload=b"x")

    storage = InMemoryObjectStorage()  # empty: the key isn't there
    engine, session = await _open_tenant_session(app_database_url, seeded["tenant_id"])
    try:
        result = await ingest_document(
            session,
            document_id=seeded["document_id"],
            storage=storage,
            antivirus=NullAntivirus(),
            docling=StaticDoclingClient(chunks=[]),
            embedder=HashEmbedder(),
            redis=None,
        )
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()

    assert result.status == "failed"
    assert "missing" in (result.error_message or "").lower()
