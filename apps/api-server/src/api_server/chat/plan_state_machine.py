"""Plan lifecycle state machine (Plan 03 task_03_16).

A plan's status moves through ten states (`PlanStatus`). Free
transitions would let a caller flip a `completed` plan back to
`draft` and re-execute it, which is exactly the kind of bug the
state machine exists to catch.

This module is the single source of truth for what's legal:

  draft -> pending_approval | cancelled
  pending_approval -> approved | rejected | cancelled
  approved -> in_progress | cancelled
  in_progress -> blocked | pending_human_validation | cancelled
  blocked -> in_progress | cancelled
  pending_human_validation -> completed | rejected | in_progress
  completed -> archived
  rejected -> draft | archived
  cancelled -> archived
  archived -> (terminal)

The router (and the agent loop that drives execution transitions)
calls `transition_plan_status(plan, target)` rather than assigning
`plan.status` directly. Bad transitions raise
`PlanTransitionError` with both endpoints so the caller can return a
focused 409 to the client.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from api_server.db.domain import Plan, PlanStatus

# Adjacency list of legal transitions.
_TRANSITIONS: dict[str, frozenset[str]] = {
    PlanStatus.DRAFT.value: frozenset(
        {PlanStatus.PENDING_APPROVAL.value, PlanStatus.CANCELLED.value}
    ),
    PlanStatus.PENDING_APPROVAL.value: frozenset(
        {
            PlanStatus.APPROVED.value,
            PlanStatus.REJECTED.value,
            PlanStatus.CANCELLED.value,
        }
    ),
    PlanStatus.APPROVED.value: frozenset(
        {PlanStatus.IN_PROGRESS.value, PlanStatus.CANCELLED.value}
    ),
    PlanStatus.IN_PROGRESS.value: frozenset(
        {
            PlanStatus.BLOCKED.value,
            PlanStatus.PENDING_HUMAN_VALIDATION.value,
            PlanStatus.CANCELLED.value,
        }
    ),
    PlanStatus.BLOCKED.value: frozenset({PlanStatus.IN_PROGRESS.value, PlanStatus.CANCELLED.value}),
    PlanStatus.PENDING_HUMAN_VALIDATION.value: frozenset(
        {
            PlanStatus.COMPLETED.value,
            PlanStatus.REJECTED.value,
            # If the reviewer asks for changes the plan goes back to
            # in_progress so the team can iterate without losing the
            # already-approved status.
            PlanStatus.IN_PROGRESS.value,
        }
    ),
    PlanStatus.COMPLETED.value: frozenset({PlanStatus.ARCHIVED.value}),
    PlanStatus.REJECTED.value: frozenset({PlanStatus.DRAFT.value, PlanStatus.ARCHIVED.value}),
    PlanStatus.CANCELLED.value: frozenset({PlanStatus.ARCHIVED.value}),
    PlanStatus.ARCHIVED.value: frozenset(),
}


class PlanTransitionError(ValueError):
    """Raised when a state move is not in the transition table."""

    def __init__(self, from_status: str, to_status: str):
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"illegal plan transition: {from_status!r} -> {to_status!r}")


def allowed_transitions(from_status: str) -> frozenset[str]:
    """Return the set of legal next states from ``from_status``.

    Empty set means terminal — no further moves allowed.
    """
    return _TRANSITIONS.get(from_status, frozenset())


def is_terminal(status: str) -> bool:
    return not allowed_transitions(status)


def transition_plan_status(plan: Plan, target: str, *, actor: UUID | None = None) -> None:
    """Mutate ``plan.status`` if and only if the transition is legal.

    Side-effects:
      - When moving INTO ``approved``, stamps ``approved_at`` and
        ``approved_by`` (if ``actor`` is given).
      - Other transitions only touch ``status``.

    Raises:
        PlanTransitionError: when ``target`` is not reachable from the
            plan's current status.
    """
    current = plan.status
    if current == target:
        return
    if target not in allowed_transitions(current):
        raise PlanTransitionError(current, target)
    plan.status = target
    if target == PlanStatus.APPROVED.value:
        plan.approved_at = datetime.now(tz=UTC)
        if actor is not None:
            plan.approved_by = actor


__all__ = [
    "PlanTransitionError",
    "allowed_transitions",
    "is_terminal",
    "transition_plan_status",
]
