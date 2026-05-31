"""Task lifecycle state machine — Domain Service (spec §7.2).

Until now the Task Kanban transitions lived scattered across the
orchestrator (``ready -> in_progress``), the review flow
(``in_review -> done | backlog``), the approval engine
(``in_progress -> awaiting_human_approval -> backlog | blocked``,
ADR 0020) and the REST surface, each assigning ``task.status``
directly. ``plan_state_machine`` already centralised the *plan*
lifecycle; this module is its Task sibling — the single Domain-Service
source of truth for what a Task status move is legal.

It encodes the canonical §7.2 transition table for AI tasks **and** the
Human-Agent transitions added in Plan 16 (task_16_04):

  ready              -> assigned_to_human          (human only)
  assigned_to_human  -> in_progress                (human accepts)
  assigned_to_human  -> assigned_to_human          (reassignment)
  assigned_to_human  -> blocked                    (escalation exhausted)
  in_progress        -> in_review                  (human submits — also AI)

The Human transitions are gated on the **assignee's** ``agent_type``:
they are legal ONLY when the task's assigned agent is ``agent_type=
human``. An AI-assigned task asked to move ``ready -> assigned_to_human``
is rejected with :class:`TaskTransitionError` — the SAME typed error a
structurally-illegal move raises — so the orchestrator (task_16_05) can
branch on agent_type up front and the REST surface can return a focused
409. Existing AI transitions are untouched: an AI task still flows
``backlog -> ready -> in_progress -> in_review -> done`` exactly as
before.

Callers pass the live :class:`AgentType` of the assignee (or ``None``
when the task is unassigned). ``transition_task_status`` mutates
``task.status`` iff the move is legal; ``allowed_transitions`` answers
the same question without mutating, honouring the agent-type gate.
"""

from __future__ import annotations

from api_server.db.domain import AgentType, Task, TaskStatus

# ---------------------------------------------------------------------------
# Canonical AI transition table (spec §7.2).
#
# Adjacency list of legal moves for an AI-assigned (or unassigned) task.
# This is the behaviour that predates Plan 16 — collected from the
# orchestrator dispatch (ready -> in_progress, in_progress -> ready on
# enqueue-failure revert), the review flow (in_review -> done | backlog),
# ADR 0020's approval cycle (in_progress -> awaiting_human_approval ->
# backlog | blocked) and the escalation/free-task paths. Terminal states
# (done / cancelled) map to an empty set.
# ---------------------------------------------------------------------------
_AI_TRANSITIONS: dict[str, frozenset[str]] = {
    TaskStatus.BACKLOG.value: frozenset(
        {
            TaskStatus.READY.value,
            TaskStatus.CANCELLED.value,
        }
    ),
    TaskStatus.READY.value: frozenset(
        {
            TaskStatus.IN_PROGRESS.value,
            # Stale/unblock back to backlog (e.g. re-queue, DAG churn).
            TaskStatus.BACKLOG.value,
            TaskStatus.BLOCKED.value,
            TaskStatus.CANCELLED.value,
        }
    ),
    TaskStatus.IN_PROGRESS.value: frozenset(
        {
            TaskStatus.IN_REVIEW.value,
            TaskStatus.AWAITING_HUMAN_APPROVAL.value,
            TaskStatus.BLOCKED.value,
            TaskStatus.DONE.value,
            # Orchestrator reverts a failed-enqueue dispatch to `ready`.
            TaskStatus.READY.value,
            TaskStatus.CANCELLED.value,
        }
    ),
    TaskStatus.AWAITING_HUMAN_APPROVAL.value: frozenset(
        {
            # Approve -> back into the pipeline (ADR 0020).
            TaskStatus.BACKLOG.value,
            # Reject / timeout -> blocked (ADR 0020).
            TaskStatus.BLOCKED.value,
            TaskStatus.CANCELLED.value,
        }
    ),
    TaskStatus.IN_REVIEW.value: frozenset(
        {
            # Review approved.
            TaskStatus.DONE.value,
            # Review rejected -> back to backlog with retry_count++ (06_34b1).
            TaskStatus.BACKLOG.value,
            # Reviewer asks the worker to keep iterating (legacy review flow).
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.BLOCKED.value,
            TaskStatus.CANCELLED.value,
        }
    ),
    TaskStatus.BLOCKED.value: frozenset(
        {
            TaskStatus.BACKLOG.value,
            TaskStatus.READY.value,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.CANCELLED.value,
        }
    ),
    TaskStatus.DONE.value: frozenset(),
    TaskStatus.CANCELLED.value: frozenset(),
    # `assigned_to_human` is never reachable on the AI table — only the
    # human overlay below adds the edges into and out of it.
    TaskStatus.ASSIGNED_TO_HUMAN.value: frozenset(),
}

