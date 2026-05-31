"""``/inbox`` endpoints — the personal "Tareas asignadas a mí" tray (task_16_08).

The inbox is the per-USER view of human-task work: the CALLER's OWN active
:class:`~api_server.db.domain.HumanTaskAssignment` rows, plus the four
contextual actions the assignee can take on them:

  GET    /inbox/assignments                       the caller's active assignments
  POST   /inbox/assignments/{id}/accept           pending_acceptance -> accepted
  POST   /inbox/assignments/{id}/reject           decline with a justification
  POST   /inbox/assignments/{id}/complete         submit -> task in_review
  POST   /inbox/assignments/{id}/escalate         hand to the Tenant Admin

Auth + scoping (NON-NEGOTIABLE)
-------------------------------
Any active tenant member can use the inbox (``require_tenant_member``) — it is
NOT admin-only: it is the personal tray of whoever is logged in. RLS scopes
every row to the caller's tenant; ON TOP of that, every read and every action
filters on ``HumanTaskAssignment.assigned_to_user_id == principal.user_id`` so a
user sees and acts ONLY on their OWN assignments. Someone else's assignment id
(same tenant, different user) — or a cross-tenant id — resolves to 404; the
caller cannot tell an unknown id from one that belongs to another user.

Each action applies the right ``HumanTaskAssignment`` status AND the matching
Task §7.2 transition via the task_16_04 state machine (gated on the assignee
being a Human Agent), and appends a ``task_audit_events`` row recording the
actor + action. ``escalate`` additionally fans a ``task_blocked`` event out to
the tenant's admins (best-effort — a broker outage never rolls back the move).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_member,
)
from api_server.celery_client import enqueue_event_dispatch
from api_server.db.domain import (
    Agent,
    AgentType,
    HumanAgentConfig,
    HumanTaskAssignment,
    HumanTaskAssignmentStatus,
    HumanWorkSession,
    Plan,
    Project,
    Task,
    TaskStatus,
)
from api_server.db.task_audit_repo import append_audit_event
from api_server.routers._helpers import require_tenant_id
from api_server.routers._pagination import apply_pagination, limit_query, offset_query
from api_server.schemas.human_inbox import (
    InboxAction,
    InboxActionRequest,
    InboxActionResult,
    InboxAssignmentResponse,
    InboxSubmitRequest,
    InboxSubmitResult,
)
from api_server.task_state_machine import TaskTransitionError, transition_task_status

router = APIRouter(prefix="/inbox", tags=["human-inbox"])

#: The assignment statuses that count as "active" — what the inbox shows. A
#: reassigned/declined/expired row is history, not actionable by this user.
_ACTIVE_ASSIGNMENT_STATUSES = (
    HumanTaskAssignmentStatus.PENDING_ACCEPTANCE.value,
    HumanTaskAssignmentStatus.ACCEPTED.value,
)

#: ``task_blocked`` fans out to the tenant's admins (the escalate path). Same
#: event the acceptance-timeout sweep (task_16_06) uses to alert the admin.
_TASK_BLOCKED_EVENT = "task_blocked"


# ---------------------------------------------------------------------------
# GET /inbox/assignments — the caller's own active assignments
# ---------------------------------------------------------------------------
@router.get("/assignments", response_model=list[InboxAssignmentResponse])
async def list_my_assignments(
    limit: int = limit_query(),
    offset: int = offset_query(),
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[InboxAssignmentResponse]:
    """List the CALLER user's active human-task assignments.

    Returns only ``pending_acceptance`` / ``accepted`` assignments whose
    ``assigned_to_user_id`` is the caller, joined to their Task / project /
    plan context (title, status, the project name, the plan title) and the
    acceptance deadline (assigned_at + the Human Agent's
    acceptance_timeout_hours) so the tray can flag what is about to expire.
    """
    tenant_id = require_tenant_id(principal)
    stmt = (
        select(
            HumanTaskAssignment,
            Task,
            Project.name,
            Plan.title,
            HumanAgentConfig.acceptance_timeout_hours,
        )
        .join(Task, Task.id == HumanTaskAssignment.task_id)
        .join(Project, Project.id == Task.project_id)
        .outerjoin(Plan, Plan.id == Task.plan_id)
        .outerjoin(
            HumanAgentConfig,
            (HumanAgentConfig.agent_id == HumanTaskAssignment.human_agent_id)
            & (HumanAgentConfig.tenant_id == HumanTaskAssignment.tenant_id),
        )
        .where(
            HumanTaskAssignment.assigned_to_user_id == principal.user_id,
            HumanTaskAssignment.tenant_id == tenant_id,
            HumanTaskAssignment.status.in_(_ACTIVE_ASSIGNMENT_STATUSES),
        )
        .order_by(HumanTaskAssignment.assigned_at.desc(), HumanTaskAssignment.id)
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    rows = (await session.execute(stmt)).all()
    return [
        InboxAssignmentResponse(
            assignment_id=assignment.id,
            task_id=task.id,
            human_agent_id=assignment.human_agent_id,
            assignment_status=assignment.status,
            task_status=task.status,
            assigned_at=assignment.assigned_at,
            acceptance_deadline=_acceptance_deadline(assignment, timeout_hours),
            task_title=task.title,
            task_description=task.description,
            project_id=task.project_id,
            project_name=project_name,
            plan_id=task.plan_id,
            plan_title=plan_title,
        )
        for assignment, task, project_name, plan_title, timeout_hours in rows
    ]


def _acceptance_deadline(
    assignment: HumanTaskAssignment, timeout_hours: int | None
) -> datetime | None:
    """The moment a ``pending_acceptance`` window lapses, else ``None``.

    Only meaningful while the user still has to accept; once the task is
    ``accepted`` there is no acceptance deadline to surface."""
    if assignment.status != HumanTaskAssignmentStatus.PENDING_ACCEPTANCE.value:
        return None
    hours = timeout_hours if timeout_hours is not None else 24
    return assignment.assigned_at + timedelta(hours=hours)


# ---------------------------------------------------------------------------
# The four actions
# ---------------------------------------------------------------------------
async def _load_my_assignment_or_404(
    session: AsyncSession, assignment_id: UUID, principal: AuthPrincipal, tenant_id: UUID
) -> tuple[HumanTaskAssignment, Task]:
    """Load the caller's OWN assignment + its Task, or 404.

    Scoped on ``assigned_to_user_id == caller`` AND ``tenant_id`` (belt-and-
    braces over RLS) so another user's — or another tenant's — assignment id is
    indistinguishable from an unknown one. The Task is loaded under the same
    tenant predicate."""
    assignment = (
        await session.execute(
            select(HumanTaskAssignment).where(
                HumanTaskAssignment.id == assignment_id,
                HumanTaskAssignment.assigned_to_user_id == principal.user_id,
                HumanTaskAssignment.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if assignment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="assignment not found")
    task = (
        await session.execute(
            select(Task).where(
                Task.id == assignment.task_id,
                Task.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    return assignment, task


async def _assert_human_assignee(
    session: AsyncSession, assignment: HumanTaskAssignment, tenant_id: UUID
) -> None:
    """Sanity-guard: the assignment's agent must still be a Human Agent.

    The §7.2 human transitions are legal ONLY for a human assignee. If the
    agent was deleted (``human_agent_id`` SET NULL) we still allow the move —
    the assignment row IS the human-route marker — but a non-human agent would
    be a data bug; reject it with a focused 409 rather than mis-transition."""
    if assignment.human_agent_id is None:
        return
    agent_type = (
        await session.execute(
            select(Agent.agent_type).where(
                Agent.id == assignment.human_agent_id,
                Agent.tenant_id == tenant_id,
            )
        )
    ).scalar_one_or_none()
    if agent_type is not None and agent_type != AgentType.HUMAN.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="assignment is not bound to a human agent",
        )


def _transition_or_409(task: Task, target: str) -> None:
    """Apply a §7.2 human transition or raise a focused 409 Conflict.

    All inbox transitions are made as a Human assignee (the assignment IS the
    human route); an illegal move (e.g. accepting a task that is no longer
    ``assigned_to_human``) surfaces the state machine's typed error as a 409."""
    try:
        transition_task_status(task, target, assignee_agent_type=AgentType.HUMAN)
    except TaskTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"illegal task transition: {exc.from_status} -> {exc.to_status}",
        ) from exc


