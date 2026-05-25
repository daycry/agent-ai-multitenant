"""Audio ingestion via Whisper-backed docling-serve (Plan 04 task_04_12).

The roadmap promises audio routing through the same docling-serve
convert API. From the platform's point of view that's just another
mime type — the ingestion pipeline doesn't branch on it. So this
test asserts the *contract*:

  - the worker forwards the original audio mime type to the docling
    client (so docling-serve picks the right backend),
  - the resulting chunks (Whisper transcript segments) are persisted
    just like any other document's chunks,
  - the document ends up `indexed` with `page_count=0` (audio is
    unpaginated).

The real Whisper run lives inside docling-serve; we exercise the
*wire* with a `StaticDoclingClient` so the test is deterministic.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.ingestion import DoclingChunk, NullAntivirus, ingest_document
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


async def _seed_audio_doc(dsn: str) -> dict[str, UUID | str]:
    tenant_id = uuid4()
    kb_id = uuid4()
    document_id = uuid4()
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
            "Tenant Audio",
            "tenant-audio",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-audio",
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, $3)",
            kb_id,
            tenant_id,
            "KB Audio",
        )
        storage_key = f"kb/{tenant_id}/{kb_id}/{document_id}/talk.wav"
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')",
            document_id,
            tenant_id,
            kb_id,
            "Conference Talk",
            "talk.wav",
            "audio/wav",
            storage_key,
            1024,
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "kb_id": kb_id,
        "document_id": document_id,
        "storage_key": storage_key,
    }


@pytest.mark.asyncio
async def test_audio_mime_type_routes_through_pipeline(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed_audio_doc(migrations_pg_dsn)
    audio_bytes = b"RIFF\x00\x00\x00\x00WAVE fake audio payload"

    storage = InMemoryObjectStorage()
    await storage.put_object(
        key=str(seeded["storage_key"]),
        data=audio_bytes,
        content_type="audio/wav",
    )

    # docling-serve would return Whisper segments; we stand in with
    # two transcript-like chunks (no bbox — audio is unpaginated).
    docling = StaticDoclingClient(
        chunks=[
            DoclingChunk(
                ordinal=0,
                content="Welcome to the conference. Today we cover RAG.",
                bbox=None,
                metadata={"start_ms": 0, "end_ms": 3500, "speaker": "S1"},
            ),
            DoclingChunk(
                ordinal=1,
                content="The first speaker discusses vector indexes.",
                bbox=None,
                metadata={"start_ms": 3500, "end_ms": 8000, "speaker": "S1"},
            ),
        ]
    )

    engine = create_async_engine(app_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(seeded["tenant_id"])},
    )
    try:
        result = await ingest_document(
            session,
            document_id=UUID(str(seeded["document_id"])),
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

    # The wire: docling-serve received the audio mime type verbatim.
    assert docling.calls == [
        {"filename": "talk.wav", "content_type": "audio/wav", "size": len(audio_bytes)}
    ]

    # Out-of-band DB check: page_count stays 0 for unpaginated audio,
    # and the Whisper-style metadata survives on each chunk.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        doc_row = await conn.fetchrow(
            "SELECT page_count, status FROM documents WHERE id = $1",
            seeded["document_id"],
        )
        assert doc_row["page_count"] == 0
        assert doc_row["status"] == "indexed"
        chunk_metas = await conn.fetch(
            "SELECT metadata FROM chunks WHERE document_id = $1 ORDER BY ordinal",
            seeded["document_id"],
        )
        # First chunk's metadata round-trips through JSONB unchanged.
        meta = chunk_metas[0]["metadata"]
        # asyncpg returns JSONB as raw text by default; tolerate both.
        if isinstance(meta, str):
            import json

            meta = json.loads(meta)
        assert meta["speaker"] == "S1"
        assert meta["start_ms"] == 0
    finally:
        await conn.close()
