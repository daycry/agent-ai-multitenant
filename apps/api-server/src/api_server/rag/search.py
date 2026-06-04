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
def _kb_visibility_filter(*, with_agent: bool = False) -> str:
    """`AND` clause restricting chunks to KBs the caller can read.

    Bound parameters:
      - ``:tenant_id`` — current tenant (defence in depth on top of
        RLS).
      - ``:project_id`` — the project asking. The chunk's KB must
        appear in `kb_projects` for this project.
      - ``:agent_id`` — only when ``with_agent=True``. KBs granted to
        the agent template are also visible (Plan 06.9).

    Delegates to :func:`api_server.rag.visibility.visibility_filter_clause`
    so the rule lives in one place; the resolver
    :func:`resolve_visible_kbs` and the chunk search use the same SQL.
    """
    from api_server.rag.visibility import visibility_filter_clause

    return visibility_filter_clause(with_agent=with_agent)


# ---------------------------------------------------------------------------
# BM25 (task_04_16)
# ---------------------------------------------------------------------------
async def bm25_chunks(
    session: AsyncSession,
    *,
    query: str,
    tenant_id: UUID,
    project_id: UUID,
    agent_id: UUID | None = None,
    limit: int = BM25_K_DEFAULT,
) -> list[UUID]:
    """Top-`limit` chunk ids by ts_rank_cd, restricted to KBs the
    project (and optionally agent) can read. Empty list if `query`
    is blank."""
    if not query.strip():
        return []
    sql = (
        "SELECT chunks.id"
        " FROM chunks"
        " WHERE to_tsvector('simple', chunks.content)"
        "        @@ plainto_tsquery('simple', :q)"
        + _kb_visibility_filter(with_agent=agent_id is not None)
        + " ORDER BY ts_rank_cd("
        "          to_tsvector('simple', chunks.content),"
        "          plainto_tsquery('simple', :q)) DESC"
        "  LIMIT :limit"
    )
    params: dict[str, object] = {
        "q": query,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "limit": limit,
    }
    if agent_id is not None:
        params["agent_id"] = agent_id
    result = await session.execute(text(sql), params)
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
    agent_id: UUID | None = None,
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
        + _kb_visibility_filter(with_agent=agent_id is not None)
        + " ORDER BY chunks.embedding <=> CAST(:qvec AS vector)"
        " LIMIT :limit"
    )
    qvec_str = "[" + ",".join(f"{x:.6f}" for x in query_embedding) + "]"
    params: dict[str, object] = {
        "qvec": qvec_str,
        "tenant_id": tenant_id,
        "project_id": project_id,
        "limit": limit,
    }
    if agent_id is not None:
        params["agent_id"] = agent_id
    result = await session.execute(text(sql), params)
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
    agent_id: UUID | None = None,
    query_embedding: Sequence[float] | None = None,
    limit: int = 8,
    bm25_k: int = BM25_K_DEFAULT,
    vector_k: int = VECTOR_K_DEFAULT,
    rrf_k: int = RRF_K_DEFAULT,
) -> list[ChunkHit]:
    """Hybrid (BM25 + vector + RRF) chunk search.

    Returns up to ``limit`` :class:`ChunkHit` sorted by RRF score
    descending. The session must already have `app.tenant_id` set.

    When ``agent_id`` is given, KBs granted to the agent template
    (Plan 06.9) are also visible — the chunks query unions them
    with the project's grants.
    """
    bm25_ids = await bm25_chunks(
        session,
        query=query,
        tenant_id=tenant_id,
        project_id=project_id,
        agent_id=agent_id,
        limit=bm25_k,
    )
    vec_ids = await vector_chunks(
        session,
        query_embedding=query_embedding,
        tenant_id=tenant_id,
        project_id=project_id,
        agent_id=agent_id,
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


# ---------------------------------------------------------------------------
# KB-scoped preview search (Plan 06.17 task_06_17_05)
# ---------------------------------------------------------------------------
async def search_kb_chunks(
    session: AsyncSession,
    *,
    kb_id: UUID,
    query: str,
    query_embedding: Sequence[float] | None = None,
    limit: int = 8,
    bm25_k: int = BM25_K_DEFAULT,
    vector_k: int = VECTOR_K_DEFAULT,
    rrf_k: int = RRF_K_DEFAULT,
) -> list[ChunkHit]:
    """Búsqueda híbrida (BM25 + vector + RRF) acotada a UNA KB.

    A diferencia de :func:`recall_chunks` (acotada por grants de
    proyecto/agente para el RAG del agente), esta es el **preview del
    dueño de la KB**: busca chunks de los documentos de ``kb_id``,
    aislada por tenant vía RLS (la sesión ya trae ``app.tenant_id``).
    El llamador (endpoint) verifica antes que la KB es visible (404 si
    no), de modo que cross-tenant nunca llega aquí.

    Devuelve hasta ``limit`` :class:`ChunkHit` ordenados por RRF. Lista
    vacía si ``query`` es blanco.
    """
    if not query.strip():
        return []

    bm25_ids = await _kb_bm25_chunks(session, kb_id=kb_id, query=query, limit=bm25_k)
    vec_ids = await _kb_vector_chunks(
        session, kb_id=kb_id, query_embedding=query_embedding, limit=vector_k
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


async def _kb_bm25_chunks(
    session: AsyncSession, *, kb_id: UUID, query: str, limit: int
) -> list[UUID]:
    """Top-`limit` chunk ids por ts_rank_cd, acotados a la KB.

    Usa la misma configuración TS español/inglés + unaccent que el recall
    de memoria (``public.es_unaccent``, migración 0079) para que
    ``arquitectura`` case ``arquitecturas`` en el preview."""
    if not query.strip():
        return []
    sql = (
        "SELECT chunks.id"
        " FROM chunks"
        " JOIN documents ON documents.id = chunks.document_id"
        " WHERE documents.kb_id = :kb_id"
        "   AND documents.deleted_at IS NULL"
        "   AND to_tsvector('public.es_unaccent', chunks.content)"
        "        @@ plainto_tsquery('public.es_unaccent', :q)"
        " ORDER BY ts_rank_cd("
        "          to_tsvector('public.es_unaccent', chunks.content),"
        "          plainto_tsquery('public.es_unaccent', :q)) DESC"
        " LIMIT :limit"
    )
    result = await session.execute(text(sql), {"q": query, "kb_id": kb_id, "limit": limit})
    return [row[0] for row in result.all()]


async def _kb_vector_chunks(
    session: AsyncSession,
    *,
    kb_id: UUID,
    query_embedding: Sequence[float] | None,
    limit: int,
) -> list[UUID]:
    """Top-`limit` chunk ids por similitud coseno, acotados a la KB.
    Lista vacía si no hay query embedding (sin embedder no hay vector)."""
    if query_embedding is None:
        return []
    sql = (
        "SELECT chunks.id"
        " FROM chunks"
        " JOIN documents ON documents.id = chunks.document_id"
        " WHERE documents.kb_id = :kb_id"
        "   AND documents.deleted_at IS NULL"
        "   AND chunks.embedding IS NOT NULL"
        " ORDER BY chunks.embedding <=> CAST(:qvec AS vector)"
        " LIMIT :limit"
    )
    qvec_str = "[" + ",".join(f"{x:.6f}" for x in query_embedding) + "]"
    result = await session.execute(text(sql), {"qvec": qvec_str, "kb_id": kb_id, "limit": limit})
    return [row[0] for row in result.all()]


__all__ = [
    "ChunkHit",
    "bm25_chunks",
    "recall_chunks",
    "search_kb_chunks",
    "vector_chunks",
]
