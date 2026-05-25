"""`rag_search` tool surface (Plan 04 task_04_20).

High-level function the agent runtime exposes as a tool. It:

  1. embeds the query (optional — falls back to BM25-only if no
     embedder is passed),
  2. runs the hybrid `recall_chunks` against the chunks visible to
     the project,
  3. reranks the top candidates with the configured `Reranker`
     (default: noop / deterministic for tests),
  4. returns the top-`limit` hits with both the RRF score and the
     reranker score so the agent (and the citation viewer) can show
     them.

The placeholder `rag_search` tool that Plan 02 left at 501 will be
re-wired to call this function once the agent-runtime is updated —
see Plan 04 Fase E task_04_22's docs follow-up.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.ingestion.embeddings import Embedder, EmbeddingError
from api_server.rag.reranker import NoopReranker, Reranker, RerankerError
from api_server.rag.search import ChunkHit, recall_chunks

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class RAGSearchHit:
    """Final hit returned to the agent — recall + rerank score
    combined. ``rrf_score`` is from hybrid retrieval; ``rerank_score``
    is from the cross-encoder (NaN if no reranker was applied)."""

    chunk_id: UUID
    document_id: UUID
    kb_id: UUID
    content: str
    ordinal: int
    bbox: dict[str, Any] | None
    bm25_rank: int | None
    vector_rank: int | None
    rrf_score: float
    rerank_score: float | None


async def rag_search(
    session: AsyncSession,
    *,
    query: str,
    tenant_id: UUID,
    project_id: UUID,
    limit: int = 5,
    recall_k: int = 20,
    embedder: Embedder | None = None,
    reranker: Reranker | None = None,
) -> list[RAGSearchHit]:
    """End-to-end RAG retrieval.

    Args:
        session: tenant-scoped async session.
        query: user / agent query text.
        tenant_id: active tenant (RLS already enforces this; we pass
            it so the SQL filters in the visibility check are
            consistent).
        project_id: project whose granted KBs we search.
        limit: top-N to return.
        recall_k: how many candidates the hybrid recall fetches before
            reranking. Bigger = slower + better.
        embedder: optional. If provided we run the vector path; if
            None, BM25-only retrieval still works.
        reranker: optional. Defaults to :class:`NoopReranker` (no
            reordering). Production wires :class:`BGEReranker`.
    """
    # 1. Embed the query if we have an embedder. Failure is OK —
    #    BM25-only retrieval is still useful.
    query_embedding: Sequence[float] | None = None
    if embedder is not None and query.strip():
        try:
            embeddings = await embedder.embed([query])
            if embeddings:
                query_embedding = embeddings[0]
        except EmbeddingError as exc:
            logger.warning("rag.embedder_failed", error=str(exc))

    # 2. Hybrid recall.
    candidates: list[ChunkHit] = await recall_chunks(
        session,
        query=query,
        tenant_id=tenant_id,
        project_id=project_id,
        query_embedding=query_embedding,
        limit=recall_k,
    )
    if not candidates:
        return []

    # 3. Optional rerank.
    rerank = reranker or NoopReranker()
    rerank_scores: dict[UUID, float] = {}
    try:
        reranked = await rerank.rerank(
            query=query,
            items=[(c.chunk_id, c.content) for c in candidates],
        )
        rerank_scores = {hit.chunk_id: hit.score for hit in reranked}
    except RerankerError as exc:
        # Fall through with the original RRF order — never block on a
        # reranker hiccup.
        logger.warning("rag.reranker_failed", error=str(exc))

    # 4. Build final list. If rerank ran we sort by rerank score;
    #    otherwise we keep the RRF order.
    if rerank_scores:
        candidates_sorted = sorted(
            candidates,
            key=lambda c: -rerank_scores.get(c.chunk_id, float("-inf")),
        )
    else:
        candidates_sorted = candidates

    out: list[RAGSearchHit] = []
    for c in candidates_sorted[:limit]:
        out.append(
            RAGSearchHit(
                chunk_id=c.chunk_id,
                document_id=c.document_id,
                kb_id=c.kb_id,
                content=c.content,
                ordinal=c.ordinal,
                bbox=c.bbox,
                bm25_rank=c.bm25_rank,
                vector_rank=c.vector_rank,
                rrf_score=c.rrf_score,
                rerank_score=rerank_scores.get(c.chunk_id),
            )
        )
    return out


__all__ = ["RAGSearchHit", "rag_search"]
