"""Ingestion worker wiring (Plan 06.11 task_06_11_01).

The pipeline `ingest_document` existed but had no production caller —
uploaded documents sat in `pending` forever. This pins the Celery
adapter that closes the gap:

  - the async core `_ingest_document_async` runs the pipeline against
    the real DB with injected fakes (storage / docling / embedder /
    antivirus), commits, and returns a JSON-safe dict;
  - `trigger_ingestion` fires `apply_async` and swallows broker errors
    (an upload must still return 201 if the broker is down);
  - `sweep_pending_documents` re-enqueues documents stuck in `pending`
    (the safety net for a missed enqueue).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------
async def _seed_pending_document(dsn: str, *, payload: bytes) -> dict[str, Any]:
    tenant_id = uuid4()
    kb_id = uuid4()
    document_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " memory_entries, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Acme', 'acme-ingw')",
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, 'KB')",
            kb_id,
            tenant_id,
        )
        storage_key = f"kb/{tenant_id}/{kb_id}/{document_id}/source.pdf"
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, 'Doc', 'manual.pdf', 'application/pdf', $4, $5, 'pending')",
            document_id,
            tenant_id,
            kb_id,
            storage_key,
            len(payload),
        )
    finally:
        await conn.close()
    return {
        "tenant_id": tenant_id,
        "kb_id": kb_id,
        "document_id": document_id,
        "storage_key": storage_key,
    }


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


@pytest.fixture()
def workers_settings(monkeypatch: pytest.MonkeyPatch, migrations_pg_dsn: str):
    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import reset_settings_cache

    reset_settings_cache()
    from workers.config import get_settings

    yield get_settings()
    reset_settings_cache()


def _fakes(chunks):
    from api_server.ingestion import NullAntivirus
    from api_server.ingestion.docling import StaticDoclingClient
    from api_server.ingestion.embeddings import HashEmbedder
    from api_server.storage import InMemoryObjectStorage

    storage = InMemoryObjectStorage()
    return {
        "storage": storage,
        "docling": StaticDoclingClient(chunks=chunks),
        "embedder": HashEmbedder(),
        "antivirus": NullAntivirus(),
    }


# ---------------------------------------------------------------------------
# Happy path: pending → indexed with chunks
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_ingestion_worker_indexes_pending_document(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()

    payload = b"%PDF-1.4 dummy"
    seeded = await _seed_pending_document(migrations_pg_dsn, payload=payload)

    from api_server.ingestion.docling import DoclingChunk

    fakes = _fakes(
        chunks=[
            DoclingChunk(ordinal=0, content="Intro. asyncpg usage.", bbox={"page": 0}),
            DoclingChunk(ordinal=1, content="Chapter 1. RAG.", bbox={"page": 1}),
        ]
    )
    await fakes["storage"].put_object(
        key=seeded["storage_key"], data=payload, content_type="application/pdf"
    )

    from workers.ingestion import _ingest_document_async

    result = await _ingest_document_async(
        seeded["document_id"],
        settings=workers_settings,
        storage_factory=lambda: fakes["storage"],
        antivirus_factory=lambda: fakes["antivirus"],
        docling_factory=lambda: fakes["docling"],
        embedder_factory=lambda: fakes["embedder"],
        redis_factory=lambda _s: None,
    )

    assert result["status"] == "indexed", result
    assert result["chunks"] == 2

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        doc = await conn.fetchrow(
            "SELECT status, indexed_at FROM documents WHERE id = $1", seeded["document_id"]
        )
        n_chunks = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", seeded["document_id"]
        )
    finally:
        await conn.close()
    assert doc["status"] == "indexed"
    assert doc["indexed_at"] is not None
    assert n_chunks == 2


@pytest.mark.asyncio
async def test_ingestion_worker_missing_document_is_clean(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    """A vanished document returns a clean error result, never raises."""
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    fakes = _fakes(chunks=[])

    from workers.ingestion import _ingest_document_async

    result = await _ingest_document_async(
        uuid4(),
        settings=workers_settings,
        storage_factory=lambda: fakes["storage"],
        antivirus_factory=lambda: fakes["antivirus"],
        docling_factory=lambda: fakes["docling"],
        embedder_factory=lambda: fakes["embedder"],
        redis_factory=lambda _s: None,
    )
    assert result["status"] in {"error", "not_found"}


# ---------------------------------------------------------------------------
# Sweep: re-enqueue pending documents
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sweep_reenqueues_pending(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    seeded = await _seed_pending_document(migrations_pg_dsn, payload=b"x")

    enqueued: list[UUID] = []

    from workers.ingestion import _sweep_pending_documents_async

    result = await _sweep_pending_documents_async(
        settings=workers_settings,
        enqueue=lambda doc_id: (enqueued.append(doc_id), True)[1],
        older_than_seconds=0,
    )
    assert seeded["document_id"] in enqueued
    assert result["reenqueued"] >= 1


# ---------------------------------------------------------------------------
# Trigger: apply_async on enqueue, swallow broker errors
# ---------------------------------------------------------------------------
def test_trigger_ingestion_fires_apply_async(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers import ingestion as mod

    calls: list[Any] = []
    monkeypatch.setattr(
        mod.ingest_document_task,
        "apply_async",
        lambda *a, **k: calls.append(k.get("args")),
    )
    doc_id = uuid4()
    assert mod.trigger_ingestion(doc_id) is True
    assert len(calls) == 1


def test_trigger_ingestion_swallows_broker_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers import ingestion as mod

    def _boom(*a: Any, **k: Any) -> None:
        raise RuntimeError("broker down")

    monkeypatch.setattr(mod.ingest_document_task, "apply_async", _boom)
    assert mod.trigger_ingestion(uuid4()) is False
