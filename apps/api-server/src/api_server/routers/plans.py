"""`/projects/{project_id}/plans` and `/plans/{plan_id}` endpoints
(Plan 03 task_03_14 + task_03_15 + task_03_16).

A plan stores the canonical-template specification the planning chat
produces. The endpoints here cover the basics — create, list, read,
partial update, soft-delete — plus the DAG cycle check on persist
(task_03_15) and the state-machine transitions (task_03_16).

Creation paths:
  - inline:  POST with `specification` body -> persisted as-is.
  - chat:    POST with `conversation_id` only -> an empty draft tied
             to the conversation; the planning sub-graph fills the
             specification later (Fase G wiring).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_principal, get_tenant_session
from api_server.chat.dag import DAGCycleError, validate_dag
from api_server.chat.plan_state_machine import (
    PlanTransitionError,
    transition_plan_status,
)
from api_server.db.conversation import Conversation
from api_server.db.domain import Plan, Project
from api_server.db.plan_comment import PlanComment
from api_server.routers._helpers import (
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.schemas.plans import (
    PlanCommentCreateRequest,
    PlanCommentResponse,
    PlanCreateRequest,
    PlanResponse,
    PlanUpdateRequest,
    to_plan_comment_response,
    to_plan_response,
)

project_plans_router = APIRouter(prefix="/projects/{project_id}/plans", tags=["plans"])
plans_router = APIRouter(prefix="/plans", tags=["plans"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _verify_project_visible(session: AsyncSession, project_id: UUID) -> Project:
    result = await session.execute(
        select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


async def _verify_conversation_in_project(
    session: AsyncSession, conversation_id: UUID, project_id: UUID
) -> Conversation:
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.deleted_at.is_(None),
        )
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    if conv.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="conversation belongs to a different project",
        )
    return conv


# ===========================================================================
# Project-scoped endpoints
# ===========================================================================
@project_plans_router.post("", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    project_id: UUID,
    payload: PlanCreateRequest,
    principal: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> PlanResponse:
    tenant_id = require_tenant_id(principal)
    await _verify_project_visible(session, project_id)

    if payload.conversation_id is not None:
        await _verify_conversation_in_project(session, payload.conversation_id, project_id)

    spec_dict = payload.specification.model_dump() if payload.specification else {}

    # Cycle check (task_03_15). The Pydantic validator handles unknown
    # deps + duplicate ids; the cycle check needs the full graph.
    if spec_dict.get("tasks"):
        try:
            validate_dag(spec_dict["tasks"])
        except DAGCycleError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "dag_cycle", "cycle": exc.cycle},
            ) from exc

    plan = Plan(
        tenant_id=tenant_id,
        project_id=project_id,
        title=payload.title or "Borrador del plan",
        description=payload.description,
        status=payload.status.value,
        conversation_id=payload.conversation_id,
        specification=spec_dict,
        created_by=principal.user_id,
    )
    session.add(plan)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc.orig)) from exc
    await session.refresh(plan)

    # Back-link the conversation to the plan so the chat UI can show
    # the "this conversation produced plan X" badge.
    if payload.conversation_id is not None:
        conv = await session.get(Conversation, payload.conversation_id)
        if conv is not None:
            conv.related_plan_id = plan.id
            await session.flush()

    return to_plan_response(plan)


@project_plans_router.get("", response_model=list[PlanResponse])
async def list_plans(
    project_id: UUID,
    status_: str | None = Query(default=None, alias="status"),
    _: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[PlanResponse]:
    await _verify_project_visible(session, project_id)
    stmt = select(Plan).where(Plan.project_id == project_id, Plan.deleted_at.is_(None))
    if status_ is not None:
        stmt = stmt.where(Plan.status == status_)
    stmt = stmt.order_by(Plan.created_at.desc())
    result = await session.execute(stmt)
    return [to_plan_response(p) for p in result.scalars().all()]


# ===========================================================================
# Plan-scoped endpoints
# ===========================================================================
async def _load_plan(session: AsyncSession, plan_id: UUID) -> Plan:
    result = await session.execute(
        select(Plan).where(Plan.id == plan_id, Plan.deleted_at.is_(None))
    )
    plan = result.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan not found")
    return plan


@plans_router.get("/{plan_id}", response_model=PlanResponse)
async def get_plan(
    plan_id: UUID,
    _: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> PlanResponse:
    return to_plan_response(await _load_plan(session, plan_id))


@plans_router.put("/{plan_id}", response_model=PlanResponse)
async def update_plan(
    plan_id: UUID,
    payload: PlanUpdateRequest,
    principal: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> PlanResponse:
    require_tenant_id(principal)
    plan = await get_writable_or_404(
        session, Plan, plan_id, principal, not_found_detail="plan not found"
    )

    # Status moves go through the state machine, not a raw assignment.
    if payload.status is not None and payload.status.value != plan.status:
        try:
            transition_plan_status(plan, payload.status.value, actor=principal.user_id)
        except PlanTransitionError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "invalid_plan_transition",
                    "from": exc.from_status,
                    "to": exc.to_status,
                },
            ) from exc

    spec_dict = payload.specification.model_dump() if payload.specification else None
    if spec_dict is not None and spec_dict.get("tasks"):
        try:
            validate_dag(spec_dict["tasks"])
        except DAGCycleError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "dag_cycle", "cycle": exc.cycle},
            ) from exc

    # Avoid touching `status` (already handled by the state machine
    # above) when the partial-update helper walks the payload. We do
    # this by walking the user-set fields manually here, instead of
    # leaning on `apply_partial_update` (which would treat an explicit
    # None on `status` as "set to null").
    changes = payload.model_dump(exclude_unset=True, exclude={"status"})
    if "specification" in changes and spec_dict is not None:
        changes["specification"] = spec_dict
    for attr, value in changes.items():
        setattr(plan, attr, value)

    await session.flush()
    await session.refresh(plan)
    return to_plan_response(plan)


@plans_router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plan(
    plan_id: UUID,
    principal: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    require_tenant_id(principal)
    plan = await get_writable_or_404(
        session, Plan, plan_id, principal, not_found_detail="plan not found"
    )
    await soft_delete(session, plan)


# ===========================================================================
# Inline plan comments (task_03_21)
# ===========================================================================
@plans_router.post(
    "/{plan_id}/comments",
    response_model=PlanCommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_plan_comment(
    plan_id: UUID,
    payload: PlanCommentCreateRequest,
    principal: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> PlanCommentResponse:
    tenant_id = require_tenant_id(principal)
    plan = await _load_plan(session, plan_id)

    # Validate the target_ref points to a real phase/task in the spec.
    if payload.target_kind == "task":
        task_ids = {t.get("id") for t in plan.specification.get("tasks") or []}
        if payload.target_ref not in task_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"task {payload.target_ref!r} not in plan specification",
            )
    elif payload.target_kind == "phase":
        phases = plan.specification.get("phases") or []
        try:
            idx = int(payload.target_ref or "")
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="phase target_ref must be the phase index as a string",
            ) from exc
        if idx < 0 or idx >= len(phases):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"phase {idx} not in plan specification",
            )

    comment = PlanComment(
        tenant_id=tenant_id,
        plan_id=plan.id,
        target_kind=payload.target_kind,
        target_ref=payload.target_ref,
        author_user_id=principal.user_id,
        content=payload.content,
    )
    session.add(comment)
    await session.flush()
    await session.refresh(comment)
    return to_plan_comment_response(comment)


@plans_router.get("/{plan_id}/comments", response_model=list[PlanCommentResponse])
async def list_plan_comments(
    plan_id: UUID,
    _: AuthPrincipal = Depends(get_principal),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[PlanCommentResponse]:
    # Ensures the plan is visible under RLS before listing comments.
    await _load_plan(session, plan_id)
    result = await session.execute(
        select(PlanComment)
        .where(PlanComment.plan_id == plan_id, PlanComment.deleted_at.is_(None))
        .order_by(PlanComment.created_at)
    )
    return [to_plan_comment_response(c) for c in result.scalars().all()]


__all__ = ["plans_router", "project_plans_router"]
