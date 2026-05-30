"""Public v1 REST endpoints (Plan 13 task_13_05, Fase B).

A THIN, scope-checked facade over the existing domain — it reuses the
domain ORM models and the SAME public Pydantic response schemas the
interactive routers expose (``to_project_response`` etc.), so there is no
duplicated business logic and no internal-only field leaks beyond what the
UI already returns.

Auth: every endpoint depends on :func:`require_scope` (Fase A
``X-API-Token`` -> tenant resolution + per-token rate limit + scope
check) and runs its query under the Fase A tenant-scoped RLS session
(:data:`V1Session`). The token's tenant is the only tenant visible — RLS
guarantees a tenant-A token can never read or write tenant-B rows, so the
read endpoints below carry no explicit ``tenant_id`` filter (the session
binds it).

Scopes: GET endpoints require ``read``; POST endpoints require ``write``.

Pagination: every list endpoint takes ``limit``/``offset`` with
``ge``/``le`` bounds (shared :mod:`api_server.routers._pagination`
helpers) so a response can never be unbounded.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.api_token_auth import ApiTokenPrincipal
from api_server.db.conversation import Conversation
from api_server.db.domain import Plan, Project, Task, TaskDependency
from api_server.db.knowledge import KnowledgeBase
from api_server.db.models import ApiTokenScope
from api_server.routers._pagination import apply_pagination, limit_query, offset_query
from api_server.routers.api_v1._deps import V1Session, require_scope
from api_server.routers.api_v1._versioning import enforce_api_version
from api_server.routers.api_v1.schemas import (
    V1ConversationCreateRequest,
    V1KnowledgeBaseCreateRequest,
    V1PlanCreateRequest,
    V1ProjectCreateRequest,
    V1TaskCreateRequest,
)
from api_server.schemas.conversations import (
    ConversationResponse,
    to_conversation_response,
)
from api_server.schemas.knowledge import KnowledgeBaseResponse, to_kb_response
from api_server.schemas.plans import PlanResponse, to_plan_response
from api_server.schemas.projects import ProjectResponse, to_project_response
from api_server.schemas.tasks import TaskResponse, to_task_response

# Versioned in the PATH (Plan 13 Decisiones Clave), tagged so the OpenAPI
# (task_13_06) groups the public surface separately from the interactive
# routers. ``enforce_api_version`` (task_13_07) is a router-level dependency
# so every v1 endpoint negotiates the optional ``X-API-Version`` header,
# advertises the served version back and tracks per-version usage — it
# composes WITH the per-endpoint ``require_scope`` auth, never replacing it.
api_v1_router = APIRouter(
    prefix="/api/v1",
    tags=["public-api-v1"],
    dependencies=[Depends(enforce_api_version)],
)

_RequireRead = Depends(require_scope(ApiTokenScope.READ))
_RequireWrite = Depends(require_scope(ApiTokenScope.WRITE))


# ===========================================================================
# Shared resolve helpers — turn a cross-tenant / missing row into a clean
# 404 (RLS has already filtered other tenants' rows out of the session).
# ===========================================================================
async def _get_project_or_404(session: AsyncSession, project_id: UUID) -> Project:
    result = await session.execute(
        select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


# ===========================================================================
# Projects
# ===========================================================================
@api_v1_router.get("/projects", response_model=list[ProjectResponse])
async def v1_list_projects(
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: ApiTokenPrincipal = _RequireRead,
    session: AsyncSession = Depends(V1Session),
) -> list[ProjectResponse]:
    """List the token tenant's projects (excludes templates + soft-deleted)."""
    stmt = (
        select(Project)
        .where(Project.deleted_at.is_(None), Project.is_template.is_(False))
        .order_by(Project.created_at, Project.id)
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    return [to_project_response(p) for p in result.scalars().all()]


@api_v1_router.get("/projects/{project_id}", response_model=ProjectResponse)
async def v1_get_project(
    project_id: UUID,
    _: ApiTokenPrincipal = _RequireRead,
    session: AsyncSession = Depends(V1Session),
) -> ProjectResponse:
    return to_project_response(await _get_project_or_404(session, project_id))


@api_v1_router.post(
    "/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED
)
async def v1_create_project(
    payload: V1ProjectCreateRequest,
    principal: ApiTokenPrincipal = _RequireWrite,
    session: AsyncSession = Depends(V1Session),
) -> ProjectResponse:
    project = Project(
        tenant_id=principal.tenant_id,
        name=payload.name,
        description=payload.description,
        status=payload.status.value,
    )
    session.add(project)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="project create conflict"
        ) from exc
    await session.refresh(project)
    return to_project_response(project)


