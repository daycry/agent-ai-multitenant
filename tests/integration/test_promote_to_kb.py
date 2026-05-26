"""Integration test for `promote_to_kb` (Plan 04 task_04_23).

Exercises the path "convert in-flight → keep in the KB" end-to-end
against the real Postgres + the in-memory storage. The convert step
is faked with a :class:`StaticDoclingMCPClient`; the embedder is the
deterministic :class:`HashEmbedder`.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.db.knowledge import Chunk, Document
from api_server.ingestion import (
    ConvertResult,
    DoclingChunk,
    PromotionError,
    document_convert,
    promote_to_kb,
)
from api_server.ingestion.docling_mcp import StaticDoclingMCPClient
from api_server.ingestion.embeddings import HashEmbedder
from api_server.storage import InMemoryObjectStorage
from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_tenant_and_kb(dsn: str) -> dict[str, UUID]:
    tenant_id = uuid4()
    kb_id = uuid4()
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
            "Tenant Promote",
            "tenant-promote",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-promote",
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, $3)",
            kb_id,
            tenant_id,
            "KB Promote",
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "kb_id": kb_id}


async def _open_session(app_database_url: str, tenant_id: UUID):
    engine = create_async_engine(app_database_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    session = session_factory()
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(tenant_id)},
    )
    return engine, session


# ---------------------------------------------------------------------------
# Happy path: convert → promote → assert rows
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_convert_then_promote_persists_document_and_chunks(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed_tenant_and_kb(migrations_pg_dsn)

    raw = b"%PDF-1.4 dummy bytes"
    mcp_client = StaticDoclingMCPClient(
        chunks=[
            DoclingChunk(
                ordinal=0,
                content="Introduction.",
                bbox={"page": 0, "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.05},
                metadata={"heading": "Intro"},
            ),
            DoclingChunk(
                ordinal=1,
                content="Chapter 1 about asyncpg.",
                bbox={"page": 1, "x": 0.1, "y": 0.2, "w": 0.8, "h": 0.05},
                metadata={"heading": "Ch 1"},
            ),
        ]
    )

    converted = await document_convert(
        filename="manual.pdf",
        content_type="application/pdf",
        data=raw,
        client=mcp_client,
    )
    assert converted.page_count == 2

    storage = InMemoryObjectStorage()
    engine, session = await _open_session(app_database_url, seeded["tenant_id"])
    try:
        result = await promote_to_kb(
            session,
            convert_result=converted,
            tenant_id=seeded["tenant_id"],
            kb_id=seeded["kb_id"],
            raw_bytes=raw,
            storage=storage,
            embedder=HashEmbedder(),
            title="Manual (promoted)",
        )
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()

    assert result.chunks_persisted == 2
    assert result.chunks_embedded == 2

    # Out-of-band assertions on the rows.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        doc = await conn.fetchrow(
            "SELECT title, status, page_count, source_storage_key,"
            "       source_size_bytes, indexed_at, kb_id"
            " FROM documents WHERE id = $1",
            result.document_id,
        )
        assert doc is not None
        assert doc["title"] == "Manual (promoted)"
        assert doc["status"] == "indexed"
        assert doc["page_count"] == 2
        assert doc["source_size_bytes"] == len(raw)
        assert doc["indexed_at"] is not None
        # Canonical key shape (matches the upload endpoint).
        assert doc["source_storage_key"].startswith(
            f"kb/{seeded['tenant_id']}/{seeded['kb_id']}/{result.document_id}/"
        )
        assert doc["source_storage_key"].endswith("/manual.pdf")
        assert doc["kb_id"] == seeded["kb_id"]

        chunks = await conn.fetch(
            "SELECT ordinal, content, embedding IS NOT NULL AS has_embedding, bbox"
            " FROM chunks WHERE document_id = $1 ORDER BY ordinal",
            result.document_id,
        )
        assert len(chunks) == 2
        assert chunks[0]["content"].startswith("Introduction")
        assert chunks[0]["has_embedding"] is True
        # bbox is a JSON object; both chunks carry a page key.
        import json as _json

        bb = chunks[1]["bbox"]
        if isinstance(bb, str):
            bb = _json.loads(bb)
        assert bb["page"] == 1
    finally:
        await conn.close()

    # The raw bytes really landed in storage.
    assert await storage.object_exists(key=doc["source_storage_key"]) is True
    assert await storage.get_object(key=doc["source_storage_key"]) == raw


# ---------------------------------------------------------------------------
# No embedder → BM25-only (embeddings NULL)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_promote_without_embedder_keeps_embeddings_null(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed_tenant_and_kb(migrations_pg_dsn)

    converted = ConvertResult(
        filename="notes.md",
        content_type="text/markdown",
        chunks=[DoclingChunk(ordinal=0, content="Plain text.")],
        page_count=0,
    )

    storage = InMemoryObjectStorage()
    engine, session = await _open_session(app_database_url, seeded["tenant_id"])
    try:
        result = await promote_to_kb(
            session,
            convert_result=converted,
            tenant_id=seeded["tenant_id"],
            kb_id=seeded["kb_id"],
            raw_bytes=b"plain text",
            storage=storage,
            embedder=None,
        )
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()

    assert result.chunks_persisted == 1
    assert result.chunks_embedded == 0


# ---------------------------------------------------------------------------
# KB does not exist
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_promote_to_missing_kb_raises(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed_tenant_and_kb(migrations_pg_dsn)

    converted = ConvertResult(
        filename="x.md",
        content_type="text/markdown",
        chunks=[DoclingChunk(ordinal=0, content="x")],
    )

    engine, session = await _open_session(app_database_url, seeded["tenant_id"])
    try:
        with pytest.raises(PromotionError, match="not found"):
            await promote_to_kb(
                session,
                convert_result=converted,
                tenant_id=seeded["tenant_id"],
                kb_id=uuid4(),  # never created
                raw_bytes=b"x",
                storage=InMemoryObjectStorage(),
            )
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# Cross-tenant promotion is rejected
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_promote_rejects_cross_tenant_kb(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed_tenant_and_kb(migrations_pg_dsn)
    other_tenant = uuid4()

    converted = ConvertResult(
        filename="x.md",
        content_type="text/markdown",
        chunks=[DoclingChunk(ordinal=0, content="x")],
    )

    engine, session = await _open_session(app_database_url, seeded["tenant_id"])
    try:
        # RLS will hide the KB from a session bound to a different
        # tenant; the explicit check inside promote_to_kb fires the
        # same way (kb appears as None → PromotionError).
        with pytest.raises(PromotionError):
            await promote_to_kb(
                session,
                convert_result=converted,
                tenant_id=other_tenant,
                kb_id=seeded["kb_id"],
                raw_bytes=b"x",
                storage=InMemoryObjectStorage(),
            )
    finally:
        await session.close()
        await engine.dispose()


# ---------------------------------------------------------------------------
# ORM round-trip
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_orm_can_load_promoted_document_and_chunks(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    """Smoke: the rows promote_to_kb writes are visible to the
    SQLAlchemy models the rest of the system reads with."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed_tenant_and_kb(migrations_pg_dsn)

    converted = ConvertResult(
        filename="orm.md",
        content_type="text/markdown",
        chunks=[
            DoclingChunk(ordinal=0, content="One"),
            DoclingChunk(ordinal=1, content="Two"),
        ],
    )

    engine, session = await _open_session(app_database_url, seeded["tenant_id"])
    try:
        result = await promote_to_kb(
            session,
            convert_result=converted,
            tenant_id=seeded["tenant_id"],
            kb_id=seeded["kb_id"],
            raw_bytes=b"hello",
            storage=InMemoryObjectStorage(),
            embedder=HashEmbedder(),
        )
        await session.commit()

        # Re-read through the ORM in the same session.
        doc = (
            await session.execute(select(Document).where(Document.id == result.document_id))
        ).scalar_one()
        assert doc.status == "indexed"

        chunks = (
            (
                await session.execute(
                    select(Chunk)
                    .where(Chunk.document_id == result.document_id)
                    .order_by(Chunk.ordinal)
                )
            )
            .scalars()
            .all()
        )
        assert [c.content for c in chunks] == ["One", "Two"]
    finally:
        await session.close()
        await engine.dispose()
