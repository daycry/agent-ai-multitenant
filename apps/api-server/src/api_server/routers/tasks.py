"""`/projects/{project_id}/tasks` endpoints (task_01_08).

Tasks are the Kanban units. CRUD is plain; status moves go through PUT.
Dependencies are stored in the task_dependencies junction and rewritten
atomically on POST/PUT.

DELETE is a hard delete (Task has no soft-delete mixin); to "archive"
a task move it to status='cancelled' or 'done' via PUT instead.
"""

from __future__ import annotations

import contextlib
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
    principal_is_tenant_admin,
    require_tenant_admin,
    require_tenant_member,
    schedule_after_commit,
)
from api_server.celery_client import revoke_job_callback
from api_server.chat.criteria_llm import (
    format_sibling_context,
    generate_task_acceptance_criteria,
)
from api_server.chat.dag_enforcement import (
    DependenciesNotDoneError,
    assert_dependencies_done,
)
from api_server.chat.planning_llm import _clean_acceptance_criteria
from api_server.chat.responder import _resolve_chat_provider, resolve_chat_model_config
from api_server.db.domain import Project, Task, TaskDependency, TaskStatus
from api_server.db.execution_repo import cancel_running_executions_for_task
from api_server.events import publish_task_created, publish_task_status_changed
from api_server.llm_providers.vault import LLMProviderVaultStore
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
from api_server.routers.llm_providers import get_provider_vault_store
from api_server.schemas.tasks import (
    GeneratedAcceptanceCriteria,
    TaskCreateRequest,
    TaskResponse,
    TaskUpdateRequest,
    to_task_response,
)
from api_server.task_state_machine import allowed_transitions

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


async def _plan_sibling_context(session: AsyncSession, task: Task) -> str:
    """Digest of the OTHER tasks in this task's plan (title + criteria) so criteria
    generation stays coherent with a sibling's decisions (a shared response
    contract, an error format, …). Empty when the task belongs to no plan.

    This is the fix for the CI4 "Implementar controladores" block: without it the
    generator emitted a "ResponseTrait" criterion that contradicted a sibling
    contract task's ``{message, meta}`` shape, making the self-review unsatisfiable."""
    if task.plan_id is None:
        return ""
    rows = (
        await session.execute(
            select(Task.title, Task.acceptance_criteria).where(
                Task.plan_id == task.plan_id, Task.id != task.id
            )
        )
    ).all()
    siblings = [(str(title), _clean_acceptance_criteria(criteria)) for title, criteria in rows]
    return format_sibling_context(siblings)


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
# POST /projects/{project_id}/tasks/{task_id}/generate-acceptance-criteria
# ---------------------------------------------------------------------------
@router.post(
    "/{task_id}/generate-acceptance-criteria",
    response_model=GeneratedAcceptanceCriteria,
)
async def generate_acceptance_criteria(
    project_id: UUID,
    task_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> GeneratedAcceptanceCriteria:
    """Propose acceptance criteria for one task via the project's chat LLM
    (ADR 0021), taking any EXISTING criteria into account so a regenerate
    refines rather than ignores them. Does NOT persist: the operator reviews
    (and confirms against a comparison when the task already had criteria)
    before saving via PUT."""
    project = await _verify_project_visible(session, project_id)
    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.project_id == project_id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")

    sibling_context = await _plan_sibling_context(session, task)

    effective = await resolve_chat_model_config(session, project)
    provider, _kind, api_model = await _resolve_chat_provider(session, effective, vault)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No hay proveedor LLM configurado para el chat de este proyecto.",
        )
    try:
        proposal = await generate_task_acceptance_criteria(
            provider,
            title=task.title,
            description=task.description,
            existing=_clean_acceptance_criteria(task.acceptance_criteria),
            project_context={"name": project.name, "description": project.description or ""},
            model=api_model,
            sibling_context=sibling_context,
        )
    finally:
        with contextlib.suppress(Exception):
            await provider.aclose()
    return GeneratedAcceptanceCriteria(acceptance_criteria=proposal)


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
    force: bool = Query(
        default=False,
        description="tenant_admin override: apply an otherwise-illegal status "
        "transition (c1/T2). Ignored toward `done`.",
    ),
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

        # c1/T2 (audit 2026-07-03, ratified opt B): the state machine is the SINGLE
        # gate — an illegal transition is a 409 Conflict (distinct from the DAG 422),
        # so a drag&drop backlog→done can no longer fake a `done` that the
        # trg_compute_task_ready trigger amplifies into promoting dependents. Runs AFTER
        # the DAG check (a pending dependency stays a 422). A tenant_admin may FORCE an
        # otherwise-illegal move (force=true) EXCEPT toward `done` — a forced false-done
        # would re-open exactly the amplification this closes. The board uses the AI
        # table (assignee_agent_type=None); human-task moves go via the human inbox.
        if payload.status.value not in allowed_transitions(old_status):
            forced = (
                force
                and payload.status.value != TaskStatus.DONE.value
                and await principal_is_tenant_admin(session, principal)
            )
            if not forced:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "illegal_transition",
                        "from": old_status,
                        "to": payload.status.value,
                    },
                )

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
        # prod-06 cancel_01: cancelling a task in flight must also cancel its
        # running execution(s) — seal cancel_requested_at (the worker polls it
        # to kill the container + finalise as cancelled) and revoke the queued
        # Celery job after commit (no-op if it never started).
        if new_status == TaskStatus.CANCELLED.value:
            for execution in await cancel_running_executions_for_task(session, task.id):
                if execution.celery_task_id:
                    schedule_after_commit(session, revoke_job_callback(execution.celery_task_id))
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
    # HARDDEP: refuse to hard-delete a task other tasks DEPEND ON. task_dependencies
    # has ON DELETE CASCADE on depends_on_task_id, so deleting a prerequisite would
    # silently drop the dependents' dependency rows → they'd promote to `ready` as if
    # the prerequisite had completed (DAG corruption). Force the operator to remove
    # the edge (or delete the dependents) first. Deleting a leaf is unaffected.
    dependents = (
        (
            await session.execute(
                select(TaskDependency.task_id).where(TaskDependency.depends_on_task_id == task_id)
            )
        )
        .scalars()
        .all()
    )
    if dependents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"task is a dependency of {len(dependents)} other task(s); "
                "remove the dependency or delete them first"
            ),
        )
    # task_dependencies rows (where this task is the dependent) CASCADE off it.
    await session.delete(task)
    await session.flush()