# ===========================================================================
# Plans (project-scoped list + create; flat get)
# ===========================================================================
@api_v1_router.get("/projects/{project_id}/plans", response_model=list[PlanResponse])
async def v1_list_plans(
    project_id: UUID,
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: ApiTokenPrincipal = _RequireRead,
    session: AsyncSession = Depends(V1Session),
) -> list[PlanResponse]:
    await _get_project_or_404(session, project_id)
    stmt = (
        select(Plan)
        .where(Plan.project_id == project_id, Plan.deleted_at.is_(None))
        .order_by(Plan.created_at.desc(), Plan.id)
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    return [to_plan_response(p) for p in result.scalars().all()]


@api_v1_router.get("/plans/{plan_id}", response_model=PlanResponse)
async def v1_get_plan(
    plan_id: UUID,
    _: ApiTokenPrincipal = _RequireRead,
    session: AsyncSession = Depends(V1Session),
) -> PlanResponse:
    result = await session.execute(
        select(Plan).where(Plan.id == plan_id, Plan.deleted_at.is_(None))
    )
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan not found")
    return to_plan_response(plan)


@api_v1_router.post(
    "/projects/{project_id}/plans",
    response_model=PlanResponse,
    status_code=status.HTTP_201_CREATED,
)
async def v1_create_plan(
    project_id: UUID,
    payload: V1PlanCreateRequest,
    principal: ApiTokenPrincipal = _RequireWrite,
    session: AsyncSession = Depends(V1Session),
) -> PlanResponse:
    await _get_project_or_404(session, project_id)
    plan = Plan(
        tenant_id=principal.tenant_id,
        project_id=project_id,
        title=payload.title,
        description=payload.description,
        status=payload.status.value,
        specification={},
        created_by=None,
    )
    session.add(plan)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="plan create conflict"
        ) from exc
    await session.refresh(plan)
    return to_plan_response(plan)


# ===========================================================================
# Tasks (project-scoped)
# ===========================================================================
async def _load_task_deps(session: AsyncSession, task_id: UUID) -> list[UUID]:
    result = await session.execute(
        select(TaskDependency.depends_on_task_id).where(TaskDependency.task_id == task_id)
    )
    return [r[0] for r in result.all()]


@api_v1_router.get("/projects/{project_id}/tasks", response_model=list[TaskResponse])
async def v1_list_tasks(
    project_id: UUID,
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: ApiTokenPrincipal = _RequireRead,
    session: AsyncSession = Depends(V1Session),
) -> list[TaskResponse]:
    await _get_project_or_404(session, project_id)
    stmt = select(Task).where(Task.project_id == project_id).order_by(Task.created_at, Task.id)
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    tasks = list(result.scalars().all())
    if not tasks:
        return []
    dep_res = await session.execute(
        select(TaskDependency.task_id, TaskDependency.depends_on_task_id).where(
            TaskDependency.task_id.in_([t.id for t in tasks])
        )
    )
    deps_by_task: dict[UUID, list[UUID]] = {}
    for task_id, dep_id in dep_res.all():
        deps_by_task.setdefault(task_id, []).append(dep_id)
    return [to_task_response(t, deps_by_task.get(t.id, [])) for t in tasks]


@api_v1_router.get("/projects/{project_id}/tasks/{task_id}", response_model=TaskResponse)
async def v1_get_task(
    project_id: UUID,
    task_id: UUID,
    _: ApiTokenPrincipal = _RequireRead,
    session: AsyncSession = Depends(V1Session),
) -> TaskResponse:
    await _get_project_or_404(session, project_id)
    result = await session.execute(
        select(Task).where(Task.id == task_id, Task.project_id == project_id)
    )
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="task not found")
    return to_task_response(task, await _load_task_deps(session, task.id))


