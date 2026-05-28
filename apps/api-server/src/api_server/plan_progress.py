"""Plan progress + lifecycle transitions (Plan 06 Fase H).

Three tasks of Fase H live here (the fourth is documentation):

  * :func:`compute_plan_progress` (06_35) — X/Y task counts + cost
    sum the Kanban renders next to each plan card.
  * :func:`transition_to_pending_human_validation` (06_36) — when
    every task of the plan is ``done``, the plan flips to
    ``pending_human_validation``.
  * :func:`transition_to_completed` (06_37) — after the human
    verdict is ``approved`` AND the PR is merged, the plan flips to
    ``completed``.

The functions are pure and DB-agnostic: they take a snapshot of
(tasks, plan) and return the new (plan_status, side_effects).
Production wires them inside the orchestrator's task-event handler;
tests pass simple lists.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

# Closed set of plan statuses — mirrors docs/roadmap/README.md's
# documented states.
PlanStatus = Literal[
    "pending_approval",
    "approved",
    "in_progress",
    "blocked",
    "pending_human_validation",
    "completed",
    "cancelled",
    "rejected",
    "archived",
]

# Statuses that count a task as "open" — i.e. neither ``done`` nor
# ``cancelled``. A plan still has open tasks ⇒ it can't transition
# to ``pending_human_validation``.
_OPEN_TASK_STATUSES = frozenset(
    {"backlog", "in_progress", "in_review", "awaiting_human", "blocked"}
)


@dataclass(frozen=True)
class PlanProgress:
    """Progress snapshot the Kanban renders for a plan card."""

    plan_id: str
    total: int
    done: int
    open: int
    cost_eur_accumulated: float

    @property
    def label(self) -> str:
        """``X/Y`` label shown on the card."""
        return f"{self.done}/{self.total}"


@dataclass(frozen=True)
class TaskSnapshot:
    """Minimal shape ``compute_plan_progress`` needs.

    Production wires this from ``Task`` rows; tests use dataclass
    literals."""

    id: str
    status: str
    cost_eur: float = 0.0


# ---------------------------------------------------------------------------
# task_06_35 — progress
# ---------------------------------------------------------------------------


def compute_plan_progress(plan_id: str, tasks: Iterable[TaskSnapshot]) -> PlanProgress:
    """Build a :class:`PlanProgress` from a plan's task list.

    ``cancelled`` tasks are excluded from BOTH ``total`` and ``done``
    counters — they don't belong to the user's mental model of "what
    needs to happen for this plan".
    """
    materialised = list(tasks)
    total = 0
    done = 0
    open_count = 0
    cost = 0.0
    for task in materialised:
        if task.status == "cancelled":
            continue
        total += 1
        cost += task.cost_eur
        if task.status == "done":
            done += 1
        elif task.status in _OPEN_TASK_STATUSES:
            open_count += 1
    return PlanProgress(
        plan_id=plan_id,
        total=total,
        done=done,
        open=open_count,
        cost_eur_accumulated=round(cost, 2),
    )


# ---------------------------------------------------------------------------
# task_06_36 — transition to pending_human_validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TransitionResult:
    """The outcome of a transition attempt."""

    new_status: PlanStatus
    transitioned: bool
    reason: str | None = None


def transition_to_pending_human_validation(
    current_status: PlanStatus,
    tasks: Iterable[TaskSnapshot],
) -> TransitionResult:
    """If every non-cancelled task is ``done`` AND the plan is
    ``in_progress``, return a transition to ``pending_human_validation``.
    Otherwise return a no-op result with the reason.
    """
    if current_status != "in_progress":
        return TransitionResult(
            new_status=current_status,
            transitioned=False,
            reason=f"plan is {current_status!r}, only in_progress can transition",
        )

    open_tasks = [t for t in tasks if t.status in _OPEN_TASK_STATUSES or t.status == "in_review"]
    if open_tasks:
        return TransitionResult(
            new_status="in_progress",
            transitioned=False,
            reason=f"{len(open_tasks)} task(s) still open",
        )
    return TransitionResult(
        new_status="pending_human_validation",
        transitioned=True,
    )


# ---------------------------------------------------------------------------
# task_06_37 — transition to completed
# ---------------------------------------------------------------------------


def transition_to_completed(
    current_status: PlanStatus,
    *,
    human_verdict: Literal["approved", "rejected"] | None,
    pr_merged: bool,
) -> TransitionResult:
    """Plan goes to ``completed`` iff human approved AND PRs merged.

    Called by the orchestrator after both the review-runtime emits an
    ``approved`` verdict and the PR-merge webhook fires. Returns a
    no-op when either condition isn't met yet."""
    if current_status != "pending_human_validation":
        return TransitionResult(
            new_status=current_status,
            transitioned=False,
            reason=f"plan is {current_status!r}, only pending_human_validation can complete",
        )
    if human_verdict != "approved":
        return TransitionResult(
            new_status=current_status,
            transitioned=False,
            reason=f"human verdict is {human_verdict!r}, not 'approved'",
        )
    if not pr_merged:
        return TransitionResult(
            new_status=current_status,
            transitioned=False,
            reason="PR not merged yet",
        )
    return TransitionResult(new_status="completed", transitioned=True)


__all__ = [
    "PlanProgress",
    "PlanStatus",
    "TaskSnapshot",
    "TransitionResult",
    "compute_plan_progress",
    "transition_to_completed",
    "transition_to_pending_human_validation",
]
