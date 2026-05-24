"""The human-approval engine (task_02_24 / task_02_27).

When an agent attempts a sensitive action, the engine checks the
project's `human_approval_policy`:

  * `auto`           — the action proceeds, nothing is persisted.
  * `human_required` — the execution is parked in
                       `awaiting_human_approval` and an `ApprovalRequest`
                       row is persisted for a reviewer.

A reviewer resolves the request (approve / reject); an unanswered one
times out after a configurable window (default 24 h) — the request is
marked `timed_out`, its execution aborted and its task blocked.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import (
    ApprovalRequest,
    ApprovalRequestStatus,
    Execution,
    ExecutionStatus,
    Project,
    Task,
    TaskStatus,
)

# Abort code stamped on an execution whose approval request timed out.
APPROVAL_TIMEOUT_ABORT_CODE = "approval_timeout_exceeded"
# Abort code stamped on an execution whose approval request was rejected
# by a human reviewer (ADR 0020).
APPROVAL_REJECTED_ABORT_CODE = "approval_rejected"


def requires_human_approval(policy: dict[str, Any] | None, category: str) -> bool:
    """True if `category` needs a human under this project's policy.

    The policy JSONB is `{"categories": {<category>: "auto" |
    "human_required"}}` (a bare `{<category>: ...}` map is also
    accepted). An unlisted category defaults to `auto`.
    """
    if not policy:
        return False
    categories = policy.get("categories", policy)
    if not isinstance(categories, dict):
        return False
    return str(categories.get(category, "auto")) == "human_required"


async def request_approval_if_needed(
    session: AsyncSession,
    *,
    execution: Execution,
    project: Project,
    category: str,
    action: dict[str, Any],
) -> ApprovalRequest | None:
    """Evaluate `category` against the project's policy.

    Returns the persisted `ApprovalRequest` and parks the execution in
    `awaiting_human_approval` when a human is required; returns None
    (the action may proceed) otherwise. The caller owns the transaction.
    """
    if not requires_human_approval(project.human_approval_policy, category):
        return None

    request = ApprovalRequest(
        tenant_id=execution.tenant_id,
        execution_id=execution.id,
        task_id=execution.task_id,
        project_id=project.id,
        category=category,
        action=action,
        status=ApprovalRequestStatus.PENDING,
    )
    session.add(request)
    execution.status = ExecutionStatus.AWAITING_HUMAN_APPROVAL

    # ADR 0020: la TAREA también se aparca y el agente queda libre, para
    # que el dispatcher pueda darle otra tarea y para que el board
    # muestre la espera en una columna propia.
    task = await session.get(Task, execution.task_id)
    if task is not None and task.status != TaskStatus.AWAITING_HUMAN_APPROVAL:
        task.status = TaskStatus.AWAITING_HUMAN_APPROVAL
        task.assigned_agent_id = None

    await session.flush()
    return request


async def get_approval_request(session: AsyncSession, request_id: UUID) -> ApprovalRequest | None:
    result = await session.execute(select(ApprovalRequest).where(ApprovalRequest.id == request_id))
    return result.scalar_one_or_none()


async def list_pending_approvals(session: AsyncSession) -> list[ApprovalRequest]:
    """All pending requests, oldest first — the in-app notification feed."""
    result = await session.execute(
        select(ApprovalRequest)
        .where(ApprovalRequest.status == ApprovalRequestStatus.PENDING)
        .order_by(ApprovalRequest.requested_at)
    )
    return list(result.scalars().all())


async def resolve_approval(
    session: AsyncSession,
    request: ApprovalRequest,
    *,
    approved: bool,
    resolver_id: UUID | None = None,
    reason: str | None = None,
) -> ApprovalRequest:
    """Approve or reject a pending request — ADR 0020.

    APPROVE: the original execution closes as `done`; the task goes
    back to `backlog` with its agent cleared, so the dispatcher re-picks
    when it becomes `ready` again (the original agent may be busy with
    another task by then).

    REJECT: the task is `blocked` — the human said no, and the action
    will not be retried automatically. The reviewer's `reason` lives
    on the `ApprovalRequest` for audit (Opción B del ADR 0020, no
    implementada todavía: pasarlo de vuelta al agente como feedback).
    """
    request.status = ApprovalRequestStatus.APPROVED if approved else ApprovalRequestStatus.REJECTED
    request.resolved_at = datetime.now(UTC)
    request.resolved_by = resolver_id
    request.reason = reason

    execution = await session.get(Execution, request.execution_id)
    task = await session.get(Task, request.task_id)

    if approved:
        if execution is not None:
            execution.status = ExecutionStatus.DONE
            execution.completed_at = datetime.now(UTC)
        if task is not None:
            task.status = TaskStatus.BACKLOG
            task.assigned_agent_id = None
    else:
        if execution is not None:
            execution.status = ExecutionStatus.ABORTED
            execution.abort_code = APPROVAL_REJECTED_ABORT_CODE
            execution.completed_at = datetime.now(UTC)
        if task is not None:
            task.status = TaskStatus.BLOCKED

    await session.flush()
    return request


async def expire_stale_requests(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    timeout_hours: float = 24.0,
) -> list[ApprovalRequest]:
    """Time out every pending request older than `timeout_hours`.

    A timed-out request aborts its execution and blocks its task — a
    decision nobody made cannot leave the run hanging forever. Returns
    the requests that were expired.
    """
    now = now or datetime.now(UTC)
    cutoff = now - timedelta(hours=timeout_hours)

    result = await session.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.status == ApprovalRequestStatus.PENDING,
            ApprovalRequest.requested_at < cutoff,
        )
    )
    stale = list(result.scalars().all())

    for request in stale:
        request.status = ApprovalRequestStatus.TIMED_OUT
        request.resolved_at = now
        request.reason = f"no response within {timeout_hours:g} h"

        execution = await session.get(Execution, request.execution_id)
        if execution is not None:
            execution.status = ExecutionStatus.ABORTED
            execution.abort_code = APPROVAL_TIMEOUT_ABORT_CODE
        task = await session.get(Task, request.task_id)
        if task is not None:
            task.status = TaskStatus.BLOCKED

    await session.flush()
    return stale
