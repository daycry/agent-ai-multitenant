"""Reranker contract test (Plan 04 task_04_19).

Verifies the `Reranker` Protocol shape against:

  - `NoopReranker` — identity ordering with decaying scores,
  - `DeterministicReranker` — lexical-overlap fake,
  - `BGEReranker` — real cross-encoder; we don't run the model here
    (heavy torch dep) but we exercise the import-error path so
    deployments that skip the dep fail fast and clearly.
"""

from __future__ import annotations

import pytest
from api_server.rag.reranker import (
    BGEReranker,
    DeterministicReranker,
    NoopReranker,
    RerankerError,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_noop_reranker_preserves_input_order_with_decaying_scores() -> None:
    reranker = NoopReranker()
    items = [("a", "content a"), ("b", "content b"), ("c", "content c")]
    out = await reranker.rerank(query="anything", items=items)
    assert [hit.chunk_id for hit in out] == ["a", "b", "c"]
    # Scores strictly decreasing.
    assert out[0].score > out[1].score > out[2].score


@pytest.mark.asyncio
async def test_deterministic_reranker_promotes_overlap_match() -> None:
    """Lexical overlap fake: the chunk that shares more tokens with
    the query must rank above one that shares fewer."""
    reranker = DeterministicReranker()
    items = [
        ("noise", "The weather is fine today."),
        ("hit", "The system uses asyncpg for postgres queries."),
        ("partial", "Postgres is a database engine."),
    ]
    out = await reranker.rerank(query="asyncpg postgres queries", items=items)
    # 'hit' shares 3/3 tokens → top; 'partial' shares 1/3 → middle;
    # 'noise' shares 0 → bottom.
    assert out[0].chunk_id == "hit"
    assert out[-1].chunk_id == "noise"


@pytest.mark.asyncio
async def test_deterministic_reranker_ties_resolve_stably() -> None:
    reranker = DeterministicReranker()
    out = await reranker.rerank(
        query="alpha",
        items=[("a", "alpha beta"), ("b", "alpha gamma")],
    )
    # Both share 1 token → tied. Score must be equal.
    assert out[0].score == out[1].score


@pytest.mark.asyncio
async def test_empty_items_returns_empty_list() -> None:
    for reranker in (NoopReranker(), DeterministicReranker(), BGEReranker()):
        out = await reranker.rerank(query="x", items=[])
        assert out == []


@pytest.mark.asyncio
async def test_bge_reranker_raises_when_flag_embedding_missing() -> None:
    """A deployment that skipped the FlagEmbedding install must fail
    fast and clearly when the reranker is exercised — not silently
    return identity scores."""
    reranker = BGEReranker()
    items = [("a", "hello"), ("b", "world")]
    # We rely on FlagEmbedding NOT being installed in the test env.
    # If a future contributor adds it, the test will surface that.
    try:
        import FlagEmbedding  # noqa: F401
    except ImportError:
        with pytest.raises(RerankerError, match="FlagEmbedding"):
            await reranker.rerank(query="x", items=items)
    else:
        pytest.skip("FlagEmbedding present — real reranker is exercised by hand")