async def _record_action(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    task_id: UUID,
    principal: AuthPrincipal,
    action: InboxAction,
    extra: dict[str, object] | None = None,
) -> None:
    """Append a ``human_inbox_action`` audit event for the move."""
    payload: dict[str, object] = {"action": action.value, "actor_user_id": str(principal.user_id)}
    if extra:
        payload.update(extra)
    await append_audit_event(
        session,
        tenant_id=tenant_id,
        task_id=task_id,
        kind="human_inbox_action",
        actor=f"user:{principal.user_id}",
        payload=payload,
    )


@router.post("/assignments/{assignment_id}/accept", response_model=InboxActionResult)
async def accept_assignment(
    assignment_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> InboxActionResult:
    """Accept a pending assignment: ``pending_acceptance`` -> ``accepted``;
    Task ``assigned_to_human`` -> ``in_progress`` (§7.2, human assignee)."""
    tenant_id = require_tenant_id(principal)
    assignment, task = await _load_my_assignment_or_404(
        session, assignment_id, principal, tenant_id
    )
    if assignment.status != HumanTaskAssignmentStatus.PENDING_ACCEPTANCE.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"assignment is '{assignment.status}', not 'pending_acceptance'",
        )
    await _assert_human_assignee(session, assignment, tenant_id)
    _transition_or_409(task, TaskStatus.IN_PROGRESS.value)
    if task.started_at is None:
        task.started_at = datetime.now(UTC)
    assignment.status = HumanTaskAssignmentStatus.ACCEPTED.value
    await session.flush()
    await _record_action(
        session,
        tenant_id=tenant_id,
        task_id=task.id,
        principal=principal,
        action=InboxAction.ACCEPT,
    )
    return _result(assignment, task, InboxAction.ACCEPT)


