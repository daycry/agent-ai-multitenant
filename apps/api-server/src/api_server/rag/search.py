"""Chunk retrieval primitives (Plan 04 task_04_16 / task_04_17 / task_04_18).

Mirrors the memory recall path but against the `chunks` table — which
ties to `documents` → `knowledge_bases`, with the M:N `kb_projects`
junction controlling which KBs a project can read.

Three building blocks:

  - :func:`bm25_chunks`     — text relevance via `ts_rank_cd` on the
    GIN FTS index installed by migration 0022.
  - :func:`vector_chunks`   — pgvector cosine similarity on the HNSW
    index, skipping chunks with NULL embeddings (the ingestion
    pipeline back-fills them; until then BM25 still surfaces them).
  - :func:`recall_chunks`   — combines both via Reciprocal Rank
    Fusion (`api_server.memorizer.recall.fuse_rankings`).

All three accept a `project_id` parameter so the SQL filter restricts
results to KBs visible to that project via `kb_projects`. Tenant
isolation is delegated to RLS as usual.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Shared RRF primitives — they live in memorizer/recall.py because
# memory recall was the first caller. We import rather than re-implement.
from api_server.memorizer.recall import (
    BM25_K_DEFAULT,
    RRF_K_DEFAULT,
    VECTOR_K_DEFAULT,
    fuse_rankings,
)


@dataclass(frozen=True)
class ChunkHit:
    """One chunk surfaced by retrieval.

    The per-path ranks let the caller (or the reranker) reason about
    *why* a chunk was retrieved; `rrf_score` is the merged score the
    hybrid path produces. The reranker may overwrite `rrf_score` with
    its own; we keep both visible on the response for debugging.
    """

    chunk_id: UUID
    document_id: UUID
    kb_id: UUID
    content: str
    ordinal: int
    bbox: dict[str, Any] | None
    bm25_rank: int | None
    vector_rank: int | None
    rrf_score: float


# ---------------------------------------------------------------------------
# Internal SQL builders
# ---------------------------------------------------------------------------
def _kb_visibility_filter() -> str:
    """`AND` clause restricting chunks to KBs the project can read.

    Bound parameters:
      - ``:tenant_id`` — current tenant (defence in depth on top of
        RLS).
      - ``:project_id`` — the project asking. The chunk's KB must
        appear in `kb_projects` for this project, OR be granted to
        no project (no rows means invisible — see Plan 04 Fase B
        decision).
    """
    return (
        " AND chunks.tenant_id = :tenant_id"
        " AND EXISTS ("
        "   SELECT 1 FROM kb_projects kp"
        "   JOIN documents d ON d.id = chunks.document_id"
        "   WHERE kp.kb_id = d.kb_id"
        "     AND kp.project_id = :project_id"
        " )"
    )


# ---------------------------------------------------------------------------
# BM25 (task_04_16)
# ---------------------------------------------------------------------------
async def bm25_chunks(
    session: AsyncSession,
    *,
    query: str,
    tenant_id: UUID,
    project_id: UUID,
    limit: int = BM25_K_DEFAULT,
) -> list[UUID]:
    """Top-`limit` chunk ids by ts_rank_cd, restricted to KBs the
    project can read. Empty list if `query` is blank."""
    if not query.strip():
        return []
    sql = (
        "SELECT chunks.id"
        " FROM chunks"
        " WHERE to_tsvector('simple', chunks.content)"
        "        @@ plainto_tsquery('simple', :q)"
        + _kb_visibility_filter()
        + " ORDER BY ts_rank_cd("
        "          to_tsvector('simple', chunks.content),"
        "          plainto_tsquery('simple', :q)) DESC"
        "  LIMIT :limit"
    )
    result = await session.execute(
        text(sql),
        {
            "q": query,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "limit": limit,
        },
    )
    return [row[0] for row in result.all()]


# ---------------------------------------------------------------------------
# Vector (task_04_17)
# ---------------------------------------------------------------------------
async def vector_chunks(
    session: AsyncSession,
    *,
    query_embedding: Sequence[float] | None,
    tenant_id: UUID,
    project_id: UUID,
    limit: int = VECTOR_K_DEFAULT,
) -> list[UUID]:
    """Top-`limit` chunk ids by cosine similarity. Empty list if no
    query embedding (an embedder is required for the vector path)."""
    if query_embedding is None:
        return []
    sql = (
        "SELECT chunks.id"
        " FROM chunks"
        " WHERE chunks.embedding IS NOT NULL"
        + _kb_visibility_filter()
        + " ORDER BY chunks.embedding <=> CAST(:qvec AS vector)"
        " LIMIT :limit"
    )
    qvec_str = "[" + ",".join(f"{x:.6f}" for x in query_embedding) + "]"
    result = await session.execute(
        text(sql),
        {
            "qvec": qvec_str,
            "tenant_id": tenant_id,
            "project_id": project_id,
            "limit": limit,
        },
    )
    return [row[0] for row in result.all()]


# ---------------------------------------------------------------------------
# Hybrid recall_chunks (task_04_18 — RRF orchestrator)
# ---------------------------------------------------------------------------
async def recall_chunks(
    session: AsyncSession,
    *,
    query: str,
    tenant_id: UUID,
    project_id: UUID,
    query_embedding: Sequence[float] | None = None,
    limit: int = 8,
    bm25_k: int = BM25_K_DEFAULT,
    vector_k: int = VECTOR_K_DEFAULT,
    rrf_k: int = RRF_K_DEFAULT,
) -> list[ChunkHit]:
    """Hybrid (BM25 + vector + RRF) chunk search.

    Returns up to ``limit`` :class:`ChunkHit` sorted by RRF score
    descending. The session must already have `app.tenant_id` set."""
    bm25_ids = await bm25_chunks(
        session, query=query, tenant_id=tenant_id, project_id=project_id, limit=bm25_k
    )
    vec_ids = await vector_chunks(
        session,
        query_embedding=query_embedding,
        tenant_id=tenant_id,
        project_id=project_id,
        limit=vector_k,
    )
    fused = fuse_rankings(bm25_ids, vec_ids, k=rrf_k)
    if not fused:
        return []

    top_ids = [mid for mid, _ in sorted(fused.items(), key=lambda kv: -kv[1][0])][:limit]
    if not top_ids:
        return []

    rows = await session.execute(
        text(
            "SELECT chunks.id, chunks.document_id, documents.kb_id,"
            "       chunks.content, chunks.ordinal, chunks.bbox"
            " FROM chunks"
            " JOIN documents ON documents.id = chunks.document_id"
            " WHERE chunks.id = ANY(:ids)"
        ),
        {"ids": top_ids},
    )
    by_id: dict[UUID, dict[str, Any]] = {
        row[0]: {
            "document_id": row[1],
            "kb_id": row[2],
            "content": row[3],
            "ordinal": row[4],
            "bbox": row[5],
        }
        for row in rows.all()
    }

    hits: list[ChunkHit] = []
    for chunk_id in top_ids:
        if chunk_id not in by_id:
            continue
        s, bm25_r, vec_r = fused[chunk_id]
        d = by_id[chunk_id]
        hits.append(
            ChunkHit(
                chunk_id=chunk_id,
                document_id=d["document_id"],
                kb_id=d["kb_id"],
                content=d["content"],
                ordinal=d["ordinal"],
                bbox=d["bbox"],
                bm25_rank=bm25_r,
                vector_rank=vec_r,
                rrf_score=s,
            )
        )
    return hits


__all__ = [
    "ChunkHit",
    "bm25_chunks",
    "recall_chunks",
    "vector_chunks",
]
