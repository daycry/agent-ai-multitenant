"""Ingestion pipeline orchestrator (Plan 04 Fase C).

Glues the four injected dependencies (storage, antivirus, docling
client, embedder) into one async function:

  1. mark document `processing` + emit a `document.status` event,
  2. read bytes from storage,
  3. AV scan — fail fast on `INFECTED`,
  4. docling-serve parse + chunk,
  5. embed chunks in bounded batches (:data:`EMBED_BATCH_SIZE`),
  6. persist `chunks` rows and update `documents` (`indexed_at`,
     `page_count`, `status`),
  7. emit a final `document.status` event with the count.

The terminal status is **honest** (Plan 06.17 task_06_17_05):

  - ``indexed``        — chunks were produced and persisted;
  - ``indexed_empty``  — the document parsed cleanly but yielded ZERO
    chunks (e.g. an image-only PDF, an unsupported layout). It is NOT
    green ``indexed`` because the agent cannot retrieve anything from
    it; the UI surfaces "indexado vacío" so the operator re-uploads;
  - ``failed``         — a hard failure (storage / AV / Docling parse
    error). The Docling failure reason is propagated to
    ``error_message`` (no silent ``_fail``).

The pipeline never raises into the Celery worker — Celery's retry is
reserved for infrastructure failures, not parse errors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.knowledge import Chunk, Document
from api_server.events import (
    EVENT_DOCUMENT_PROGRESS,
    EVENT_DOCUMENT_STATUS,
    publish_document_event,
)
from api_server.ingestion.antivirus import AntivirusScanner, AntivirusVerdict
from api_server.ingestion.docling import DoclingClient, DoclingParseError
from api_server.ingestion.embeddings import Embedder, EmbeddingError
from api_server.storage import ObjectStorage

logger = structlog.get_logger(__name__)

# Chunks por petición al embedder (prod-13 task_prod13_16, hallazgo perf-4).
#
# La ingesta embebía TODOS los chunks del documento en una sola llamada. Con un
# manual de cientos de páginas eso son miles de textos en una petición: tarda
# minutos, carga el cuerpo entero en memoria a los dos lados y, si revienta, deja
# el documento COMPLETO sin vector — el hueco "verde en la UI, invisible para el
# RAG vectorial". Troceando, el fallo se acota al lote y el resto del documento
# queda recuperable por vector desde ya; el backfill
# (``workers.backfill_chunk_embeddings``) rellena los NULL que queden.
#
# 64 es el valor que fija el plan: suficientemente grande para no convertir un
# documento normal en decenas de round-trips, suficientemente pequeño para que un
# lote quepa holgado en la petición de Ollama.
EMBED_BATCH_SIZE = 64


@dataclass(frozen=True)
class IngestionResult:
    """Outcome of one `ingest_document` call.

    Returned to the Celery task so the orchestrator can decide whether
    to retry, alert or move on. ``status`` matches the value the
    pipeline wrote on `documents.status`.
    """

    document_id: UUID
    status: str  # 'indexed' | 'indexed_empty' | 'failed' | 'pending_scan'
    chunks_persisted: int
    error_message: str | None


async def ingest_document(
    session: AsyncSession,
    *,
    document_id: UUID,
    storage: ObjectStorage,
    antivirus: AntivirusScanner,
    docling: DoclingClient,
    embedder: Embedder,
    redis: Redis | None = None,
    av_failure_mode: str = "fail_closed",
) -> IngestionResult:
    """End-to-end ingestion. The session is the tenant-scoped one the
    Celery wrapper opens; we own the lifecycle of the row but commit
    at the very end so a failure mid-flight rolls everything back."""
    doc = await _load_document(session, document_id)

    await _set_status(session, doc, "processing", error=None, redis=redis)

    # 1. fetch bytes
    try:
        data = await storage.get_object(key=doc.source_storage_key)
    except KeyError:
        return await _fail(session, doc, "source object missing in storage", redis)
    except Exception as exc:
        return await _fail(session, doc, f"storage read failed: {exc}", redis)

    # 2. antivirus scan
    av_report = await antivirus.scan(filename=doc.source_filename, data=data)
    if av_report.verdict == AntivirusVerdict.INFECTED:
        msg = f"antivirus hit: {av_report.signature or 'unknown'}"
        return await _fail(session, doc, msg, redis)
    if av_report.verdict == AntivirusVerdict.ERROR:
        if av_failure_mode != "fail_open":
            # prod-12 av_01 / ADR 0105 (api-1): fail-CLOSED por defecto — un
            # documento sin escanear NO se indexa. Queda en `pending_scan` y el
            # sweep de pendientes lo reintenta cuando ClamAV vuelva.
            msg = f"antivirus unavailable: {av_report.message or 'backend error'}"
            logger.warning(
                "ingestion.antivirus_unavailable",
                document_id=str(doc.id),
                message=av_report.message,
            )
            await _set_status(session, doc, "pending_scan", error=msg[:2000], redis=redis)
            return IngestionResult(
                document_id=doc.id,
                status="pending_scan",
                chunks_persisted=0,
                error_message=msg[:2000],
            )
        # fail_open (solo dev/sandbox): indexar con warning, como antes.
        logger.warning(
            "ingestion.antivirus_error",
            document_id=str(doc.id),
            message=av_report.message,
        )

    await _emit_progress(redis, doc.id, stage="scanned", detail=av_report.message or "")

    # 3. parse + chunk via docling-serve
    try:
        docling_chunks = await docling.convert(
            filename=doc.source_filename,
            content_type=doc.source_mime_type,
            data=data,
        )
    except DoclingParseError as exc:
        return await _fail(session, doc, str(exc), redis)

    await _emit_progress(redis, doc.id, stage="chunked", detail=f"{len(docling_chunks)} chunks")

    # 4. embed TROCEADO en lotes (lista vacía = nada que embeber)
    embeddings = await _embed_in_batches([c.content for c in docling_chunks], embedder, doc.id)

    await _emit_progress(
        redis,
        doc.id,
        stage="embedded",
        detail=f"{sum(1 for e in embeddings if e is not None)}/{len(embeddings)}",
    )

    # 5. persist chunks
    for chunk_spec, embedding in zip(docling_chunks, embeddings, strict=True):
        session.add(
            Chunk(
                tenant_id=doc.tenant_id,
                document_id=doc.id,
                ordinal=chunk_spec.ordinal,
                content=chunk_spec.content,
                embedding=embedding,
                bbox=chunk_spec.bbox,
                metadata_=chunk_spec.metadata or {},
            )
        )
    # Honestidad de estado (task_06_17_05): 0 chunks NO es verde
    # "indexed". El documento se procesó (indexed_at se sella), pero no
    # aporta conocimiento recuperable; lo marcamos `indexed_empty` para
    # que la UI no mienta y el operador re-suba un original con texto.
    terminal_status = "indexed" if docling_chunks else "indexed_empty"
    doc.status = terminal_status
    doc.error_message = None
    doc.indexed_at = datetime.now(tz=UTC)
    doc.page_count = _infer_page_count(docling_chunks)
    await session.flush()
    await _publish_status(redis, doc, {"chunks": len(docling_chunks)})

    logger.info(
        "ingestion.indexed",
        document_id=str(doc.id),
        status=terminal_status,
        chunks=len(docling_chunks),
        pages=doc.page_count,
    )
    return IngestionResult(
        document_id=doc.id,
        status=terminal_status,
        chunks_persisted=len(docling_chunks),
        error_message=None,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
async def _embed_in_batches(
    contents: list[str], embedder: Embedder, document_id: UUID
) -> list[list[float] | None]:
    """Embebe `contents` en lotes de :data:`EMBED_BATCH_SIZE`, en orden.

    Devuelve SIEMPRE una lista de la misma longitud que `contents`, alineada
    posición a posición: el elemento *i* es el vector del texto *i*, o ``None``.

    Dos degradaciones, ambas acotadas al LOTE y no al documento:

      * ``EmbeddingError`` (Ollama caído/lento) → ese lote va a ``None``. Los
        embeddings son un nice-to-have: BM25 sigue funcionando sin ellos y el
        backfill los rellenará. Cortar la ingesta entera sería peor.
      * el embedder devuelve un número de vectores DISTINTO del pedido → el lote
        entero va a ``None``. Emparejar por posición una respuesta corta cruzaría
        vectores con chunks ajenos, un daño silencioso que envenena el RAG sin
        que nada falle. Es el mismo criterio que ya aplica
        ``workers.maintenance.chunk_backfill``. Antes de esta tarea el desajuste
        levantaba ``ValueError`` desde el ``zip(strict=True)`` de más abajo, que
        escapaba a Celery pese a que este pipeline promete no levantar nunca.
    """
    embeddings: list[list[float] | None] = []
    for start in range(0, len(contents), EMBED_BATCH_SIZE):
        batch = contents[start : start + EMBED_BATCH_SIZE]
        try:
            vectors = list(await embedder.embed(batch))
        except EmbeddingError as exc:
            logger.warning(
                "ingestion.embedder_failed",
                document_id=str(document_id),
                error=str(exc),
                batch_start=start,
                batch_size=len(batch),
            )
            embeddings.extend([None] * len(batch))
            continue
        if len(vectors) != len(batch):
            logger.warning(
                "ingestion.embedder_count_mismatch",
                document_id=str(document_id),
                expected=len(batch),
                got=len(vectors),
                batch_start=start,
            )
            embeddings.extend([None] * len(batch))
            continue
        embeddings.extend(vectors)
    return embeddings


async def _load_document(session: AsyncSession, document_id: UUID) -> Document:
    result = await session.execute(
        select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
    )
    doc = result.scalar_one_or_none()
    if doc is None:
        raise LookupError(f"document {document_id} not found / deleted")
    return doc


async def _set_status(
    session: AsyncSession,
    doc: Document,
    new_status: str,
    *,
    error: str | None,
    redis: Redis | None,
) -> None:
    doc.status = new_status
    doc.error_message = error
    await session.flush()
    await _publish_status(redis, doc, {})


async def _fail(
    session: AsyncSession,
    doc: Document,
    message: str,
    redis: Redis | None,
) -> IngestionResult:
    doc.status = "failed"
    doc.error_message = message[:2000]
    await session.flush()
    await _publish_status(redis, doc, {"error_message": doc.error_message})
    logger.warning("ingestion.failed", document_id=str(doc.id), error=message)
    return IngestionResult(
        document_id=doc.id,
        status="failed",
        chunks_persisted=0,
        error_message=doc.error_message,
    )


async def _publish_status(redis: Redis | None, doc: Document, extra: dict[str, Any]) -> None:
    if redis is None:
        return
    payload: dict[str, Any] = {
        "document_id": str(doc.id),
        "kb_id": str(doc.kb_id),
        "status": doc.status,
    }
    payload.update(extra)
    await publish_document_event(
        redis, str(doc.id), event_type=EVENT_DOCUMENT_STATUS, payload=payload
    )


async def _emit_progress(
    redis: Redis | None, document_id: UUID, *, stage: str, detail: str
) -> None:
    if redis is None:
        return
    await publish_document_event(
        redis,
        str(document_id),
        event_type=EVENT_DOCUMENT_PROGRESS,
        payload={"stage": stage, "detail": detail},
    )


def _infer_page_count(chunks: list[Any]) -> int:
    """Best-effort page count from chunk bboxes. 0 when nothing in
    the chunks tells us pages (HTML / Markdown sources)."""
    pages: set[int] = {
        c.bbox["page"]
        for c in chunks
        if isinstance(c.bbox, dict) and isinstance(c.bbox.get("page"), int)
    }
    return max(pages) + 1 if pages else 0


__all__ = ["IngestionResult", "ingest_document"]
