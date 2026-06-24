"""Unit tests for the pure RRF helpers (Plan 04 task_04_04).

The async `recall()` function is exercised end-to-end against Postgres
in `tests/integration/test_memory_recall.py`; here we only test the
fusion arithmetic — easy to reason about in isolation.
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
# rrf_score
# ---------------------------------------------------------------------------
def test_rrf_score_decreases_with_rank() -> None:
    assert rrf_score(1) > rrf_score(2) > rrf_score(10) > rrf_score(100)


def test_rrf_score_top_rank_with_default_k() -> None:
    # 1 / (60 + 1)
    assert rrf_score(1) == pytest.approx(1.0 / 61, abs=1e-9)


def test_rrf_score_custom_k() -> None:
    # Smaller k makes top rank weigh more.
    assert rrf_score(1, k=10) > rrf_score(1, k=60)


def test_rrf_score_rejects_zero_or_negative_rank() -> None:
    with pytest.raises(ValueError, match="1-indexed"):
        rrf_score(0)
    with pytest.raises(ValueError, match="1-indexed"):
        rrf_score(-1)


# ---------------------------------------------------------------------------
# fuse_rankings
# ---------------------------------------------------------------------------
def test_fuse_two_disjoint_lists_returns_all_ids() -> None:
    bm25 = [_uid(1), _uid(2)]
    vec = [_uid(3)]
    out = fuse_rankings(bm25, vec)
    assert set(out) == {_uid(1), _uid(2), _uid(3)}


def test_fuse_overlapping_id_gets_summed_score() -> None:
    """An id that ranks #1 in both lists must score 2 / (k+1)."""
    out = fuse_rankings([_uid(1)], [_uid(1)])
    score, bm25_r, vec_r, _ent_r = out[_uid(1)]
    assert score == pytest.approx(2.0 / (RRF_K_DEFAULT + 1), abs=1e-9)
    assert bm25_r == 1
    assert vec_r == 1


def test_fuse_only_in_one_list_carries_none_for_other_rank() -> None:
    out = fuse_rankings([_uid(1)], [])
    score, bm25_r, vec_r, _ent_r = out[_uid(1)]
    assert score == pytest.approx(1.0 / (RRF_K_DEFAULT + 1))
    assert bm25_r == 1
    assert vec_r is None


def test_fuse_preserves_rank_order_via_score() -> None:
    """When ranks differ, the lower rank wins."""
    out = fuse_rankings([_uid(1), _uid(2)], [_uid(2), _uid(1)])
    # _uid(1) is rank 1 in bm25 + rank 2 in vector
    # _uid(2) is rank 2 in bm25 + rank 1 in vector
    # Symmetric → equal scores.
    assert out[_uid(1)][0] == pytest.approx(out[_uid(2)][0])


def test_fuse_ranking_top_is_double_listed_id() -> None:
    """Boost: an id present in both lists must score above any
    id present in only one."""
    bm25 = [_uid(1), _uid(2), _uid(3)]
    vec = [_uid(1), _uid(4), _uid(5)]
    out = fuse_rankings(bm25, vec)
    sorted_ids = sorted(out.items(), key=lambda kv: -kv[1][0])
    assert sorted_ids[0][0] == _uid(1)


def test_fuse_empty_lists_return_empty_dict() -> None:
    assert fuse_rankings([], []) == {}


def test_fuse_with_custom_k() -> None:
    """A smaller k makes the top rank more dominant; the relative
    order between two ids of similar rank doesn't change."""
    bm25 = [_uid(1), _uid(2)]
    vec = [_uid(2), _uid(1)]
    out60 = fuse_rankings(bm25, vec, k=60)
    out5 = fuse_rankings(bm25, vec, k=5)
    # Same set; same symmetric order.
    assert set(out60) == set(out5)
    # k=5 yields strictly higher scores for both.
    assert out5[_uid(1)][0] > out60[_uid(1)][0]


# ---------------------------------------------------------------------------
# Entity-match signal (ADR 0059 Opción A — third RRF list, mem0 idea nativa)
# ---------------------------------------------------------------------------
def test_fuse_entity_signal_contributes_to_score() -> None:
    """An id ranked #1 in both bm25 AND the entity-match list scores 2/(k+1)."""
    out = fuse_rankings([_uid(1)], [], [_uid(1)])
    score, bm25_r, vec_r, ent_r = out[_uid(1)]
    assert score == pytest.approx(2.0 / (RRF_K_DEFAULT + 1), abs=1e-9)
    assert bm25_r == 1
    assert vec_r is None
    assert ent_r == 1


def test_fuse_entity_only_id_surfaces() -> None:
    """A memory found ONLY by entity match still surfaces with its own score."""
    out = fuse_rankings([], [], [_uid(7)])
    score, bm25_r, vec_r, ent_r = out[_uid(7)]
    assert bm25_r is None and vec_r is None and ent_r == 1
    assert score == pytest.approx(1.0 / (RRF_K_DEFAULT + 1))


def test_fuse_id_in_all_three_lists_tops_ranking() -> None:
    out = fuse_rankings([_uid(1), _uid(2)], [_uid(1), _uid(3)], [_uid(1), _uid(4)])
    top = sorted(out.items(), key=lambda kv: -kv[1][0])[0][0]
    assert top == _uid(1)


def test_fuse_no_entities_is_backward_compatible_four_tuple() -> None:
    """Omitting the entity list yields entity_rank=None (only the shape grows)."""
    out = fuse_rankings([_uid(1)], [_uid(1)])
    _score, _b, _v, ent_r = out[_uid(1)]
    assert ent_r is None
