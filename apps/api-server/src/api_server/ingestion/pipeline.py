"""Ingestion pipeline orchestrator (Plan 04 Fase C).

Glues the four injected dependencies (storage, antivirus, docling
client, embedder) into one async function:

  1. mark document `processing` + emit a `document.status` event,
  2. read bytes from storage,
  3. AV scan — fail fast on `INFECTED`,
  4. docling-serve parse + chunk,
  5. embed chunks in one batch,
  6. persist `chunks` rows and update `documents` (`indexed_at`,
     `page_count`, `status=indexed`),
  7. emit a final `document.status` event with the count.

Any failure flips the document to `failed`, stamps `error_message`,
and emits the same event so the UI bar turns red. The pipeline never
raises into the Celery worker — Celery's retry is reserved for
infrastructure failures, not parse errors.
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


@dataclass(frozen=True)
class IngestionResult:
    """Outcome of one `ingest_document` call.

    Returned to the Celery task so the orchestrator can decide whether
    to retry, alert or move on. ``status`` matches the value the
    pipeline wrote on `documents.status`.
    """

    document_id: UUID
    status: str  # 'indexed' | 'failed'
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
        # ERROR is a backend hiccup; we still index but log loudly.
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

    # 4. embed in one batch (empty list = nothing to embed)
    embeddings: list[list[float] | None]
    if docling_chunks:
        try:
            vectors = await embedder.embed([c.content for c in docling_chunks])
            embeddings = list(vectors)
        except EmbeddingError as exc:
            # Embeddings are nice-to-have — BM25 still works without them.
            # Log + persist with NULL embeddings; the back-fill job
            # (Plan 04 Fase D follow-up) can retry later.
            logger.warning(
                "ingestion.embedder_failed",
                document_id=str(doc.id),
                error=str(exc),
            )
            embeddings = [None] * len(docling_chunks)
    else:
        embeddings = []

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
    doc.status = "indexed"
    doc.error_message = None
    doc.indexed_at = datetime.now(tz=UTC)
    doc.page_count = _infer_page_count(docling_chunks)
    await session.flush()
    await _publish_status(redis, doc, {"chunks": len(docling_chunks)})

    logger.info(
        "ingestion.indexed",
        document_id=str(doc.id),
        chunks=len(docling_chunks),
        pages=doc.page_count,
    )
    return IngestionResult(
        document_id=doc.id,
        status="indexed",
        chunks_persisted=len(docling_chunks),
        error_message=None,
    )


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
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
