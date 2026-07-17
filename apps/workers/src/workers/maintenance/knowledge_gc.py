"""GC físico del conocimiento — `workers.collect_knowledge_garbage` (G-03).

El borrado de KB/documento solo soft-borraba metadatos: chunks huérfanos
eternos en Postgres y blobs sin dueño en MinIO (el audit encontró 8 ya). Este
beat reclama el espacio en dos barridos, ambos idempotentes y best-effort:

  1. **Documentos soft-borrados VENCIDOS** (`deleted_at < now - retention`):
     borra sus chunks, su blob y la fila — hard-delete tras la gracia.
  2. **Blobs `kb/**` sin fila `documents`**: un blob cuyo `{document_id}`
     (4º segmento de la clave ``kb/{tenant}/{kb}/{document}/{file}``) no existe
     ya en `documents` es basura de una KB hard-borrada / restore raro.

Corre bajo la sesión admin BYPASSRLS (mantenimiento cross-tenant); una métrica
textfile (`agentic_kb_gc_*`) reporta lo reclamado. Nunca rompe el beat.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from uuid import UUID

import structlog
from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from workers.celery_app import app

_log = structlog.get_logger("workers.maintenance")

_Sessionmaker = async_sessionmaker[AsyncSession]

_KB_BLOB_PREFIX = "kb/"
# Cota por pasada: el barrido de huérfanos es O(blobs); acotar evita una pasada
# eterna en un bucket enorme (el resto cae en la siguiente pasada del beat).
_ORPHAN_SWEEP_CAP = 1000


def _document_id_from_key(key: str) -> UUID | None:
    """El ``{document_id}`` de una clave ``kb/{tenant}/{kb}/{document}/{file}``."""
    parts = key.split("/")
    if len(parts) < 5 or parts[0] != "kb":
        return None
    try:
        return UUID(parts[3])
    except (ValueError, IndexError):
        return None


async def _purge_expired_documents(
    sessionmaker: _Sessionmaker, storage: Any, *, cutoff: datetime
) -> tuple[int, int]:
    """Barrido 1: hard-borra documentos soft-borrados vencidos (fila + chunks +
    blob). Devuelve ``(documents_purged, chunks_purged)``."""
    async with sessionmaker() as session:
        expired = (
            await session.execute(
                text(
                    "SELECT id, source_storage_key FROM documents"
                    " WHERE deleted_at IS NOT NULL AND deleted_at < :cutoff"
                ),
                {"cutoff": cutoff},
            )
        ).all()

    documents_purged = 0
    chunks_purged = 0
    for doc_id, storage_key in expired:
        try:
            async with sessionmaker() as session, session.begin():
                deleted_chunks = cast(
                    "CursorResult[Any]",
                    await session.execute(
                        text("DELETE FROM chunks WHERE document_id = :d"), {"d": doc_id}
                    ),
                )
                await session.execute(text("DELETE FROM documents WHERE id = :d"), {"d": doc_id})
            chunks_purged += int(deleted_chunks.rowcount or 0)
            documents_purged += 1
            if storage_key:
                try:
                    await storage.delete_object(key=storage_key)
                except Exception:  # blob ya ausente / storage caído → best-effort
                    _log.warning("knowledge_gc.blob_delete_failed", key=storage_key)
        except Exception:
            _log.warning("knowledge_gc.document_purge_failed", document_id=str(doc_id))
    return documents_purged, chunks_purged


async def _sweep_orphan_blobs(sessionmaker: _Sessionmaker, storage: Any) -> int:
    """Barrido 2: borra blobs ``kb/**`` cuyo ``{document_id}`` ya no tiene fila
    en ``documents``. Devuelve cuántos barrió."""
    try:
        keys = list(await storage.list_objects(prefix=_KB_BLOB_PREFIX))[:_ORPHAN_SWEEP_CAP]
    except Exception:
        _log.warning("knowledge_gc.list_objects_failed")
        return 0
    by_doc: dict[UUID, list[str]] = {}
    for key in keys:
        did = _document_id_from_key(key)
        if did is not None:
            by_doc.setdefault(did, []).append(key)
    if not by_doc:
        return 0
    async with sessionmaker() as session:
        present = {
            row[0]
            for row in (
                await session.execute(
                    text("SELECT id FROM documents WHERE id = ANY(:ids)"),
                    {"ids": list(by_doc)},
                )
            ).all()
        }
    swept = 0
    for did, doc_keys in by_doc.items():
        if did in present:
            continue
        for key in doc_keys:
            try:
                await storage.delete_object(key=key)
                swept += 1
            except Exception:
                _log.warning("knowledge_gc.orphan_delete_failed", key=key)
    return swept


async def collect_knowledge_garbage(
    sessionmaker: _Sessionmaker,
    storage: Any,
    *,
    retention_days: int = 30,
    now: datetime | None = None,
) -> dict[str, int]:
    """Purga documentos soft-borrados vencidos + barre blobs huérfanos.

    Devuelve ``{"documents_purged": int, "chunks_purged": int,
    "orphan_blobs_swept": int}``. Best-effort por elemento: un fallo de blob
    no impide reclamar la fila (y viceversa)."""
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(days=retention_days)

    documents_purged, chunks_purged = await _purge_expired_documents(
        sessionmaker, storage, cutoff=cutoff
    )
    orphan_blobs_swept = await _sweep_orphan_blobs(sessionmaker, storage)

    result = {
        "documents_purged": documents_purged,
        "chunks_purged": chunks_purged,
        "orphan_blobs_swept": orphan_blobs_swept,
    }
    if documents_purged or orphan_blobs_swept:
        _log.info("knowledge_gc.done", **result)
    return result


@app.task(name="workers.collect_knowledge_garbage")  # type: ignore[untyped-decorator]
def collect_knowledge_garbage_task() -> dict[str, int]:
    """Celery entry (beat diario). Best-effort — nunca crashea beat."""
    return asyncio.run(_run())


async def _run() -> dict[str, int]:
    """Engine + storage propios, dispose garantizado. El storage sale de
    `api_server.storage.get_object_storage` (mismo MinIO que la ingesta, desde
    el env del proceso — el worker corre sobre la imagen base del api-server)."""
    from api_server.storage import get_object_storage

    from workers.config import get_settings

    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    try:
        sm = async_sessionmaker(engine, expire_on_commit=False)
        return await collect_knowledge_garbage(
            sm, get_object_storage(), retention_days=settings.knowledge_gc_retention_days
        )
    finally:
        await engine.dispose()


__all__ = ["collect_knowledge_garbage", "collect_knowledge_garbage_task"]
