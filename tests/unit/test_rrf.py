"""Reciprocal Rank Fusion (Plan 04 task_04_18).

The same `fuse_rankings` / `rrf_score` primitives the memory recall
path already uses (see `tests/unit/test_memory_recall_rrf.py`). This
file pins the contract from the RAG side: the chunk-search caller
must see the same RRF semantics, with the canonical Cormack 2009
constant (k=60).
"""

from __future__ import annotations

from uuid import UUID

import pytest
from api_server.memorizer.recall import (
    RRF_K_DEFAULT,
    fuse_rankings,
    rrf_score,
)

pytestmark = pytest.mark.unit


def _uid(n: int) -> UUID:
    return UUID(f"00000000-0000-0000-0000-{n:012d}")


# ---------------------------------------------------------------------------
# Default-k semantics
# ---------------------------------------------------------------------------
def test_default_k_is_60_as_per_cormack_2009() -> None:
    """RRF's smoothing constant is fixed at 60 — every published
    paper since uses the same value. Lock it in so an upstream
    refactor doesn't silently change retrieval quality."""
    assert RRF_K_DEFAULT == 60


def test_rrf_score_at_rank_1_is_one_over_61() -> None:
    assert rrf_score(1) == pytest.approx(1.0 / 61, abs=1e-9)


def test_rrf_score_monotone_decreasing() -> None:
    """Lower rank → higher contribution."""
    prev = float("inf")
    for r in range(1, 30):
        s = rrf_score(r)
        assert s < prev
        prev = s


# ---------------------------------------------------------------------------
# fuse_rankings algebra
# ---------------------------------------------------------------------------
def test_id_only_in_bm25_gets_bm25_only_score() -> None:
    out = fuse_rankings([_uid(1)], [])
    s, br, vr, _er = out[_uid(1)]
    assert s == pytest.approx(1.0 / (RRF_K_DEFAULT + 1))
    assert br == 1
    assert vr is None


def test_id_in_both_lists_is_strictly_above_id_in_one() -> None:
    """The headline property of RRF: an item present in *both* paths
    must outscore an item present in only one."""
    out = fuse_rankings([_uid(1), _uid(2)], [_uid(1), _uid(3)])
    sorted_ids = [mid for mid, _ in sorted(out.items(), key=lambda kv: -kv[1][0])]
    # _uid(1) is in both lists (top of each → strongest signal).
    assert sorted_ids[0] == _uid(1)
    # _uid(2) and _uid(3) only appear in one each → tied.
    assert {sorted_ids[1], sorted_ids[2]} == {_uid(2), _uid(3)}


def test_fuse_is_symmetric_in_the_two_lists() -> None:
    """fuse(A, B) and fuse(B, A) yield the same scores."""
    a = [_uid(1), _uid(2)]
    b = [_uid(2), _uid(3)]
    out_ab = fuse_rankings(a, b)
    out_ba = fuse_rankings(b, a)
    for mid in set(out_ab) | set(out_ba):
        assert out_ab[mid][0] == pytest.approx(out_ba[mid][0])


def test_smaller_k_amplifies_top_ranks() -> None:
    """A smaller k weights the top-of-list more heavily — useful when
    we trust the first hit far more than the rest."""
    bm25 = [_uid(1), _uid(2), _uid(3)]
    out_60 = fuse_rankings(bm25, [], k=60)
    out_5 = fuse_rankings(bm25, [], k=5)
    # Top rank gains a lot more with k=5 than k=60.
    gain_top = out_5[_uid(1)][0] / out_60[_uid(1)][0]
    gain_bot = out_5[_uid(3)][0] / out_60[_uid(3)][0]
    assert gain_top > gain_bot


def test_empty_inputs_yield_empty_dict() -> None:
    assert fuse_rankings([], []) == {}


def test_fuse_rejects_invalid_rank() -> None:
    """RRF is undefined for rank 0 — surface the error early."""
    with pytest.raises(ValueError):
        rrf_score(0)
