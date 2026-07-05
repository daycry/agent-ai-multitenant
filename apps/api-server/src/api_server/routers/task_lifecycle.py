"""`/tasks/{task_id}/*` endpoints (Plan 06.5 Fase B).

Operations on a task addressed by its id directly (no project_id
prefix). RLS scopes the visibility — if the task belongs to a
different tenant the endpoint returns 404, never leaks the existence
of the row.

Endpoints in this router:

  * GET  /tasks/{task_id}/history       — task_06_5_04
  * POST /tasks/{task_id}/human-action  — task_06_5_05  (sigue por Fase B)
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_redis,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
    schedule_after_commit,
)
from api_server.chat.dag_enforcement import DependenciesNotDoneError, assert_dependencies_done
from api_server.chat.plan_state_machine import transition_plan_status
from api_server.db.domain import Plan, Task
from api_server.db.task_audit_repo import append_audit_event, list_history, to_dict
from api_server.events import publish_task_status_changed
from api_server.routers._helpers import require_tenant_id

router = APIRouter(prefix="/tasks", tags=["task-lifecycle"])


async def _assert_task_visible(session: AsyncSession, task_id: UUID) -> None:
    """RLS-safe lookup. Raises 404 if the task does not exist in the
    current tenant scope — the caller cannot tell whether the id is
    invalid or belongs to another tenant."""
    result = await session.execute(select(Task.id).where(Task.id == task_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")


@router.get("/{task_id}/history")
async def get_task_history(
    task_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, list[dict[str, object]]]:
    """Chronological audit trail of a task.

    Returns the events oldest-first as JSON. The shape mirrors
    `api_server.task_lifecycle.AuditEvent`:

        {
          "events": [
            {"id": "...", "task_id": "...", "at": 1716889200.123,
             "kind": "review_comment", "actor": "agent:reviewer",
             "payload": {...}},
            ...
          ]
        }

    Empty array if the task has no events yet. Append-only — there is
    no DELETE / PUT counterpart in this router.
    """
    await _assert_task_visible(session, task_id)
    events = await list_history(session, task_id)
    return {"events": [to_dict(e) for e in events]}


# ---------------------------------------------------------------------------
# Human actions (Plan 06 task_06_34b4 — exposed via HTTP by Plan 06.5
# task_06_5_05). Mirrors `TaskLifecycle.apply_human_action` but writes
# directly to the SQLAlchemy `Task` table + `task_audit_events`. The
# in-memory `TaskLifecycle` class is kept as the reference / tests
# fixture; this endpoint is the production path.
# ---------------------------------------------------------------------------

HumanAction = Literal[
    "approve_manual",  # task → done
    "reassign_with_guidance",  # task → backlog, retry_count++
    "block_with_reason",  # task → blocked
    "cancel",  # task → cancelled
    "retry",  # T7c: blocked task → ready (backlog if deps pending) + reset retry + reactivate plan
]


class HumanActionRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    action: HumanAction
    # `reason` for block, `guidance` for reassign. Both optional —
    # `block_with_reason` without a reason is allowed (the empty
    # string is recorded) so the UI can ask the human to fill it
    # later if needed.
    reason: str | None = None
    guidance: str | None = None


# Map each action to (new_status, audit_kind, retry_increment).
_ACTION_TABLE: dict[HumanAction, tuple[str, str, bool]] = {
    "approve_manual": ("done", "human_action", False),
    "reassign_with_guidance": ("backlog", "human_action", True),
    "block_with_reason": ("blocked", "human_action", False),
    "cancel": ("cancelled", "human_action", False),
    "retry": ("ready", "human_action", False),  # reset + plan reactivation handled specially
}


@router.post("/{task_id}/human-action")
async def apply_human_action(
    task_id: UUID,
    payload: HumanActionRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, object]:
    """Apply one of the four human actions on a task in
    `awaiting_human_approval`.

    Side effects:
      1. `tasks.status` moves to the action's target status.
      2. `tasks.retry_count` increments on `reassign_with_guidance`.
      3. An audit event is appended with kind=`human_action` and
         payload `{action, reason?, guidance?, actor_user_id}`.

    Returns `{task: <updated row>, event: <new audit event>}`.

    Allowed only from `awaiting_human_approval` — calling on a task
    already in a terminal state returns 409 Conflict. RBAC (Plan 06.8
    follow-up) will gate this further to `tenant_admin`.
    """
    tenant_id = require_tenant_id(principal)
    await _assert_task_visible(session, task_id)

    task_row = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one_or_none()
    if task_row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")

    if task_row.status not in {"awaiting_human_approval", "blocked"}:
        # `blocked` accepted as a starting point too — a human can
        # cancel or reassign a blocked task without re-escalating.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"task is in '{task_row.status}', not in 'awaiting_human_approval' / 'blocked'"
            ),
        )

    original_status = task_row.status
    new_status, kind, bump_retry = _ACTION_TABLE[payload.action]
    if payload.action == "retry":
        # T7c (c3, ratified A+D): un-stick a blocked task. Reset the retry budget so the
        # re-run is not immediately re-blocked (the exhaustion path that blocked it),
        # degrade ready→backlog when a dependency is still pending (the DAG would reject
        # `ready`), and reactivate the plan (blocked→in_progress) so the promoter picks
        # the task up again.
        try:
            await assert_dependencies_done(session, task_row.id, "ready")
        except DependenciesNotDoneError:
            new_status = "backlog"
        task_row.status = new_status
        task_row.retry_count = 0
        if task_row.plan_id is not None:
            plan = await session.get(Plan, task_row.plan_id)
            if plan is not None and plan.status == "blocked":
                transition_plan_status(plan, "in_progress")
    else:
        task_row.status = new_status
        if bump_retry:
            task_row.retry_count += 1
    await session.flush()

    # T7c: a retried task that reached `ready` must reach the orchestrator so it is
    # re-dispatched (its plan is in_progress again). Deferred to after commit so the
    # consumer reads the committed status (same pattern as PUT /tasks).
    if payload.action == "retry" and task_row.status == "ready":
        schedule_after_commit(
            session,
            lambda: publish_task_status_changed(
                get_redis(), task_row, old_status=original_status, new_status="ready"
            ),
        )

    event_payload: dict[str, object] = {
        "action": payload.action,
        "actor_user_id": str(principal.user_id),
    }
    if payload.reason:
        event_payload["reason"] = payload.reason
    if payload.guidance:
        event_payload["guidance"] = payload.guidance
    event = await append_audit_event(
        session,
        tenant_id=tenant_id,
        task_id=task_id,
        kind=kind,
        actor=f"user:{principal.user_id}",
        payload=event_payload,
    )

    return {
        "task": {
            "id": str(task_row.id),
            "status": task_row.status,
            "retry_count": task_row.retry_count,
        },
        "event": to_dict(event),
    }