# ---------------------------------------------------------------------------
# Human-Agent overlay (Plan 16 §7.2 / task_16_04).
#
# Extra edges that are legal ONLY when the task's assignee is a Human
# Agent (agent_type=human). They are MERGED on top of the AI table for
# a human-assigned task, never available to an AI task — that is what
# makes `ready -> assigned_to_human` rejected for an AI assignee.
# ---------------------------------------------------------------------------
_HUMAN_OVERLAY: dict[str, frozenset[str]] = {
    # The orchestrator routes a ready human task here instead of asking
    # the pool for a container (task_16_05).
    TaskStatus.READY.value: frozenset({TaskStatus.ASSIGNED_TO_HUMAN.value}),
    TaskStatus.ASSIGNED_TO_HUMAN.value: frozenset(
        {
            # The assigned User accepts -> work starts.
            TaskStatus.IN_PROGRESS.value,
            # Reassignment (escalation target / manual hand-off): stays in
            # assigned_to_human, a new assignment row is what changes.
            TaskStatus.ASSIGNED_TO_HUMAN.value,
            # Acceptance timeout exhausted -> blocked (task_16_06).
            TaskStatus.BLOCKED.value,
            TaskStatus.CANCELLED.value,
        }
    ),
    # `in_progress -> in_review` is already legal on the AI table; the
    # human submit path reuses it. Listed here for documentation parity.
    TaskStatus.IN_PROGRESS.value: frozenset({TaskStatus.IN_REVIEW.value}),
}


class TaskTransitionError(ValueError):
    """Raised when a Task status move is not legal for the assignee.

    ``agent_type`` records the assignee's type at the time of the
    rejection so the caller can tell apart a structurally-illegal move
    from one rejected *because the assignee is AI* (e.g.
    ``ready -> assigned_to_human`` on an AI task).
    """

    def __init__(
        self,
        from_status: str,
        to_status: str,
        *,
        agent_type: str | None = None,
    ) -> None:
        self.from_status = from_status
        self.to_status = to_status
        self.agent_type = agent_type
        suffix = f" (agent_type={agent_type})" if agent_type is not None else ""
        super().__init__(f"illegal task transition: {from_status!r} -> {to_status!r}{suffix}")


def _agent_type_value(agent_type: AgentType | str | None) -> str | None:
    """Normalise the assignee agent type to its string value (or None)."""
    if agent_type is None:
        return None
    if isinstance(agent_type, AgentType):
        return agent_type.value
    return str(agent_type)


def allowed_transitions(
    from_status: str,
    *,
    assignee_agent_type: AgentType | str | None = None,
) -> frozenset[str]:
    """Legal next states from ``from_status`` for the given assignee type.

    For an AI assignee (or an unassigned task) this is the canonical §7.2
    AI table. For a Human assignee the Human overlay edges are merged on
    top — so ``ready`` gains ``assigned_to_human`` and the
    ``assigned_to_human`` state becomes reachable/leavable. An empty set
    means terminal — no further moves allowed.
    """
    base = _AI_TRANSITIONS.get(from_status, frozenset())
    if _agent_type_value(assignee_agent_type) == AgentType.HUMAN.value:
        return base | _HUMAN_OVERLAY.get(from_status, frozenset())
    return base


def is_terminal(status: str) -> bool:
    """True when no transition leaves ``status`` for any assignee type."""
    return not (_AI_TRANSITIONS.get(status, frozenset()) | _HUMAN_OVERLAY.get(status, frozenset()))


def can_transition(
    from_status: str,
    to_status: str,
    *,
    assignee_agent_type: AgentType | str | None = None,
) -> bool:
    """Pure predicate: is ``from_status -> to_status`` legal for this assignee?

    A self-loop is legal only when the table explicitly lists it (the
    reassignment ``assigned_to_human -> assigned_to_human`` edge for a
    human assignee) — unlike :func:`transition_task_status`, which treats
    an unchanged status as a harmless no-op.
    """
    return to_status in allowed_transitions(from_status, assignee_agent_type=assignee_agent_type)


def transition_task_status(
    task: Task,
    target: str,
    *,
    assignee_agent_type: AgentType | str | None = None,
) -> None:
    """Mutate ``task.status`` iff the transition is legal for the assignee.

    ``assignee_agent_type`` is the live :class:`AgentType` of the task's
    assigned agent (``None`` when unassigned). The Human transitions of
    §7.2 (``ready -> assigned_to_human`` and the moves around it) are
    accepted ONLY when it is :attr:`AgentType.HUMAN`; an AI assignee asked
    to make one is rejected with :class:`TaskTransitionError`.

    A no-op (``target == task.status``) returns silently — EXCEPT the
    reassignment self-loop ``assigned_to_human -> assigned_to_human``,
    which is a real, table-listed move and so is allowed to "succeed"
    without raising even though the status string does not change.

    Raises:
        TaskTransitionError: when ``target`` is not reachable from the
            current status for this assignee type.
    """
    current = task.status
    allowed = allowed_transitions(current, assignee_agent_type=assignee_agent_type)
    if current == target and target not in allowed:
        # Idempotent no-op for an unchanged status that is not itself a
        # legal self-loop (mirrors plan_state_machine's early return).
        return
    if target not in allowed:
        raise TaskTransitionError(
            current,
            target,
            agent_type=_agent_type_value(assignee_agent_type),
        )
    task.status = target


__all__ = [
    "TaskTransitionError",
    "allowed_transitions",
    "can_transition",
    "is_terminal",
    "transition_task_status",
]
