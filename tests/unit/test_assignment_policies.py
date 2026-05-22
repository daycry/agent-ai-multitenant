"""Unit tests for the orchestrator assignment policies (task_02_03)."""

from __future__ import annotations

import math

import pytest
from orchestrator.assignment import (
    Candidate,
    RoundRobin,
    TaskRequirement,
    assign_load_balanced,
    assign_manual,
    assign_skill_match,
    skill_match_score,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# skill_match_score
# ---------------------------------------------------------------------------
def test_score_identical_sets_is_one() -> None:
    s = frozenset({"python", "fastapi", "sql"})
    assert skill_match_score(s, s) == pytest.approx(1.0)


def test_score_disjoint_sets_is_zero() -> None:
    assert skill_match_score(frozenset({"python"}), frozenset({"rust"})) == 0.0


def test_score_empty_set_is_zero() -> None:
    assert skill_match_score(frozenset(), frozenset({"python"})) == 0.0
    assert skill_match_score(frozenset({"python"}), frozenset()) == 0.0


def test_score_partial_overlap_is_cosine() -> None:
    required = frozenset({"python", "sql"})
    agent = frozenset({"python", "sql", "docker", "redis"})
    # |∩| = 2, cos = 2 / sqrt(2 * 4)
    assert skill_match_score(required, agent) == pytest.approx(2 / math.sqrt(8))


# ---------------------------------------------------------------------------
# skill_match
# ---------------------------------------------------------------------------
def test_skill_match_picks_highest_overlap() -> None:
    req = TaskRequirement("t1", frozenset({"python", "sql"}))
    candidates = [
        Candidate("agent-a", frozenset({"python"})),
        Candidate("agent-b", frozenset({"python", "sql"})),
        Candidate("agent-c", frozenset({"rust"})),
    ]
    assert assign_skill_match(req, candidates) == "agent-b"


def test_skill_match_no_overlap_returns_none() -> None:
    req = TaskRequirement("t1", frozenset({"python"}))
    candidates = [Candidate("agent-a", frozenset({"rust"}))]
    assert assign_skill_match(req, candidates) is None


def test_skill_match_empty_pool_returns_none() -> None:
    req = TaskRequirement("t1", frozenset({"python"}))
    assert assign_skill_match(req, []) is None


def test_skill_match_tie_breaks_toward_lighter_load() -> None:
    # Both agents have the same skill overlap; the lighter-loaded wins.
    req = TaskRequirement("t1", frozenset({"python", "sql"}))
    candidates = [
        Candidate("agent-a", frozenset({"python", "sql"}), active_task_count=5),
        Candidate("agent-b", frozenset({"python", "sql"}), active_task_count=1),
    ]
    assert assign_skill_match(req, candidates) == "agent-b"


def test_skill_match_tie_breaks_deterministically_by_agent_id() -> None:
    # Equal score, equal load → lexically smaller agent_id wins.
    req = TaskRequirement("t1", frozenset({"python"}))
    candidates = [
        Candidate("agent-z", frozenset({"python"}), active_task_count=0),
        Candidate("agent-a", frozenset({"python"}), active_task_count=0),
    ]
    assert assign_skill_match(req, candidates) == "agent-a"


# ---------------------------------------------------------------------------
# load_balanced
# ---------------------------------------------------------------------------
def test_load_balanced_picks_fewest_active_tasks() -> None:
    candidates = [
        Candidate("agent-a", active_task_count=4),
        Candidate("agent-b", active_task_count=1),
        Candidate("agent-c", active_task_count=7),
    ]
    assert assign_load_balanced(candidates) == "agent-b"


def test_load_balanced_tie_breaks_by_agent_id() -> None:
    candidates = [
        Candidate("agent-z", active_task_count=2),
        Candidate("agent-a", active_task_count=2),
    ]
    assert assign_load_balanced(candidates) == "agent-a"


def test_load_balanced_empty_pool_returns_none() -> None:
    assert assign_load_balanced([]) is None


# ---------------------------------------------------------------------------
# manual
# ---------------------------------------------------------------------------
def test_manual_returns_preset_assignee() -> None:
    req = TaskRequirement("t1", preset_agent_id="agent-chosen")
    assert assign_manual(req) == "agent-chosen"


def test_manual_without_preset_returns_none() -> None:
    assert assign_manual(TaskRequirement("t1")) is None


# ---------------------------------------------------------------------------
# round_robin
# ---------------------------------------------------------------------------
def test_round_robin_cycles_through_candidates() -> None:
    rr = RoundRobin()
    pool = [Candidate("agent-a"), Candidate("agent-b"), Candidate("agent-c")]
    picks = [rr.pick(pool) for _ in range(7)]
    # Stable order is sorted by agent_id, then it wraps.
    assert picks == [
        "agent-a",
        "agent-b",
        "agent-c",
        "agent-a",
        "agent-b",
        "agent-c",
        "agent-a",
    ]


def test_round_robin_independent_cursor_per_pool() -> None:
    rr = RoundRobin()
    pool_one = [Candidate("agent-a"), Candidate("agent-b")]
    pool_two = [Candidate("agent-x"), Candidate("agent-y")]
    # Advancing pool_one must not desync pool_two.
    assert rr.pick(pool_one) == "agent-a"
    assert rr.pick(pool_two) == "agent-x"
    assert rr.pick(pool_one) == "agent-b"
    assert rr.pick(pool_two) == "agent-y"


def test_round_robin_empty_pool_returns_none() -> None:
    assert RoundRobin().pick([]) is None
