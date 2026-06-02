"""Human-task review modes (Plan 16 Fase D, task_16_11).

Wires ``project.human_task_review_mode`` into the human-task completion flow —
the task_16_09 delivery path that creates a
:class:`~api_server.db.domain.HumanWorkSession` and moves the Task
``in_progress -> in_review``. Two modes (Plan 16 Alcance; ``ai_reviewer`` is
out of scope this plan):

  * ``auto_approve`` (DEFAULT) — the task transitions straight to ``done``; no
    extra review step. The act of submitting IS the completion.
  * ``peer_human_reviewer`` — the task stays ``in_review`` and a SECOND
    :class:`~api_server.db.domain.HumanTaskAssignment` is created for ANOTHER
    Human Agent (the reviewer, resolved from ``task.reviewer_agent_id`` ->
    ``human_agent_config.assigned_user_id``). That reviewer approves
    (-> ``done``) or rejects with ``feedback_text`` (-> back to ``backlog``
    with ``retry_count`` bumped; after ``max_retries`` the §7.9 retry/
    escalation infra parks the task in ``blocked`` and alerts the Tenant
    Admin — the SAME ``blocked`` + ``task_blocked`` escalation the
    acceptance-timeout sweep, task_16_06, uses).

Reuse, not reinvention
----------------------
The reject path mirrors the AI-task review/retry mechanism
(:mod:`api_server.reviewer_bridge` / :mod:`api_server.task_lifecycle`): task to
``backlog``, ``retry_count += 1``, a structured review comment audited. The
escalation-on-exhaustion mirrors the human §7.9 infra
(:mod:`api_server.human_agents.escalation`): ``-> blocked`` + a ``task_blocked``
fan-out to the tenant admins. Every transition goes through
:func:`api_server.task_state_machine.transition_task_status` (gated on the
Human assignee type); the verdict (``reviewer_user_id`` + ``verdict`` +
``feedback_text``) is recorded as an auditable ``task_audit_events`` row.

Multi-tenancy (NON-NEGOTIABLE)
------------------------------
Every read/write is scoped to the task's OWN ``tenant_id``: the project's
review mode, the reviewer-agent resolution and the reviewer-user resolution all
carry an explicit ``tenant_id`` predicate, so a forged cross-tenant
``reviewer_agent_id`` resolves to None (the task simply stays ``in_review`` with
no reviewer assignment) and a peer review can never land on another tenant's
user. Under RLS (the API path) the predicate is belt-and-braces; the helpers
also work BYPASSRLS (workers) where it is the only guard.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import (
    Agent,
    AgentType,
    HumanAgentConfig,
    HumanTaskAssignment,
    HumanTaskAssignmentStatus,
    HumanTaskReviewMode,
    Project,
    Task,
    TaskStatus,
)
from api_server.db.task_audit_repo import append_audit_event
from api_server.task_state_machine import transition_task_status

#: The verdict kinds a peer reviewer can return.
VERDICT_APPROVED = "approved"
VERDICT_REJECTED = "rejected"

#: ``task_blocked`` fans out to the tenant's admins when review retries are
#: exhausted — the SAME event the acceptance-timeout sweep (task_16_06) uses to
#: alert the admin (registered in the dispatcher EVENT_REGISTRY + templates).
TASK_BLOCKED_EVENT = "task_blocked"


@dataclass(frozen=True)
class PeerReviewRouted:
    """The outcome of routing a submitted human task into peer review.

    ``review_assignment_id`` is the fresh reviewer :class:`HumanTaskAssignment`
    (None when no reviewer Human Agent / user could be resolved — the task then
    stays ``in_review`` with no reviewer, surfaced for an admin to fix).
    ``reviewer_user_id`` is the concrete User the review landed on.
    """

    review_assignment_id: UUID | None
    reviewer_user_id: UUID | None


@dataclass(frozen=True)
class PeerReviewVerdictResult:
    """The outcome of applying a peer reviewer's verdict.

    ``action`` is one of ``approved`` / ``rejected`` / ``escalated``; on a
    rejection ``task_status`` is ``backlog`` (or ``blocked`` when escalated) and
    ``retry_count`` is the post-increment value. ``escalated`` is True only when
    the rejection exhausted ``max_retries`` and the task was blocked for human
    attention (§7.9).
    """

    action: str
    task_status: str
    retry_count: int
    escalated: bool
    review_event_id: UUID


async def resolve_review_mode(
    session: AsyncSession, *, project_id: UUID, tenant_id: UUID
) -> HumanTaskReviewMode:
    """The task's project review mode (tenant-scoped), defaulting to auto_approve.

    A project that is somehow not visible (deleted / cross-tenant) falls back to
    ``auto_approve`` — the safe MVP default (the task completes rather than
    stranding in ``in_review`` with no reviewer)."""
    mode = (
        await session.execute(
            select(Project.human_task_review_mode).where(
                Project.id == project_id,
                Project.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if mode is None:
        return HumanTaskReviewMode.AUTO_APPROVE
    return HumanTaskReviewMode(mode)


async def _resolve_reviewer(
    session: AsyncSession, *, task: Task, tenant_id: UUID
) -> tuple[UUID | None, UUID | None]:
    """Resolve the peer reviewer (Human Agent id, User id) for a task.

    The reviewer is the task's ``reviewer_agent_id`` when it names a Human Agent
    in the SAME tenant; the concrete User comes from that agent's
    ``human_agent_config.assigned_user_id``. Returns ``(None, None)`` when there
    is no reviewer agent, it is not a Human Agent, or it has no config — the
    caller then leaves the task in ``in_review`` without an assignment.
    """
    if task.reviewer_agent_id is None:
        return None, None
    agent_type = (
        await session.execute(
            select(Agent.agent_type).where(
                Agent.id == task.reviewer_agent_id,
                Agent.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if agent_type != AgentType.HUMAN.value:
        return None, None
    reviewer_user_id = (
        await session.execute(
            select(HumanAgentConfig.assigned_user_id).where(
                HumanAgentConfig.agent_id == task.reviewer_agent_id,
                HumanAgentConfig.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    return task.reviewer_agent_id, reviewer_user_id


async def route_into_peer_review(
    session: AsyncSession, *, task: Task, tenant_id: UUID
) -> PeerReviewRouted:
    """Create the reviewer assignment for a task already moved to ``in_review``.

    Resolves the peer reviewer Human Agent + User from ``task.reviewer_agent_id``
    and inserts a fresh ``pending_acceptance`` :class:`HumanTaskAssignment` for
    them (the SECOND assignment on the task — the original work assignment stays
    ``accepted`` as the historical record). The task is NOT transitioned here —
    the caller has already moved it ``in_progress -> in_review``; peer review
    simply leaves it there until the reviewer rules.
    """
    reviewer_agent_id, reviewer_user_id = await _resolve_reviewer(
        session, task=task, tenant_id=tenant_id
    )
    if reviewer_agent_id is None:
        return PeerReviewRouted(review_assignment_id=None, reviewer_user_id=None)
    review_assignment = HumanTaskAssignment(
        tenant_id=tenant_id,
        task_id=task.id,
        human_agent_id=reviewer_agent_id,
        assigned_to_user_id=reviewer_user_id,
        assigned_at=datetime.now(UTC),
        status=HumanTaskAssignmentStatus.PENDING_ACCEPTANCE.value,
    )
    session.add(review_assignment)
    await session.flush()  # populate review_assignment.id
    await append_audit_event(
        session,
        tenant_id=tenant_id,
        task_id=task.id,
        kind="peer_review_requested",
        actor="system",
        payload={
            "review_assignment_id": str(review_assignment.id),
            "reviewer_agent_id": str(reviewer_agent_id),
            "reviewer_user_id": (str(reviewer_user_id) if reviewer_user_id is not None else None),
        },
    )
    return PeerReviewRouted(
        review_assignment_id=review_assignment.id,
        reviewer_user_id=reviewer_user_id,
    )


async def approve_peer_review(
    session: AsyncSession,
    *,
    task: Task,
    tenant_id: UUID,
    reviewer_user_id: UUID,
) -> PeerReviewVerdictResult:
    """The peer reviewer approves: Task ``in_review -> done`` (§7.2).

    Records an auditable ``peer_review_verdict`` event carrying the reviewer +
    the ``approved`` verdict, then completes the task. The reviewer is a Human
    assignee, so the move is the SAME ``in_review -> done`` edge the AI review
    approve path uses.
    """
    transition_task_status(task, TaskStatus.DONE.value, assignee_agent_type=AgentType.HUMAN)
    if task.completed_at is None:
        task.completed_at = datetime.now(UTC)
    event = await append_audit_event(
        session,
        tenant_id=tenant_id,
        task_id=task.id,
        kind="peer_review_verdict",
        actor=f"user:{reviewer_user_id}",
        payload={
            "verdict": VERDICT_APPROVED,
            "reviewer_user_id": str(reviewer_user_id),
        },
    )
    return PeerReviewVerdictResult(
        action=VERDICT_APPROVED,
        task_status=task.status,
        retry_count=task.retry_count,
        escalated=False,
        review_event_id=event.id,
    )


async def reject_peer_review(
    session: AsyncSession,
    *,
    task: Task,
    tenant_id: UUID,
    reviewer_user_id: UUID,
    feedback_text: str,
) -> PeerReviewVerdictResult:
    """The peer reviewer rejects with feedback (§7.9 retry/escalation).

    Mirrors the AI-task review/retry mechanism
    (:func:`api_server.reviewer_bridge.apply_reviewer_verdict`): the task goes
    back to ``backlog`` (``in_review -> backlog``, §7.2), ``retry_count`` is
    incremented, and the ``feedback_text`` + reviewer are recorded as an
    auditable ``peer_review_verdict`` event (the rework comments travel with the
    task).

    When the increment reaches ``task.max_retries`` (the per-task
    max_review_retries; §7.9 default 3), the task is ESCALATED instead of
    returned to ``backlog``: it transitions ``in_review -> blocked`` for human
    attention — the SAME ``blocked`` escalation the human acceptance-timeout
    sweep (task_16_06) uses — and the caller fans a ``task_blocked`` event out
    to the tenant admins.
    """
    # The increment decides the target BEFORE transitioning: a non-exhausting
    # reject returns the task to backlog for rework; the exhausting one escalates
    # straight to blocked (in_review -> blocked, §7.2 — no backlog hop, which the
    # state machine would not allow on to blocked).
    next_retry_count = task.retry_count + 1
    escalated = next_retry_count >= task.max_retries
    target = TaskStatus.BLOCKED.value if escalated else TaskStatus.BACKLOG.value
    transition_task_status(task, target, assignee_agent_type=AgentType.HUMAN)
    task.retry_count = next_retry_count
    event = await append_audit_event(
        session,
        tenant_id=tenant_id,
        task_id=task.id,
        kind="peer_review_verdict",
        actor=f"user:{reviewer_user_id}",
        payload={
            "verdict": VERDICT_REJECTED,
            "reviewer_user_id": str(reviewer_user_id),
            "feedback_text": feedback_text,
            "retry_count": task.retry_count,
            "escalated": escalated,
        },
    )
    if escalated:
        # Retries exhausted (§7.9): record the escalation transition for audit.
        await append_audit_event(
            session,
            tenant_id=tenant_id,
            task_id=task.id,
            kind="transition",
            actor="system",
            payload={
                "from": TaskStatus.IN_REVIEW.value,
                "to": TaskStatus.BLOCKED.value,
                "reason": "max_review_retries",
                "retry_count": task.retry_count,
            },
        )
    return PeerReviewVerdictResult(
        action="escalated" if escalated else VERDICT_REJECTED,
        task_status=task.status,
        retry_count=task.retry_count,
        escalated=escalated,
        review_event_id=event.id,
    )


__all__ = [
    "TASK_BLOCKED_EVENT",
    "VERDICT_APPROVED",
    "VERDICT_REJECTED",
    "PeerReviewRouted",
    "PeerReviewVerdictResult",
    "approve_peer_review",
    "reject_peer_review",
    "resolve_review_mode",
    "route_into_peer_review",
]
