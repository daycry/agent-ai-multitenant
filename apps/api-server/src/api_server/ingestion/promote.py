"""Promote an in-flight `ConvertResult` to a persisted KB Document
(Plan 04 task_04_23).

The companion of :func:`document_convert`: turns the in-memory chunks
into rows on `documents` + `chunks` so the KB search picks them up.

Steps (in one transaction):

  1. write the source bytes to MinIO under the canonical key
     ``kb/{tenant}/{kb}/{document}/{filename}``,
  2. insert a `documents` row with `status='indexed'` (the chunks
     are already parsed; we skip the `pending → processing → indexed`
     dance the async pipeline uses),
  3. embed the chunks in one batch (the embedder is optional — if
     None or it fails, embeddings stay NULL and BM25 still works),
  4. insert one `chunks` row per `DoclingChunk`.

This is the "I converted a doc with `document_convert` and now I
want to keep it" path. The async ingestion pipeline (Plan 04 Fase C)
is the "I uploaded a doc to the KB and want it processed in the
background" path; both end up at the same shape on disk.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.knowledge import Chunk, Document, KnowledgeBase
from api_server.ingestion.docling_mcp import ConvertResult
from api_server.ingestion.embeddings import Embedder, EmbeddingError
from api_server.storage import ObjectStorage, ObjectStorageError

logger = structlog.get_logger(__name__)


class PromotionError(RuntimeError):
    """Raised when the document can't be promoted (KB missing, storage
    backend down, etc.). The router catches it and translates to a
    4xx/5xx."""


@dataclass(frozen=True)
class PromotionResult:
    document_id: UUID
    chunks_persisted: int
    chunks_embedded: int


async def promote_to_kb(
    session: AsyncSession,
    *,
    convert_result: ConvertResult,
    tenant_id: UUID,
    kb_id: UUID,
    raw_bytes: bytes,
    storage: ObjectStorage,
    embedder: Embedder | None = None,
    title: str | None = None,
    actor_user_id: UUID | None = None,
) -> PromotionResult:
    """Persist `convert_result` as a Document + chunks under `kb_id`.

    Args:
        session: tenant-scoped async session (RLS enforced).
        convert_result: output of :func:`document_convert`.
        tenant_id: active tenant — must match the KB's tenant.
        kb_id: target KB id. The KB must exist and not be deleted.
        raw_bytes: the original source bytes (we re-upload them to
            MinIO so the Document row points to a real blob, like
            an upload via /knowledge-bases/{id}/documents would).
        storage: MinIO client (real or in-memory).
        embedder: optional. If None or it fails, embedding stays
            NULL; BM25 still surfaces the chunks.
        title: optional override; defaults to the filename.
        actor_user_id: user attribution (`Document.created_by`).
    """
    kb = await _load_kb(session, kb_id)
    if kb.tenant_id != tenant_id:
        raise PromotionError(f"kb {kb_id} does not belong to tenant {tenant_id}")

    document_id = uuid4()
    storage_key = f"kb/{tenant_id}/{kb_id}/{document_id}/{convert_result.filename}"

    try:
        await storage.put_object(
            key=storage_key,
            data=raw_bytes,
            content_type=convert_result.content_type,
        )
    except ObjectStorageError as exc:
        raise PromotionError(f"storage put_object failed: {exc}") from exc

    doc = Document(
        id=document_id,
        tenant_id=tenant_id,
        kb_id=kb_id,
        title=title or convert_result.filename,
        source_filename=convert_result.filename,
        source_mime_type=convert_result.content_type,
        source_storage_key=storage_key,
        source_size_bytes=len(raw_bytes),
        status="indexed",
        page_count=convert_result.page_count,
        indexed_at=datetime.now(tz=UTC),
        created_by=actor_user_id,
    )
    session.add(doc)
    await session.flush()

    # Embed chunks in one batch — same recipe as the async pipeline.
    embeddings: list[list[float] | None]
    if convert_result.chunks and embedder is not None:
        try:
            vectors = await embedder.embed([c.content for c in convert_result.chunks])
            embeddings = list(vectors)
        except EmbeddingError as exc:
            logger.warning(
                "promote.embedder_failed",
                document_id=str(document_id),
                error=str(exc),
            )
            embeddings = [None] * len(convert_result.chunks)
    else:
        embeddings = [None] * len(convert_result.chunks)

    for spec, embedding in zip(convert_result.chunks, embeddings, strict=True):
        session.add(
            Chunk(
                tenant_id=tenant_id,
                document_id=document_id,
                ordinal=spec.ordinal,
                content=spec.content,
                embedding=embedding,
                bbox=spec.bbox,
                metadata_=spec.metadata or {},
            )
        )
    await session.flush()

    embedded_count = sum(1 for e in embeddings if e is not None)
    logger.info(
        "promote.indexed",
        document_id=str(document_id),
        chunks=len(convert_result.chunks),
        embedded=embedded_count,
    )
    return PromotionResult(
        document_id=document_id,
        chunks_persisted=len(convert_result.chunks),
        chunks_embedded=embedded_count,
    )


async def _load_kb(session: AsyncSession, kb_id: UUID) -> KnowledgeBase:
    result = await session.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.deleted_at.is_(None))
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise PromotionError(f"kb {kb_id} not found")
    return kb


__all__ = ["PromotionError", "PromotionResult", "promote_to_kb"]
