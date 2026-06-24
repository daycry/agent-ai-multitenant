"""`/projects/{project_id}/tasks` endpoints (task_01_08).

Tasks are the Kanban units. CRUD is plain; status moves go through PUT.
Dependencies are stored in the task_dependencies junction and rewritten
atomically on POST/PUT.

DELETE is a hard delete (Task has no soft-delete mixin); to "archive"
a task move it to status='cancelled' or 'done' via PUT instead.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_redis,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
    schedule_after_commit,
)
from api_server.chat.dag_enforcement import (
    DependenciesNotDoneError,
    assert_dependencies_done,
)
from api_server.db.domain import Project, Task, TaskDependency
from api_server.events import publish_task_created, publish_task_status_changed
from api_server.routers._helpers import (
    apply_partial_update,
    get_writable_or_404,
    require_tenant_id,
)
from api_server.routers._pagination import (
    apply_pagination,
    limit_query,
    offset_query,
)
from api_server.schemas.tasks import (
    TaskCreateRequest,
    TaskResponse,
    TaskUpdateRequest,
    to_task_response,
)

router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _verify_project_visible(session: AsyncSession, project_id: UUID) -> Project:
    """RLS already hides cross-tenant projects. This turns "0 rows" into
    an explicit 404 instead of letting downstream FK errors surface."""
    result = await session.execute(
        select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


async def _load_dependencies(session: AsyncSession, task_id: UUID) -> list[UUID]:
    result = await session.execute(
        select(TaskDependency.depends_on_task_id).where(TaskDependency.task_id == task_id)
    )
    return [r[0] for r in result.all()]


async def _set_dependencies(
    session: AsyncSession, task_id: UUID, project_id: UUID, depends_on: list[UUID]
) -> None:
    """Replace the task's dependencies atomically. All referenced tasks
    must belong to the same project (cross-project deps don't make sense
    in this MVP)."""
    if depends_on:
        result = await session.execute(
            select(Task.id).where(
                Task.id.in_(depends_on),
                Task.project_id == project_id,
            )
        )
        present = {r[0] for r in result.all()}
        missing = set(depends_on) - present
        if missing:
            missing_ids = sorted(str(x) for x in missing)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"dependency task(s) not found in this project: {missing_ids}",
            )

    await session.execute(sql_delete(TaskDependency).where(TaskDependency.task_id == task_id))
    for dep_id in depends_on:
        session.add(TaskDependency(task_id=task_id, depends_on_task_id=dep_id))
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        # The DB CHECK ck_task_dependencies_no_self_loop catches a task
        # depending on itself, even if the application logic somehow
        # let it through.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc.orig)) from exc


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/tasks
# ---------------------------------------------------------------------------
@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    project_id: UUID,
    status_: str | None = Query(default=None, alias="status"),
    priority: str | None = Query(default=None),
    assigned_agent_id: UUID | None = Query(default=None),
    plan_id: UUID | None = Query(default=None),
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[TaskResponse]:
    await _verify_project_visible(session, project_id)

    stmt = select(Task).where(Task.project_id == project_id)
    if status_ is not None:
        stmt = stmt.where(Task.status == status_)
    if priority is not None:
        stmt = stmt.where(Task.priority == priority)
    if assigned_agent_id is not None:
        stmt = stmt.where(Task.assigned_agent_id == assigned_agent_id)
    if plan_id is not None:
        stmt = stmt.where(Task.plan_id == plan_id)
    stmt = stmt.order_by(Task.created_at, Task.id)
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    tasks = list(result.scalars().all())

    if not tasks:
        return []

    # One round-trip for all dependencies across these tasks.
    dep_res = await session.execute(
        select(TaskDependency.task_id, TaskDependency.depends_on_task_id).where(
            TaskDependency.task_id.in_([t.id for t in tasks])
        )
    )
    deps_by_task: dict[UUID, list[UUID]] = {}
    for task_id, dep_id in dep_res.all():
        deps_by_task.setdefault(task_id, []).append(dep_id)

    return [to_task_response(t, deps_by_task.get(t.id, [])) for t in tasks]


# ---------------------------------------------------------------------------
# GET /projects/{project_id}/tasks/{task_id}
# ---------------------------------------------------------------------------
@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    project_id: UUID,
    task_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> TaskResponse:
    await _verify_project_visible(session, project_id)
    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.project_id == project_id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    deps = await _load_dependencies(session, task.id)
    return to_task_response(task, deps)


# ---------------------------------------------------------------------------
# POST /projects/{project_id}/tasks
# ---------------------------------------------------------------------------
@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    project_id: UUID,
    payload: TaskCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> TaskResponse:
    tenant_id = require_tenant_id(principal)
    await _verify_project_visible(session, project_id)

    task = Task(
        tenant_id=tenant_id,
        project_id=project_id,
        plan_id=payload.plan_id,
        title=payload.title,
        description=payload.description,
        status=payload.status.value,
        priority=payload.priority.value,
        assigned_agent_id=payload.assigned_agent_id,
        reviewer_agent_id=payload.reviewer_agent_id,
        acceptance_criteria=payload.acceptance_criteria,
        inputs=payload.inputs,
        estimated_complexity=(
            payload.estimated_complexity.value if payload.estimated_complexity is not None else None
        ),
        max_retries=payload.max_retries,
    )
    session.add(task)
    await session.flush()

    if payload.depends_on:
        await _set_dependencies(session, task.id, project_id, payload.depends_on)

    await session.refresh(task)
    deps = await _load_dependencies(session, task.id)
    # Notify the orchestrator AFTER the request transaction commits (see
    # `schedule_after_commit`). Emitting inline — before `open_tenant_session`
    # commits on return — lets a fast orchestrator read `task is None` in
    # `_dispatch` and silently skip the dispatch (root cause of the "consumer
    # se atasca" symptom, sesión 2026-06-18). `expire_on_commit=False` keeps
    # the task's scalars readable in the post-commit callback.
    schedule_after_commit(session, lambda: publish_task_created(get_redis(), task))
    return to_task_response(task, deps)


# ---------------------------------------------------------------------------
# PUT /projects/{project_id}/tasks/{task_id}
# ---------------------------------------------------------------------------
@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    project_id: UUID,
    task_id: UUID,
    payload: TaskUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> TaskResponse:
    require_tenant_id(principal)
    await _verify_project_visible(session, project_id)

    task = await get_writable_or_404(
        session,
        Task,
        task_id,
        principal,
        not_found_detail="task not found",
        extra_filters=(Task.project_id == project_id,),
        soft_delete_aware=False,
    )

    # Snapshot the status before the update so we can tell whether the
    # PUT actually moved the task across the Kanban (and thus whether
    # the orchestrator needs a `task.status_changed` event).
    old_status = task.status

    # DAG enforcement (task_03_30): a move to a "starts-work" status is
    # rejected if any upstream dependency is still not `done`. We check
    # *before* applying the partial update so the row is not mutated.
    if payload.status is not None and payload.status.value != old_status:
        try:
            await assert_dependencies_done(session, task.id, payload.status.value)
        except DependenciesNotDoneError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={
                    "error": "dependencies_not_done",
                    "target_status": payload.status.value,
                    "pending": [
                        {"task_id": str(p.task_id), "status": p.status} for p in exc.pending
                    ],
                },
            ) from exc

    # Dependencies are handled out-of-band; remove them from the scalar
    # update so apply_partial_update doesn't try to setattr a list of
    # UUIDs onto the SA column.
    sent = payload.model_fields_set
    deps_change = "depends_on" in sent
    payload_for_obj = payload.model_copy(update={"depends_on": None})
    payload_for_obj.__pydantic_fields_set__.discard("depends_on")

    apply_partial_update(
        task,
        payload_for_obj,
        enum_fields=("status", "priority", "estimated_complexity"),
    )
    await session.flush()

    if deps_change:
        await _set_dependencies(session, task.id, project_id, payload.depends_on or [])

    await session.refresh(task)
    deps = await _load_dependencies(session, task.id)
    # Best-effort orchestrator notification on a real status move — deferred to
    # AFTER the request commits (see create_task / schedule_after_commit), so a
    # consumer reacting to the event reads the committed NEW status, not the
    # stale one.
    if task.status != old_status:
        new_status = task.status
        schedule_after_commit(
            session,
            lambda: publish_task_status_changed(
                get_redis(), task, old_status=old_status, new_status=new_status
            ),
        )
    return to_task_response(task, deps)


# ---------------------------------------------------------------------------
# DELETE /projects/{project_id}/tasks/{task_id} -- hard delete
# ---------------------------------------------------------------------------
@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    project_id: UUID,
    task_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    require_tenant_id(principal)
    await _verify_project_visible(session, project_id)
    task = await get_writable_or_404(
        session,
        Task,
        task_id,
        principal,
        not_found_detail="task not found",
        extra_filters=(Task.project_id == project_id,),
        soft_delete_aware=False,
    )
    # task_dependencies rows CASCADE off the task; no manual cleanup.
    await session.delete(task)
    await session.flush()
