"""G-03 (auditoría proyecto 2026-07-17): GC físico del conocimiento.

`delete_kb` solo soft-borraba la KB (ni documentos, ni chunks, ni blobs) y el
«GC job» que el docstring de `delete_document` prometía no existía → chunks
huérfanos para siempre y blobs sin dueño en MinIO. El GC:
  1. purga los documentos soft-borrados vencidos (chunks + blob + fila);
  2. barre blobs `kb/**` sin fila `documents` viva (los 8 huérfanos del audit);
  3. `delete_kb` soft-borra en cascada sus documentos para que envejezcan.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = pytest.mark.integration


@pytest.fixture()
def _migrated(alembic_config: object) -> None:
    command.upgrade(alembic_config, "head")


async def _seed_doc_with_chunk(
    sm: async_sessionmaker, storage: object, *, deleted_days_ago: float | None
) -> dict[str, object]:
    tenant, kb, doc = uuid4(), uuid4(), uuid4()
    key = f"kb/{tenant}/{kb}/{doc}/manual.pdf"
    await storage.put_object(key=key, data=b"pdf", content_type="application/pdf")  # type: ignore[attr-defined]
    deleted_at = (
        None if deleted_days_ago is None else datetime.now(UTC) - timedelta(days=deleted_days_ago)
    )
    async with sm() as s, s.begin():
        await s.execute(
            text("INSERT INTO organizations (id, name, slug) VALUES (:t, 'T', :sl)"),
            {"t": tenant, "sl": f"gc-{tenant.hex[:8]}"},
        )
        await s.execute(
            text(
                "INSERT INTO knowledge_bases (id, tenant_id, name, embedding_model_id, is_builtin)"
                " VALUES (:k, :t, 'KB', 'nomic-embed-text-v1.5', false)"
            ),
            {"k": kb, "t": tenant},
        )
        await s.execute(
            text(
                "INSERT INTO documents (id, tenant_id, kb_id, title, source_filename,"
                " source_mime_type, source_storage_key, status, deleted_at)"
                " VALUES (:d, :t, :k, 'Manual', 'manual.pdf', 'application/pdf',"
                " :key, 'indexed', :del)"
            ),
            {"d": doc, "t": tenant, "k": kb, "key": key, "del": deleted_at},
        )
        await s.execute(
            text(
                "INSERT INTO chunks (id, tenant_id, document_id, ordinal, content)"
                " VALUES (:c, :t, :d, 0, 'x')"
            ),
            {"c": uuid4(), "t": tenant, "d": doc},
        )
    return {"tenant": tenant, "kb": kb, "doc": doc, "key": key}


async def _counts(sm: async_sessionmaker, doc: object) -> tuple[int, int]:
    async with sm() as s:
        docs = int(
            (
                await s.execute(text("SELECT count(*) FROM documents WHERE id = :d"), {"d": doc})
            ).scalar_one()
        )
        chunks = int(
            (
                await s.execute(
                    text("SELECT count(*) FROM chunks WHERE document_id = :d"), {"d": doc}
                )
            ).scalar_one()
        )
    return docs, chunks


@pytest.mark.asyncio
async def test_gc_purges_expired_soft_deleted_documents(
    _migrated: None, admin_database_url: str
) -> None:
    from api_server.storage.memory import InMemoryObjectStorage
    from workers.maintenance.knowledge_gc import collect_knowledge_garbage

    engine = create_async_engine(admin_database_url)
    storage = InMemoryObjectStorage()
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        old = await _seed_doc_with_chunk(sm, storage, deleted_days_ago=40)
        fresh = await _seed_doc_with_chunk(sm, storage, deleted_days_ago=1)
        alive = await _seed_doc_with_chunk(sm, storage, deleted_days_ago=None)

        result = await collect_knowledge_garbage(
            sm,
            storage,
            retention_days=30,  # type: ignore[arg-type]
        )

        # El vencido se purga (fila+chunks+blob); el reciente y el vivo intactos.
        assert await _counts(sm, old["doc"]) == (0, 0)
        assert not await storage.object_exists(key=old["key"])  # type: ignore[arg-type]
        assert await _counts(sm, fresh["doc"]) == (1, 1)
        assert await _counts(sm, alive["doc"]) == (1, 1)
        assert result["documents_purged"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_gc_sweeps_orphan_blobs_without_document_rows(
    _migrated: None, admin_database_url: str
) -> None:
    from api_server.storage.memory import InMemoryObjectStorage
    from workers.maintenance.knowledge_gc import collect_knowledge_garbage

    engine = create_async_engine(admin_database_url)
    storage = InMemoryObjectStorage()
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        alive = await _seed_doc_with_chunk(sm, storage, deleted_days_ago=None)
        # Blob sin ninguna fila documents (KB hard-borrada / restore raro).
        orphan_key = f"kb/{uuid4()}/{uuid4()}/{uuid4()}/ghost.pdf"
        await storage.put_object(key=orphan_key, data=b"x", content_type="application/pdf")

        result = await collect_knowledge_garbage(
            sm,
            storage,
            retention_days=30,  # type: ignore[arg-type]
        )

        assert not await storage.object_exists(key=orphan_key)  # huérfano barrido
        assert await storage.object_exists(key=alive["key"])  # type: ignore[arg-type]  # el vivo NO
        assert result["orphan_blobs_swept"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_delete_kb_soft_deletes_its_documents(
    _migrated: None, admin_database_url: str
) -> None:
    """`delete_kb` (repo helper) soft-borra sus documentos para que el GC los
    recoja — antes quedaban vivos bajo una KB muerta, invisibles pero eternos."""
    from api_server.db.knowledge_gc import soft_delete_kb_cascade

    engine = create_async_engine(admin_database_url)
    from api_server.storage.memory import InMemoryObjectStorage

    storage = InMemoryObjectStorage()
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        seeded = await _seed_doc_with_chunk(sm, storage, deleted_days_ago=None)
        async with sm() as s, s.begin():
            await soft_delete_kb_cascade(s, kb_id=seeded["kb"])
        async with sm() as s:
            doc_deleted = (
                await s.execute(
                    text("SELECT deleted_at FROM documents WHERE id = :d"), {"d": seeded["doc"]}
                )
            ).scalar_one()
        assert doc_deleted is not None
    finally:
        await engine.dispose()