@router.post("/assignments/{assignment_id}/reject", response_model=InboxActionResult)
async def reject_assignment(
    assignment_id: UUID,
    payload: InboxActionRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> InboxActionResult:
    """Reject an assignment with a justification.

    The justification is REQUIRED (the plan calls for a reason). The assignment
    goes ``declined`` and the Task moves ``assigned_to_human`` -> ``blocked`` so
    a Tenant Admin can re-route it. The justification is recorded in the audit
    trail. Allowed only while the assignment is still ``pending_acceptance``
    (you reject a task you have NOT yet accepted)."""
    tenant_id = require_tenant_id(principal)
    justification = (payload.justification or "").strip()
    if not justification:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="a justification is required to reject an assignment",
        )
    assignment, task = await _load_my_assignment_or_404(
        session, assignment_id, principal, tenant_id
    )
    if assignment.status != HumanTaskAssignmentStatus.PENDING_ACCEPTANCE.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"assignment is '{assignment.status}', not 'pending_acceptance'",
        )
    await _assert_human_assignee(session, assignment, tenant_id)
    _transition_or_409(task, TaskStatus.BLOCKED.value)
    assignment.status = HumanTaskAssignmentStatus.DECLINED.value
    await session.flush()
    await _record_action(
        session,
        tenant_id=tenant_id,
        task_id=task.id,
        principal=principal,
        action=InboxAction.REJECT,
        extra={"justification": justification},
    )
    return _result(assignment, task, InboxAction.REJECT)


