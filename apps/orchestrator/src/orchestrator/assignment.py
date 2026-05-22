"""Task → agent assignment policies (task_02_03).

Four policies pick which agent runs a task:

  skill_match    cosine similarity between the task's required skills
                 and each candidate's skill set — best overlap wins.
  load_balanced  the candidate with the fewest active tasks.
  round_robin    cycle through candidates in a stable order.
  manual         no auto-pick; the task keeps its preset assignee.

Cosine similarity here is over *skill sets* as binary vectors — no
embeddings (those arrive with RAG in Plan 04). For two sets A, B the
multi-hot vectors give:

    cos(A, B) = |A ∩ B| / sqrt(|A| · |B|)

Pure, deterministic logic — unit-tested, no I/O.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field


class AssignmentPolicy(enum.StrEnum):
    SKILL_MATCH = "skill_match"
    LOAD_BALANCED = "load_balanced"
    ROUND_ROBIN = "round_robin"
    MANUAL = "manual"


@dataclass(frozen=True)
class Candidate:
    """An agent eligible to take a task."""

    agent_id: str
    skills: frozenset[str] = field(default_factory=frozenset)
    active_task_count: int = 0


@dataclass(frozen=True)
class TaskRequirement:
    """What a task needs in order to be assigned."""

    task_id: str
    required_skills: frozenset[str] = field(default_factory=frozenset)
    # Used by the `manual` policy: the assignee a human already set.
    preset_agent_id: str | None = None


def skill_match_score(required: frozenset[str], agent_skills: frozenset[str]) -> float:
    """Cosine similarity of two skill sets as binary vectors.

    Returns 0.0 when either set is empty (no signal to match on).
    Range [0.0, 1.0]; 1.0 means identical skill sets.
    """
    if not required or not agent_skills:
        return 0.0
    intersection = len(required & agent_skills)
    return intersection / math.sqrt(len(required) * len(agent_skills))


def assign_skill_match(req: TaskRequirement, candidates: list[Candidate]) -> str | None:
    """Pick the candidate with the highest skill cosine similarity.

    Ties break toward the lighter-loaded agent, then the lexically
    smaller agent_id — so the result is fully deterministic.
    """
    if not candidates:
        return None
    scored = [
        (skill_match_score(req.required_skills, c.skills), -c.active_task_count, c)
        for c in candidates
    ]
    score, _neg_load, best = max(scored, key=lambda t: (t[0], t[1], _inv_agent_id(t[2].agent_id)))
    # A zero score means nothing overlapped — no meaningful match.
    return best.agent_id if score > 0.0 else None


def assign_load_balanced(candidates: list[Candidate]) -> str | None:
    """Pick the candidate with the fewest active tasks (ties: agent_id)."""
    if not candidates:
        return None
    best = min(candidates, key=lambda c: (c.active_task_count, c.agent_id))
    return best.agent_id


def assign_manual(req: TaskRequirement) -> str | None:
    """Manual policy: keep whatever assignee the task already carries."""
    return req.preset_agent_id


class RoundRobin:
    """Stateful round-robin picker.

    Keeps a cursor so successive calls rotate through the candidate
    pool. The cursor is keyed by the *sorted tuple of agent ids* so a
    changing pool doesn't desync the rotation for an unrelated pool.
    """

    def __init__(self) -> None:
        self._cursors: dict[tuple[str, ...], int] = {}

    def pick(self, candidates: list[Candidate]) -> str | None:
        if not candidates:
            return None
        ordered = sorted(candidates, key=lambda c: c.agent_id)
        key = tuple(c.agent_id for c in ordered)
        cursor = self._cursors.get(key, 0) % len(ordered)
        self._cursors[key] = cursor + 1
        return ordered[cursor].agent_id


def _inv_agent_id(agent_id: str) -> tuple[int, ...]:
    """Sort key that makes the lexically smaller agent_id rank higher
    when used inside a `max(...)` (we want min agent_id to win ties)."""
    return tuple(-ord(ch) for ch in agent_id)
