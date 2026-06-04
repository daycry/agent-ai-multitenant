"""Honestidad de ingestión (Plan 06.17 task_06_17_05).

El pipeline de ingestión marcaba ``status=indexed`` de forma
incondicional, incluso cuando Docling no producía ningún chunk: un
documento "indexado vacío" aparecía verde en la UI aunque el agente
no podía consultar nada de él (RAG con 0 chunks). Además, un fallo de
parseo de Docling se registraba con ``_fail`` pero el motivo quedaba
solo en el campo ``error_message`` — esta suite pin-ea ambos contratos:

  1. **0 chunks → ``indexed_empty``** (no ``indexed``). El estado es
     honesto: el documento se procesó pero no aportó conocimiento.
     ``indexed_at`` se sella igual (sí se procesó) y ``page_count=0``.
  2. **DoclingParseError → ``failed`` con el motivo** en
     ``error_message`` (propagado, no silencioso).
  3. **Chunks reales → ``indexed``** (el feliz path no regresiona).

Reutiliza el patrón de ``test_ingestion_worker.py`` (async core con
fakes inyectados) para no necesitar docling-serve/MinIO arriba.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command

pytestmark = pytest.mark.integration


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
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Acme', 'acme-honesty')",
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


# ---------------------------------------------------------------------------
# Migración 0080: CHECK admite 'indexed_empty' y es reversible
#
# Test SÍNCRONO a propósito: `command.upgrade/downgrade` corren su propio
# `asyncio.run` (run_async_migrations), que choca con el event loop de un
# test async. Mismo patrón que `test_approval_timeout.test_migration_*`.
# ---------------------------------------------------------------------------
async def _insert_indexed_empty_doc(dsn: str) -> UUID:
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
            "INSERT INTO organizations (id, name, slug) VALUES ($1, 'Acme', 'acme-mig80')",
            tenant_id,
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, 'KB')",
            kb_id,
            tenant_id,
        )
        # La CHECK ampliada acepta 'indexed_empty'.
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, status)"
            " VALUES ($1, $2, $3, 'D', 'd.pdf', 'application/pdf', 'k/a/b/c/d.pdf', $4)",
            document_id,
            tenant_id,
            kb_id,
            "indexed_empty",
        )
    finally:
        await conn.close()
    return document_id


async def _read_doc_status(dsn: str, document_id: UUID) -> str:
    conn = await asyncpg.connect(dsn)
    try:
        return await conn.fetchval("SELECT status FROM documents WHERE id = $1", document_id)
    finally:
        await conn.close()


def test_migration_0080_check_accepts_indexed_empty_and_is_reversible(
    alembic_config, migrations_pg_dsn: str
) -> None:
    command.upgrade(alembic_config, "head")
    document_id = asyncio.run(_insert_indexed_empty_doc(migrations_pg_dsn))

    # Reversibilidad: downgrade normaliza 'indexed_empty'→'indexed' y
    # re-estrecha la CHECK; upgrade vuelve a head sin errores.
    command.downgrade(alembic_config, "-1")
    status_after_down = asyncio.run(_read_doc_status(migrations_pg_dsn, document_id))
    assert status_after_down == "indexed"
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


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
class _RaisingDoclingClient:
    """Docling fake que siempre falla — simula un parseo roto."""

    def __init__(self, message: str) -> None:
        self._message = message

    async def convert(self, *, filename: str, content_type: str, data: bytes) -> Any:
        from api_server.ingestion.docling import DoclingParseError

        raise DoclingParseError(self._message)

    async def aclose(self) -> None:  # pragma: no cover
        pass


def _fakes(docling: Any):
    from api_server.ingestion import NullAntivirus
    from api_server.ingestion.embeddings import HashEmbedder
    from api_server.storage import InMemoryObjectStorage

    return {
        "storage": InMemoryObjectStorage(),
        "docling": docling,
        "embedder": HashEmbedder(),
        "antivirus": NullAntivirus(),
    }


async def _run_pipeline(document_id: UUID, fakes: dict[str, Any], settings: Any) -> dict[str, Any]:
    from workers.ingestion import _ingest_document_async

    return await _ingest_document_async(
        document_id,
        settings=settings,
        storage_factory=lambda: fakes["storage"],
        antivirus_factory=lambda: fakes["antivirus"],
        docling_factory=lambda: fakes["docling"],
        embedder_factory=lambda: fakes["embedder"],
        redis_factory=lambda _s: None,
    )


# ---------------------------------------------------------------------------
# 1. 0 chunks → indexed_empty (no verde "indexed")
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_zero_chunks_marks_indexed_empty(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()

    payload = b"%PDF-1.4 empty"
    seeded = await _seed_pending_document(migrations_pg_dsn, payload=payload)

    from api_server.ingestion.docling import StaticDoclingClient

    fakes = _fakes(StaticDoclingClient(chunks=[]))
    await fakes["storage"].put_object(
        key=seeded["storage_key"], data=payload, content_type="application/pdf"
    )

    result = await _run_pipeline(seeded["document_id"], fakes, workers_settings)
    assert result["status"] == "indexed_empty", result
    assert result["chunks"] == 0

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        doc = await conn.fetchrow(
            "SELECT status, indexed_at, error_message, page_count" " FROM documents WHERE id = $1",
            seeded["document_id"],
        )
        n_chunks = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", seeded["document_id"]
        )
    finally:
        await conn.close()

    # Honesto: NO verde "indexed". El documento se procesó (indexed_at
    # sellado) pero no aportó conocimiento.
    assert doc["status"] == "indexed_empty"
    assert doc["status"] != "indexed"
    assert doc["indexed_at"] is not None
    assert doc["page_count"] == 0
    assert n_chunks == 0


# ---------------------------------------------------------------------------
# 2. DoclingParseError → failed con el motivo (no silencioso)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_docling_error_propagates_reason(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()

    payload = b"%PDF-1.4 corrupt"
    seeded = await _seed_pending_document(migrations_pg_dsn, payload=payload)

    fakes = _fakes(_RaisingDoclingClient("docling-serve 500: unsupported codec"))
    await fakes["storage"].put_object(
        key=seeded["storage_key"], data=payload, content_type="application/pdf"
    )

    result = await _run_pipeline(seeded["document_id"], fakes, workers_settings)
    assert result["status"] == "failed", result
    assert "unsupported codec" in (result["error"] or "")

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        doc = await conn.fetchrow(
            "SELECT status, error_message FROM documents WHERE id = $1",
            seeded["document_id"],
        )
    finally:
        await conn.close()
    assert doc["status"] == "failed"
    # El motivo concreto del fallo de Docling llega a la fila (no genérico).
    assert "unsupported codec" in (doc["error_message"] or "")


# ---------------------------------------------------------------------------
# 3. Chunks reales → indexed (el feliz path no regresiona)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_real_chunks_still_index_green(
    schema_at_head, migrations_pg_dsn: str, workers_settings
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()

    payload = b"%PDF-1.4 real"
    seeded = await _seed_pending_document(migrations_pg_dsn, payload=payload)

    from api_server.ingestion.docling import DoclingChunk, StaticDoclingClient

    fakes = _fakes(
        StaticDoclingClient(
            chunks=[DoclingChunk(ordinal=0, content="Contenido real.", bbox={"page": 0})]
        )
    )
    await fakes["storage"].put_object(
        key=seeded["storage_key"], data=payload, content_type="application/pdf"
    )

    result = await _run_pipeline(seeded["document_id"], fakes, workers_settings)
    assert result["status"] == "indexed", result
    assert result["chunks"] == 1

    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        status = await conn.fetchval(
            "SELECT status FROM documents WHERE id = $1", seeded["document_id"]
        )
    finally:
        await conn.close()
    assert status == "indexed"