@api_v1_router.post(
    "/projects/{project_id}/tasks",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def v1_create_task(
    project_id: UUID,
    payload: V1TaskCreateRequest,
    principal: ApiTokenPrincipal = _RequireWrite,
    session: AsyncSession = Depends(V1Session),
) -> TaskResponse:
    await _get_project_or_404(session, project_id)
    task = Task(
        tenant_id=principal.tenant_id,
        project_id=project_id,
        title=payload.title,
        description=payload.description,
        status=payload.status.value,
        priority=payload.priority.value,
    )
    session.add(task)
    await session.flush()
    await session.refresh(task)
    return to_task_response(task, [])


# ===========================================================================
# Conversations (project-scoped list + create; flat get)
# ===========================================================================
@api_v1_router.get(
    "/projects/{project_id}/conversations", response_model=list[ConversationResponse]
)
async def v1_list_conversations(
    project_id: UUID,
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: ApiTokenPrincipal = _RequireRead,
    session: AsyncSession = Depends(V1Session),
) -> list[ConversationResponse]:
    await _get_project_or_404(session, project_id)
    stmt = (
        select(Conversation)
        .where(Conversation.project_id == project_id, Conversation.deleted_at.is_(None))
        .order_by(Conversation.created_at, Conversation.id)
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    return [to_conversation_response(c) for c in result.scalars().all()]


@api_v1_router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def v1_get_conversation(
    conversation_id: UUID,
    _: ApiTokenPrincipal = _RequireRead,
    session: AsyncSession = Depends(V1Session),
) -> ConversationResponse:
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id, Conversation.deleted_at.is_(None)
        )
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return to_conversation_response(conv)


@api_v1_router.post(
    "/projects/{project_id}/conversations",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def v1_create_conversation(
    project_id: UUID,
    payload: V1ConversationCreateRequest,
    principal: ApiTokenPrincipal = _RequireWrite,
    session: AsyncSession = Depends(V1Session),
) -> ConversationResponse:
    await _get_project_or_404(session, project_id)
    conv = Conversation(
        tenant_id=principal.tenant_id,
        project_id=project_id,
        title=payload.title,
        current_mode=payload.current_mode.value,
        created_by=None,
    )
    session.add(conv)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="conversation create conflict"
        ) from exc
    await session.refresh(conv)
    return to_conversation_response(conv)


# ===========================================================================
# Knowledge bases
# ===========================================================================
@api_v1_router.get("/kbs", response_model=list[KnowledgeBaseResponse])
async def v1_list_kbs(
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: ApiTokenPrincipal = _RequireRead,
    session: AsyncSession = Depends(V1Session),
) -> list[KnowledgeBaseResponse]:
    stmt = (
        select(KnowledgeBase)
        .where(KnowledgeBase.deleted_at.is_(None))
        .order_by(KnowledgeBase.created_at.desc(), KnowledgeBase.id)
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    # `to_kb_response` accepts an optional embedded category; the public v1
    # listing omits it (a slim representation), matching the inverse project
    # listing in the interactive router.
    return [to_kb_response(kb) for kb in result.scalars().all()]


@api_v1_router.get("/kbs/{kb_id}", response_model=KnowledgeBaseResponse)
async def v1_get_kb(
    kb_id: UUID,
    _: ApiTokenPrincipal = _RequireRead,
    session: AsyncSession = Depends(V1Session),
) -> KnowledgeBaseResponse:
    result = await session.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.deleted_at.is_(None))
    )
    kb = result.scalar_one_or_none()
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="kb not found")
    return to_kb_response(kb)


@api_v1_router.post(
    "/kbs", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED
)
async def v1_create_kb(
    payload: V1KnowledgeBaseCreateRequest,
    principal: ApiTokenPrincipal = _RequireWrite,
    session: AsyncSession = Depends(V1Session),
) -> KnowledgeBaseResponse:
    # Pre-check the (tenant_id, name) uniqueness so the 409 carries a clean
    # message instead of leaking the driver error (mirrors the interactive
    # KB router). The unique index is partial on `deleted_at IS NULL`.
    existing = await session.execute(
        select(KnowledgeBase.id).where(
            KnowledgeBase.name == payload.name,
            KnowledgeBase.deleted_at.is_(None),
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="kb name already exists in tenant"
        )
    kb = KnowledgeBase(
        tenant_id=principal.tenant_id,
        name=payload.name,
        description=payload.description,
        embedding_model_id=payload.embedding_model_id or "nomic-embed-text-v1.5",
        created_by=None,
    )
    session.add(kb)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="kb name already exists in tenant"
        ) from exc
    await session.refresh(kb)
    return to_kb_response(kb)


__all__ = ["api_v1_router"]
