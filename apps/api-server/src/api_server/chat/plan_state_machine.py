"""Plan lifecycle state machine (Plan 03 task_03_16 / task_03_25).

A plan's status moves through eleven states (`PlanStatus`). Free
transitions would let a caller flip a `completed` plan back to
`draft` and re-execute it, which is exactly the kind of bug the
state machine exists to catch.

This module is the single source of truth for what's legal:

  draft -> pending_approval | cancelled
  pending_approval -> approved | pending_second_approval | rejected
                   |  cancelled
  pending_second_approval -> approved | rejected | cancelled
  approved -> in_progress | cancelled
  in_progress -> blocked | pending_human_validation | cancelled
  blocked -> in_progress | cancelled
  pending_human_validation -> completed | rejected | in_progress
  completed -> archived
  rejected -> draft | archived | in_progress
  cancelled -> archived
  archived -> (terminal)

`pending_second_approval` is the wrinkle for task_03_25: when the AI
cost estimate exceeds the platform's double-signature threshold, the
first signature parks the plan in `pending_second_approval`; a
**different** signer must confirm to reach `approved`. The state
machine asserts that the second signer is not the same user.

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
            PlanStatus.PENDING_SECOND_APPROVAL.value,
            PlanStatus.REJECTED.value,
            PlanStatus.CANCELLED.value,
        }
    ),
    PlanStatus.PENDING_SECOND_APPROVAL.value: frozenset(
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
            # An expired review session escalates the plan to blocked so the
            # operator sees it needs attention (C8 F40 expiry sweep — T4 routed
            # that sweep through this gate instead of a raw assignment).
            PlanStatus.BLOCKED.value,
        }
    ),
    PlanStatus.COMPLETED.value: frozenset({PlanStatus.ARCHIVED.value}),
    PlanStatus.REJECTED.value: frozenset(
        {
            PlanStatus.DRAFT.value,
            PlanStatus.ARCHIVED.value,
            # ADR 0107: aceptar tareas correctivas nacidas del rechazo humano
            # reactiva el plan en el mismo acto que las materializa en el
            # Kanban (accept-corrections). Espejo de blocked -> in_progress.
            PlanStatus.IN_PROGRESS.value,
        }
    ),
    PlanStatus.CANCELLED.value: frozenset({PlanStatus.ARCHIVED.value}),
    PlanStatus.ARCHIVED.value: frozenset(),
}


class PlanTransitionError(ValueError):
    """Raised when a state move is not in the transition table."""

    def __init__(self, from_status: str, to_status: str):
        self.from_status = from_status
        self.to_status = to_status
        super().__init__(f"illegal plan transition: {from_status!r} -> {to_status!r}")


# PROY2-02: transiciones que el PUT genérico (require_tenant_member) NO puede
# ejecutar — pertenecen a endpoints con su propio gate: `approved`/
# `pending_second_approval` a `POST /plans/{id}/approve` (require_can_approve_plan
# + doble firma) y `completed` al veredicto humano (submit_verdict, que además
# encola el auto-PR). Sin esto, cualquier miembro podía aprobar sin rol o
# completar sin veredicto por la mera tabla de adyacencia.
PRIVILEGED_PUT_TARGETS: frozenset[str] = frozenset(
    {
        PlanStatus.APPROVED.value,
        PlanStatus.PENDING_SECOND_APPROVAL.value,
        PlanStatus.COMPLETED.value,
    }
)

_PRIVILEGED_TARGET_ENDPOINT: dict[str, str] = {
    PlanStatus.APPROVED.value: "POST /plans/{id}/approve",
    PlanStatus.PENDING_SECOND_APPROVAL.value: "POST /plans/{id}/approve",
    PlanStatus.COMPLETED.value: "the human review verdict (submit_verdict)",
}


class PlanPutForbiddenError(ValueError):
    """Un `PUT /plans/{id}` intentó una transición privilegiada que debe ir por
    su endpoint con gate (PROY2-02)."""

    def __init__(self, from_status: str, to_status: str, endpoint: str) -> None:
        self.from_status = from_status
        self.to_status = to_status
        self.endpoint = endpoint
        super().__init__(f"transition {from_status!r} -> {to_status!r} must go through {endpoint}")


def assert_generic_put_transition(current: str, target: str) -> None:
    """Rechaza en el PUT genérico las transiciones privilegiadas (PROY2-02).

    No-op si no hay cambio de estado o el destino no es privilegiado; la
    legalidad de la transición la sigue comprobando ``transition_plan_status``.
    """
    if current == target:
        return
    if target in PRIVILEGED_PUT_TARGETS:
        raise PlanPutForbiddenError(
            current, target, _PRIVILEGED_TARGET_ENDPOINT.get(target, "its dedicated endpoint")
        )


def allowed_transitions(from_status: str) -> frozenset[str]:
    """Return the set of legal next states from ``from_status``.

    Empty set means terminal — no further moves allowed.
    """
    return _TRANSITIONS.get(from_status, frozenset())


def is_terminal(status: str) -> bool:
    return not allowed_transitions(status)


class SameSignerError(ValueError):
    """The second signature on a double-firma plan must be a different
    user than the first signature (task_03_25)."""

    def __init__(self, signer: UUID) -> None:
        self.signer = signer
        super().__init__(f"second signer must differ from the first ({signer})")


def transition_plan_status(plan: Plan, target: str, *, actor: UUID | None = None) -> None:
    """Mutate ``plan.status`` if and only if the transition is legal.

    Side-effects:
      - ``pending_approval -> approved`` (single firma): stamps
        ``approved_*`` with ``actor``.
      - ``pending_approval -> pending_second_approval`` (double firma,
        first signature): stamps ``first_approved_*`` with ``actor``.
      - ``pending_second_approval -> approved`` (second signature):
        asserts ``actor`` differs from ``first_approved_by`` and
        stamps ``approved_*``.
      - Other transitions only touch ``status``.

    Raises:
        PlanTransitionError: when ``target`` is not reachable.
        SameSignerError: when closing a double-firma without a
            distinct second signer.
    """
    current = plan.status
    if current == target:
        return
    if target not in allowed_transitions(current):
        raise PlanTransitionError(current, target)

    now = datetime.now(tz=UTC)

    # Double-firma: first signature parks the plan in pending_second_approval.
    if (
        current == PlanStatus.PENDING_APPROVAL.value
        and target == PlanStatus.PENDING_SECOND_APPROVAL.value
    ):
        plan.first_approved_at = now
        if actor is not None:
            plan.first_approved_by = actor

    # Final approval — single or double firma.
    if target == PlanStatus.APPROVED.value:
        # On the second leg of a double-firma, the closing signer must
        # differ from the first one.
        if (
            current == PlanStatus.PENDING_SECOND_APPROVAL.value
            and actor is not None
            and plan.first_approved_by == actor
        ):
            raise SameSignerError(actor)
        plan.approved_at = now
        if actor is not None:
            plan.approved_by = actor

    plan.status = target


__all__ = [
    "PRIVILEGED_PUT_TARGETS",
    "PlanPutForbiddenError",
    "PlanTransitionError",
    "SameSignerError",
    "allowed_transitions",
    "assert_generic_put_transition",
    "is_terminal",
    "transition_plan_status",
]
