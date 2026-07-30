"""prod-12 task_prod12_av_01 (ADR 0105) — antivirus fail-closed en la ingesta.

Con el backend AV caído (`AntivirusVerdict.ERROR`):
  * fail_closed (default): el documento queda en ``pending_scan`` (NO se
    indexa, docling ni se llama) y el sweep de pendientes lo re-encola;
  * fail_open (solo dev/sandbox): comportamiento anterior — indexa con warning.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.ingestion import DoclingChunk, ingest_document
from api_server.ingestion.antivirus import AntivirusReport, AntivirusVerdict
from api_server.ingestion.embeddings import HashEmbedder
from api_server.storage import InMemoryObjectStorage
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


class _DownAntivirus:
    """Backend caído: siempre ERROR (clamd inalcanzable)."""

    async def scan(self, *, filename: str, data: bytes) -> AntivirusReport:
        return AntivirusReport(
            verdict=AntivirusVerdict.ERROR, signature=None, message="clamd unreachable"
        )


class _RecordingDocling:
    def __init__(self) -> None:
        self.calls = 0

    async def convert(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        return [DoclingChunk(ordinal=0, content="contenido")]

    async def aclose(self) -> None:  # pragma: no cover
        pass


@pytest.fixture()
def schema_at_head(alembic_config) -> None:
    command.upgrade(alembic_config, "head")


async def _seed(dsn: str, *, payload: bytes) -> dict[str, UUID | str]:
    tenant_id = uuid4()
    kb_id = uuid4()
    document_id = uuid4()
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            "TRUNCATE kb_projects, chunks, documents, knowledge_bases,"
            " user_org_memberships, organizations, users RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'T AV', 't-avfc'),"
            " ($2, 'Platform', 'platform-avfc')",
            tenant_id,
            _PLATFORM_TENANT_ID,
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, 'KB')",
            kb_id,
            tenant_id,
        )
        storage_key = f"kb/{tenant_id}/{kb_id}/{document_id}/doc.bin"
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, 'Doc', 'doc.bin', 'application/octet-stream', $4, $5, 'pending')",
            document_id,
            tenant_id,
            kb_id,
            storage_key,
            len(payload),
        )
    finally:
        await conn.close()
    return {"tenant_id": tenant_id, "document_id": document_id, "storage_key": storage_key}


async def _run(seeded, *, app_database_url: str, payload: bytes, docling, av_failure_mode: str):
    storage = InMemoryObjectStorage()
    await storage.put_object(
        key=str(seeded["storage_key"]), data=payload, content_type="application/octet-stream"
    )
    engine = create_async_engine(app_database_url)
    session = async_sessionmaker(engine, expire_on_commit=False)()
    await session.execute(
        sa_text("SELECT set_config('app.tenant_id', :tid, false)"),
        {"tid": str(seeded["tenant_id"])},
    )
    try:
        result = await ingest_document(
            session,
            document_id=UUID(str(seeded["document_id"])),
            storage=storage,
            antivirus=_DownAntivirus(),
            docling=docling,
            embedder=HashEmbedder(),
            redis=None,
            av_failure_mode=av_failure_mode,
        )
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()
    return result


async def _doc_status(dsn: str, document_id) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        return str(await conn.fetchval("SELECT status FROM documents WHERE id = $1", document_id))
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_fail_closed_parks_the_document_in_pending_scan(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    payload = b"unscanned bytes"
    seeded = await _seed(migrations_pg_dsn, payload=payload)
    docling = _RecordingDocling()

    result = await _run(
        seeded,
        app_database_url=app_database_url,
        payload=payload,
        docling=docling,
        av_failure_mode="fail_closed",
    )
    assert result.status == "pending_scan"
    assert result.chunks_persisted == 0
    # Ni docling ni el índice ven bytes sin escanear.
    assert docling.calls == 0
    assert await _doc_status(migrations_pg_dsn, seeded["document_id"]) == "pending_scan"


@pytest.mark.asyncio
async def test_fail_open_keeps_the_legacy_behaviour(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    payload = b"dev sandbox bytes"
    seeded = await _seed(migrations_pg_dsn, payload=payload)
    docling = _RecordingDocling()

    result = await _run(
        seeded,
        app_database_url=app_database_url,
        payload=payload,
        docling=docling,
        av_failure_mode="fail_open",
    )
    assert result.status == "indexed"
    assert docling.calls == 1


@pytest.mark.asyncio
async def test_sweep_reenqueues_pending_scan_documents(
    schema_at_head, migrations_pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El sweep de pendientes re-encola también `pending_scan` (reescaneo
    automático al volver el antivirus)."""
    from workers.ingestion import _sweep_pending_documents_async

    payload = b"waiting for rescan"
    seeded = await _seed(migrations_pg_dsn, payload=payload)
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        await conn.execute(
            "UPDATE documents SET status = 'pending_scan',"
            " created_at = now() - interval '10 minutes' WHERE id = $1",
            seeded["document_id"],
        )
    finally:
        await conn.close()

    async_dsn = migrations_pg_dsn.replace("postgresql://", "postgresql+asyncpg://", 1)
    monkeypatch.setenv("WORKERS_DATABASE_URL", async_dsn)
    from workers.config import get_settings, reset_settings_cache

    reset_settings_cache()
    enqueued: list[str] = []

    def _record(doc_id: UUID) -> bool:
        enqueued.append(str(doc_id))
        return True

    try:
        result = await _sweep_pending_documents_async(settings=get_settings(), enqueue=_record)
    finally:
        reset_settings_cache()
    assert str(seeded["document_id"]) in enqueued
    assert result["reenqueued"] >= 1
