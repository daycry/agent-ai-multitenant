"""Hybrid memory recall (Plan 04 task_04_04).

Two retrieval paths over `memory_entries` combined with Reciprocal
Rank Fusion:

  - **Text path (BM25-like)** — `ts_rank_cd` over the GIN-indexed
    `to_tsvector('simple', content)`. PostgreSQL builds an inverted
    index on terms; the rank function approximates BM25 closely
    enough for memory retrieval.
  - **Vector path** — `embedding <=> :query_vector` (cosine distance,
    converted to similarity). Skips rows where the embedder hasn't
    back-filled the embedding yet (`embedding IS NULL`).

The two ranked result lists are merged with RRF (Cormack 2009):

    score(d) = sum_over_lists( 1 / (k + rank_d_in_list) )

with `k = 60` by default. Documents that only appear in one list
still receive a score from that list; documents in both lists are
boosted. This is the same recipe LangChain, Vespa and pgvector docs
suggest for hybrid retrieval.

Scope filter is explicit — the caller (the `memory_recall` tool in
agent-runtime) picks which scopes the agent can read based on its
own `memory_scope`. The SQL also requires the matching owner pointer
(user_id / team_id / project_id) so cross-tenant or cross-team
private memories never surface.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Reciprocal Rank Fusion smoothing constant. 60 is the value
# Cormack et al. (2009) recommend.
RRF_K_DEFAULT = 60

# How many candidates each retrieval path returns before fusion.
BM25_K_DEFAULT = 20
VECTOR_K_DEFAULT = 20


@dataclass(frozen=True)
class MemoryRecallHit:
    """One row of recall output.

    Carries the `MemoryEntry` id + the per-path scores so the caller
    can debug the ranking ("why did this rank above that?") without a
    second round-trip. The full row is loaded by the tool that owns
    the response shape, not here.
    """

    memory_id: UUID
    content: str
    scope: str
    type: str
    bm25_rank: int | None  # 1-indexed; None if the BM25 path didn't return it
    vector_rank: int | None  # 1-indexed; None if the vector path didn't return it
    rrf_score: float


def rrf_score(rank: int, k: int = RRF_K_DEFAULT) -> float:
    """Reciprocal Rank Fusion contribution of one ranked list.

    ``rank`` is 1-indexed. The contribution decays as ``1 / (k + rank)``
    so the top match contributes ``1 / (k + 1)`` and rank 20 contributes
    ``1 / (k + 20)``.
    """
    if rank < 1:
        raise ValueError("rank must be 1-indexed (>= 1)")
    return 1.0 / (k + rank)


def fuse_rankings(
    bm25_ids: Sequence[UUID],
    vector_ids: Sequence[UUID],
    *,
    k: int = RRF_K_DEFAULT,
) -> dict[UUID, tuple[float, int | None, int | None]]:
    """Merge two ranked id lists with RRF.

    Returns ``{memory_id: (rrf_score, bm25_rank, vector_rank)}`` for
    every id that appeared in at least one list. ``bm25_rank`` and
    ``vector_rank`` are 1-indexed and ``None`` when the id was absent
    from that list.
    """
    out: dict[UUID, tuple[float, int | None, int | None]] = {}
    bm25_ranks: dict[UUID, int] = {mid: i + 1 for i, mid in enumerate(bm25_ids)}
    vector_ranks: dict[UUID, int] = {mid: i + 1 for i, mid in enumerate(vector_ids)}
    for mid in set(bm25_ranks) | set(vector_ranks):
        s = 0.0
        bm25_r = bm25_ranks.get(mid)
        if bm25_r is not None:
            s += rrf_score(bm25_r, k=k)
        vec_r = vector_ranks.get(mid)
        if vec_r is not None:
            s += rrf_score(vec_r, k=k)
        out[mid] = (s, bm25_r, vec_r)
    return out


def _scope_filter_sql() -> str:
    """SQL `AND` clause for scope+owner filtering.

    Lives as a string constant so both retrieval paths build the same
    `WHERE` block. The placeholders (`:scopes`, `:user_id`,
    `:team_id`, `:project_id`) are bound by the caller; NULL inputs
    behave correctly because the owner equality short-circuits via
    the scope check.
    """
    return (
        " AND scope = ANY(:scopes)"
        " AND ("
        "   scope = 'global'"
        "   OR (scope = 'private' AND user_id = :user_id)"
        "   OR (scope = 'team_shared' AND team_id = :team_id)"
        "   OR (scope = 'project_shared' AND project_id = :project_id)"
        " )"
    )


async def _bm25_candidates(
    session: AsyncSession,
    *,
    query: str,
    tenant_id: UUID,
    scopes: Sequence[str],
    user_id: UUID | None,
    team_id: UUID | None,
    project_id: UUID | None,
    limit: int,
) -> list[UUID]:
    """Top-`limit` ids by `ts_rank_cd`. Empty list if `query` is blank."""
    if not query.strip():
        return []
    sql = (
        "SELECT id"
        " FROM memory_entries"
        " WHERE tenant_id = :tenant_id"
        "   AND deleted_at IS NULL"
        "   AND to_tsvector('simple', content) @@ plainto_tsquery('simple', :q)"
        + _scope_filter_sql()
        + " ORDER BY ts_rank_cd(to_tsvector('simple', content),"
        "          plainto_tsquery('simple', :q)) DESC"
        "  LIMIT :limit"
    )
    result = await session.execute(
        text(sql),
        {
            "tenant_id": tenant_id,
            "q": query,
            "scopes": list(scopes),
            "user_id": user_id,
            "team_id": team_id,
            "project_id": project_id,
            "limit": limit,
        },
    )
    return [row[0] for row in result.all()]


async def _vector_candidates(
    session: AsyncSession,
    *,
    query_embedding: Sequence[float] | None,
    tenant_id: UUID,
    scopes: Sequence[str],
    user_id: UUID | None,
    team_id: UUID | None,
    project_id: UUID | None,
    limit: int,
) -> list[UUID]:
    """Top-`limit` ids by cosine similarity. Empty list if no query
    embedding (the embedder hasn't been wired yet, task_04_14)."""
    if query_embedding is None:
        return []
    # pgvector's <=> is cosine distance. We don't need the score for
    # the ranking step — RRF only cares about the *order*.
    sql = (
        "SELECT id"
        " FROM memory_entries"
        " WHERE tenant_id = :tenant_id"
        "   AND deleted_at IS NULL"
        "   AND embedding IS NOT NULL"
        + _scope_filter_sql()
        + " ORDER BY embedding <=> CAST(:qvec AS vector)"
        " LIMIT :limit"
    )
    # asyncpg expects the literal vector string (e.g. "[0.1,0.2,...]").
    qvec_str = "[" + ",".join(f"{x:.6f}" for x in query_embedding) + "]"
    result = await session.execute(
        text(sql),
        {
            "tenant_id": tenant_id,
            "qvec": qvec_str,
            "scopes": list(scopes),
            "user_id": user_id,
            "team_id": team_id,
            "project_id": project_id,
            "limit": limit,
        },
    )
    return [row[0] for row in result.all()]


async def recall(
    session: AsyncSession,
    *,
    query: str,
    tenant_id: UUID,
    scopes: Sequence[str],
    user_id: UUID | None = None,
    team_id: UUID | None = None,
    project_id: UUID | None = None,
    query_embedding: Sequence[float] | None = None,
    limit: int = 5,
    bm25_k: int = BM25_K_DEFAULT,
    vector_k: int = VECTOR_K_DEFAULT,
    rrf_k: int = RRF_K_DEFAULT,
) -> list[MemoryRecallHit]:
    """Hybrid recall — see module docstring.

    Returns up to ``limit`` :class:`MemoryRecallHit` instances ordered
    by RRF score descending. The session is expected to already have
    `app.tenant_id` set (the tenant filter in SQL is a defence-in-depth
    on top of RLS).
    """
    bm25_ids = await _bm25_candidates(
        session,
        query=query,
        tenant_id=tenant_id,
        scopes=scopes,
        user_id=user_id,
        team_id=team_id,
        project_id=project_id,
        limit=bm25_k,
    )
    vector_ids = await _vector_candidates(
        session,
        query_embedding=query_embedding,
        tenant_id=tenant_id,
        scopes=scopes,
        user_id=user_id,
        team_id=team_id,
        project_id=project_id,
        limit=vector_k,
    )
    fused = fuse_rankings(bm25_ids, vector_ids, k=rrf_k)
    if not fused:
        return []

    # Load the top-`limit` rows in a single round-trip — we already
    # have their ids ordered by RRF score.
    top_ids = [mid for mid, _ in sorted(fused.items(), key=lambda kv: -kv[1][0])][:limit]
    if not top_ids:
        return []
    detail_rows = await session.execute(
        text("SELECT id, content, scope, type FROM memory_entries" " WHERE id = ANY(:ids)"),
        {"ids": top_ids},
    )
    by_id: dict[UUID, dict[str, Any]] = {
        row[0]: {"content": row[1], "scope": row[2], "type": row[3]} for row in detail_rows.all()
    }
    hits: list[MemoryRecallHit] = []
    for mid in top_ids:
        if mid not in by_id:
            continue
        s, bm25_r, vec_r = fused[mid]
        hits.append(
            MemoryRecallHit(
                memory_id=mid,
                content=by_id[mid]["content"],
                scope=by_id[mid]["scope"],
                type=by_id[mid]["type"],
                bm25_rank=bm25_r,
                vector_rank=vec_r,
                rrf_score=s,
            )
        )
    return hits


__all__ = [
    "BM25_K_DEFAULT",
    "RRF_K_DEFAULT",
    "VECTOR_K_DEFAULT",
    "MemoryRecallHit",
    "fuse_rankings",
    "recall",
    "rrf_score",
]
