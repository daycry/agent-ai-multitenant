"""Antivirus scan before ingestion (Plan 04 task_04_13).

The pipeline runs the AV scan **before** it hands bytes to docling.
A positive hit flips the document to ``failed`` with the signature
on `error_message` and never calls docling-serve — that's the safety
property we lock in here.

The real ClamAV client lives in `api_server.ingestion.antivirus` and
talks INSTREAM TCP to clamd. For unit-style integration we use
:class:`StubAntivirus` which returns ``INFECTED`` on the EICAR test
string and CLEAN otherwise — deterministic and safe (EICAR is the
industry-standard test sample, not malware).
"""

from __future__ import annotations

from uuid import UUID, uuid4

import asyncpg
import pytest
from alembic import command
from api_server.ingestion import (
    DoclingChunk,
    ingest_document,
)
from api_server.ingestion.antivirus import (
    EICAR_TEST_STRING,
    NullAntivirus,
    StubAntivirus,
)
from api_server.ingestion.embeddings import HashEmbedder
from api_server.storage import InMemoryObjectStorage
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration

_PLATFORM_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


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
            " memory_entries, plans, conversations, projects, agents, teams,"
            " user_org_memberships, organizations, users"
            " RESTART IDENTITY CASCADE"
        )
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3), ($4, $5, $6)",
            tenant_id,
            "Tenant AV",
            "tenant-av",
            _PLATFORM_TENANT_ID,
            "Platform",
            "platform-av",
        )
        await conn.execute(
            "INSERT INTO knowledge_bases (id, tenant_id, name) VALUES ($1, $2, $3)",
            kb_id,
            tenant_id,
            "KB AV",
        )
        storage_key = f"kb/{tenant_id}/{kb_id}/{document_id}/upload.bin"
        await conn.execute(
            "INSERT INTO documents"
            " (id, tenant_id, kb_id, title, source_filename, source_mime_type,"
            "  source_storage_key, source_size_bytes, status)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, 'pending')",
            document_id,
            tenant_id,
            kb_id,
            "Upload",
            "upload.bin",
            "application/octet-stream",
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


async def _run_pipeline(
    seeded: dict[str, UUID | str],
    *,
    app_database_url: str,
    payload: bytes,
    antivirus,
    docling,
):
    storage = InMemoryObjectStorage()
    await storage.put_object(
        key=str(seeded["storage_key"]),
        data=payload,
        content_type="application/octet-stream",
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
            antivirus=antivirus,
            docling=docling,
            embedder=HashEmbedder(),
            redis=None,
        )
        await session.commit()
    finally:
        await session.close()
        await engine.dispose()
    return result


# ---------------------------------------------------------------------------
# Infected payload
# ---------------------------------------------------------------------------
class _RecordingDocling:
    def __init__(self) -> None:
        self.calls = 0

    async def convert(self, **kwargs):  # type: ignore[no-untyped-def]
        self.calls += 1
        return [DoclingChunk(ordinal=0, content="should never appear")]

    async def aclose(self) -> None:  # pragma: no cover
        pass


@pytest.mark.asyncio
async def test_infected_payload_blocks_before_docling(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    payload = b"prefix " + EICAR_TEST_STRING.encode("ascii") + b" suffix"
    seeded = await _seed(migrations_pg_dsn, payload=payload)
    docling = _RecordingDocling()

    result = await _run_pipeline(
        seeded,
        app_database_url=app_database_url,
        payload=payload,
        antivirus=StubAntivirus(),
        docling=docling,
    )

    # Document flipped to failed; docling-serve never received bytes.
    assert result.status == "failed"
    assert "antivirus hit" in (result.error_message or "")
    assert "EICAR-TEST" in (result.error_message or "")
    assert docling.calls == 0

    # No chunks landed.
    conn = await asyncpg.connect(migrations_pg_dsn)
    try:
        n = await conn.fetchval(
            "SELECT count(*) FROM chunks WHERE document_id = $1", seeded["document_id"]
        )
        assert n == 0
        row = await conn.fetchrow(
            "SELECT status, error_message FROM documents WHERE id = $1",
            seeded["document_id"],
        )
        assert row["status"] == "failed"
        assert "EICAR-TEST" in row["error_message"]
    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Clean payload — control case
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_clean_payload_reaches_docling(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    payload = b"perfectly innocent bytes"
    seeded = await _seed(migrations_pg_dsn, payload=payload)
    docling = _RecordingDocling()

    result = await _run_pipeline(
        seeded,
        app_database_url=app_database_url,
        payload=payload,
        antivirus=StubAntivirus(),
        docling=docling,
    )
    assert result.status == "indexed"
    assert docling.calls == 1


# ---------------------------------------------------------------------------
# Null AV (the EICAR string is harmless with the scanner disabled)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_null_antivirus_does_not_block_anything(
    schema_at_head, migrations_pg_dsn: str, app_database_url: str
) -> None:
    from tests.integration.conftest import _grant_app_user_existing_tables

    await _grant_app_user_existing_tables()
    payload = EICAR_TEST_STRING.encode("ascii")
    seeded = await _seed(migrations_pg_dsn, payload=payload)
    docling = _RecordingDocling()

    result = await _run_pipeline(
        seeded,
        app_database_url=app_database_url,
        payload=payload,
        antivirus=NullAntivirus(),
        docling=docling,
    )
    # With AV bypassed, the pipeline runs to completion.
    assert result.status == "indexed"
    assert docling.calls == 1