@router.post("/assignments/{assignment_id}/complete", response_model=InboxSubmitResult)
async def complete_assignment(
    assignment_id: UUID,
    payload: InboxSubmitRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> InboxSubmitResult:
    """Submit the delivery form for an accepted assignment (task_16_09).

    The assignee marks the task complete with their deliverable — an output
    text, attachments (files / URLs / screenshots, stored as references) and an
    OPTIONAL hours-worked figure. This:

      1. creates a :class:`~api_server.db.domain.HumanWorkSession` (task_16_03)
         recording WHO did the work (the caller), WHEN (the task's ``started_at``
         if known, else now() -> now()), the logged hours, the output text
         (``comments``) and the attachment references (``output_files_attached``);
      2. transitions the Task ``in_progress`` -> ``in_review`` via the §7.2 state
         machine (human assignee) — the deliverable goes to review;
      3. appends an audit event recording the submission.

    The assignment row stays ``accepted`` (it is the historical record of WHO
    the work landed on). Allowed only while the assignment is ``accepted`` (you
    submit a task you have accepted). The work session is the auditable
    replacement for an ``Execution`` on a human task.
    """
    tenant_id = require_tenant_id(principal)
    assignment, task = await _load_my_assignment_or_404(
        session, assignment_id, principal, tenant_id
    )
    if assignment.status != HumanTaskAssignmentStatus.ACCEPTED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"assignment is '{assignment.status}', not 'accepted'",
        )
    await _assert_human_assignee(session, assignment, tenant_id)
    _transition_or_409(task, TaskStatus.IN_REVIEW.value)

    output_text = payload.output_text()
    attachments = payload.usable_attachments()
    now = datetime.now(UTC)
    # The session spans from when the human started the task (set on accept,
    # task_16_08) to the submit moment; fall back to a point-in-time session if
    # started_at is unknown (older rows / direct accept without a started_at).
    start_at = task.started_at or now
    work_session = HumanWorkSession(
        tenant_id=tenant_id,
        task_id=task.id,
        user_id=principal.user_id,
        start_at=start_at,
        end_at=now,
        hours_logged=payload.hours_worked,
        comments=output_text,
        output_files_attached=[a.model_dump(mode="json", exclude_none=True) for a in attachments],
    )
    session.add(work_session)
    await session.flush()

    extra: dict[str, object] = {
        "work_session_id": str(work_session.id),
        "attachments_count": len(attachments),
    }
    if output_text:
        extra["output"] = output_text
    if payload.hours_worked is not None:
        extra["hours_worked"] = str(payload.hours_worked)
    await _record_action(
        session,
        tenant_id=tenant_id,
        task_id=task.id,
        principal=principal,
        action=InboxAction.COMPLETE,
        extra=extra,
    )
    return InboxSubmitResult(
        assignment_id=assignment.id,
        task_id=task.id,
        action=InboxAction.COMPLETE.value,
        assignment_status=assignment.status,
        task_status=task.status,
        work_session_id=work_session.id,
        attachments_count=len(attachments),
    )


@router.post("/assignments/{assignment_id}/escalate", response_model=InboxActionResult)
async def escalate_assignment(
    assignment_id: UUID,
    payload: InboxActionRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> InboxActionResult:
    """Escalate an assignment to the Tenant Admin without doing the task.

    The assignment goes ``declined``, the Task moves to ``blocked`` and a
    ``task_blocked`` notification fans out to the tenant's admins (best-effort —
    a broker outage is logged, never rolls back the DB move; the task is in
    ``blocked`` regardless and an admin finds it in their list). Allowed from
    either ``pending_acceptance`` or ``accepted`` (you may realise mid-work that
    it needs a human with more authority)."""
    tenant_id = require_tenant_id(principal)
    assignment, task = await _load_my_assignment_or_404(
        session, assignment_id, principal, tenant_id
    )
    if assignment.status not in _ACTIVE_ASSIGNMENT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"assignment is '{assignment.status}', not an active assignment",
        )
    await _assert_human_assignee(session, assignment, tenant_id)
    _transition_or_409(task, TaskStatus.BLOCKED.value)
    assignment.status = HumanTaskAssignmentStatus.DECLINED.value
    await session.flush()
    reason = (payload.justification or "").strip()
    extra: dict[str, object] = {"escalated_to": "tenant_admin"}
    if reason:
        extra["reason"] = reason
    await _record_action(
        session,
        tenant_id=tenant_id,
        task_id=task.id,
        principal=principal,
        action=InboxAction.ESCALATE,
        extra=extra,
    )
    # Fan a task_blocked event out to the tenant's admins. Best-effort: the
    # commit already happened (the action is not rolled back on a broker blip).
    await enqueue_event_dispatch(
        {
            "event_type": _TASK_BLOCKED_EVENT,
            "tenant_id": str(tenant_id),
            "context": {
                "task_id": str(task.id),
                "task_title": task.title,
                "reason": reason or "escalated to admin by the assignee",
                "escalated_by_user_id": str(principal.user_id),
            },
            "locale": None,
        }
    )
    return _result(assignment, task, InboxAction.ESCALATE)


def _result(assignment: HumanTaskAssignment, task: Task, action: InboxAction) -> InboxActionResult:
    return InboxActionResult(
        assignment_id=assignment.id,
        task_id=task.id,
        action=action.value,
        assignment_status=assignment.status,
        task_status=task.status,
    )
