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

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.chat.cost import (
    DEFAULT_HOURLY_RATE_EUR,
    compute_ai_cost,
    compute_human_cost,
)
from api_server.chat.dag import DAGCycleError, validate_dag
from api_server.chat.plan_state_machine import (
    PlanTransitionError,
    SameSignerError,
    transition_plan_status,
)
from api_server.chat.sync_to_kanban import SyncScopeError, sync_plan_to_kanban
from api_server.db.conversation import Conversation
from api_server.db.domain import Plan, PlanStatus, Project, Task
from api_server.db.models import Organization
from api_server.db.plan_comment import PlanComment
from api_server.db.platform_settings import get_double_signature_threshold
from api_server.db.review_session_repo import list_review_sessions_for_plan
from api_server.routers._helpers import (
    get_writable_or_404,
    require_tenant_id,
    soft_delete,
)
from api_server.routers._pagination import (
    apply_pagination,
    limit_query,
    offset_query,
)
from api_server.routers.review import build_review_urls
from api_server.schemas.plans import (
    AICostBreakdownResponse,
    CostBreakdownResponse,
    HumanCostBreakdownResponse,
    PlanCommentCreateRequest,
    PlanCommentResponse,
    PlanCreateRequest,
    PlanResponse,
    PlanSyncRequest,
    PlanSyncResponse,
    PlanUpdateRequest,
    TaskAICostResponse,
    TaskHumanCostResponse,
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
    principal: AuthPrincipal = Depends(require_tenant_member),
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
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[PlanResponse]:
    await _verify_project_visible(session, project_id)
    stmt = select(Plan).where(Plan.project_id == project_id, Plan.deleted_at.is_(None))
    if status_ is not None:
        stmt = stmt.where(Plan.status == status_)
    stmt = stmt.order_by(Plan.created_at.desc(), Plan.id)
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
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
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> PlanResponse:
    return to_plan_response(await _load_plan(session, plan_id))


@plans_router.get("/{plan_id}/review-session")
async def get_plan_review_session(
    plan_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, object]:
    """Latest review session of a plan + freshly-signed reviewer URLs (ADR 0062).

    When a plan is in ``pending_human_validation`` the orchestrator spawns a
    review-runtime that serves the built app. This endpoint hands the operator a
    CLICKABLE link to open + test that app (``app_url``) and the reviewer SPA
    (``review_url``); both are HMAC-signed. 404 if the plan has no review
    session yet.
    """
    await _load_plan(session, plan_id)  # 404 + RLS visibility check
    sessions = await list_review_sessions_for_plan(session, plan_id)
    if not sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no review session for this plan yet",
        )
    # Prefer a live (running/suspended) session; otherwise the newest.
    row = next((s for s in sessions if s.status in {"running", "suspended"}), sessions[0])
    urls = build_review_urls(row.id, row.expires_at.timestamp())
    return {
        "session_id": str(row.id),
        "status": row.status,
        "verdict": row.verdict,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "review_url": urls["review_url"],
        "app_url": urls["app_url"],
        "verdict_url": urls["verdict_url"],
    }


@plans_router.put("/{plan_id}", response_model=PlanResponse)
async def update_plan(
    plan_id: UUID,
    payload: PlanUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
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
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
    principal: AuthPrincipal = Depends(require_tenant_admin),
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
    principal: AuthPrincipal = Depends(require_tenant_member),
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
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[PlanCommentResponse]:
    # Ensures the plan is visible under RLS before listing comments.
    await _load_plan(session, plan_id)
    result = await session.execute(
        select(PlanComment)
        .where(PlanComment.plan_id == plan_id, PlanComment.deleted_at.is_(None))
        .order_by(PlanComment.created_at, PlanComment.id)
        .limit(limit)
        .offset(offset)
    )
    return [to_plan_comment_response(c) for c in result.scalars().all()]


# ===========================================================================
# Cost breakdown (task_03_24)
# ===========================================================================
@plans_router.get(
    "/{plan_id}/cost-breakdown",
    response_model=CostBreakdownResponse,
)
async def get_plan_cost_breakdown(
    plan_id: UUID,
    model: str | None = Query(
        default=None,
        description=(
            "Default model id to estimate AI cost against. Falls back to"
            " plan.specification.metadata.default_model_id, then 'gpt-4o'."
        ),
    ),
    hourly_rate: Decimal | None = Query(
        default=None,
        description=(
            "Per-hour rate for the human cost. Overrides the tenant's"
            " configured rate (task_03_26). Defaults to 50 EUR."
        ),
    ),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> CostBreakdownResponse:
    """Recompute the human + AI cost breakdown for a plan.

    Read-only — does NOT persist anything into the plan's specification.
    The UI calls this every time the operator opens the plan detail or
    tweaks the model / rate inputs.
    """
    plan = await _load_plan(session, plan_id)
    spec = plan.specification or {}

    # Rate resolution order (task_03_26):
    #   1. `?hourly_rate=` query override (for what-if simulations).
    #   2. Tenant's configured `organizations.hourly_rate`.
    #   3. Platform default (`DEFAULT_HOURLY_RATE_EUR`, 50 EUR).
    if hourly_rate is not None:
        rate = hourly_rate
        currency = "EUR"
    else:
        tenant_rate, tenant_currency = await _resolve_tenant_rate(session, plan.tenant_id)
        rate = tenant_rate if tenant_rate is not None else DEFAULT_HOURLY_RATE_EUR
        currency = tenant_currency or "EUR"

    human = compute_human_cost(spec, hourly_rate=rate, currency=currency)

    default_model_id = model or (spec.get("metadata") or {}).get("default_model_id") or "gpt-4o"
    ai = compute_ai_cost(spec, default_model_id=default_model_id)

    return CostBreakdownResponse(
        human=HumanCostBreakdownResponse(
            currency=human.currency,
            hourly_rate=human.hourly_rate,
            total_hours=human.total_hours,
            total_cost=human.total_cost,
            tasks=[
                TaskHumanCostResponse(
                    task_id=t.task_id,
                    title=t.title,
                    hours=t.hours,
                    cost=t.cost,
                )
                for t in human.tasks
            ],
        ),
        ai=AICostBreakdownResponse(
            currency=ai.currency,
            default_model_id=ai.default_model_id,
            cost_min=ai.cost_min,
            cost_max=ai.cost_max,
            tasks=[
                TaskAICostResponse(
                    task_id=t.task_id,
                    title=t.title,
                    complexity=t.complexity,
                    model_id=t.model_id,
                    tokens_in_min=t.tokens_in_min,
                    tokens_in_max=t.tokens_in_max,
                    tokens_out_min=t.tokens_out_min,
                    tokens_out_max=t.tokens_out_max,
                    cost_min=t.cost_min,
                    cost_max=t.cost_max,
                )
                for t in ai.tasks
            ],
            missing_models=list(ai.missing_models),
        ),
    )


# ===========================================================================
# Approve endpoint (task_03_25)
# ===========================================================================
@plans_router.post("/{plan_id}/approve", response_model=PlanResponse)
async def approve_plan(
    plan_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> PlanResponse:
    """Cast an approval signature on a plan.

    The endpoint decides single vs. double firma based on the AI cost
    estimate of the plan and the platform-configured threshold
    (`plan_approval_double_signature_threshold`):

      - ``pending_approval`` + cost <= threshold → ``approved``.
      - ``pending_approval`` + cost > threshold → ``pending_second_approval``
        (first signature; a different user must close it).
      - ``pending_second_approval`` → ``approved`` (asserts the second
        signer is not the same user as the first).

    Returns 409 with ``same_signer`` when the same user tries to cast
    both signatures, or with ``invalid_plan_transition`` on any other
    illegal move.
    """
    require_tenant_id(principal)
    plan = await get_writable_or_404(
        session, Plan, plan_id, principal, not_found_detail="plan not found"
    )

    # Decide the target status: single firma, first of two, or second of two.
    current = plan.status
    if current == PlanStatus.PENDING_SECOND_APPROVAL.value:
        target = PlanStatus.APPROVED.value
    elif current == PlanStatus.PENDING_APPROVAL.value:
        target = await _resolve_first_signature_target(session, plan)
    else:
        # /approve only meaningful from these two states.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "invalid_plan_transition",
                "from": current,
                "to": PlanStatus.APPROVED.value,
                "reason": "POST /approve is only valid from pending_approval"
                " or pending_second_approval",
            },
        )

    try:
        transition_plan_status(plan, target, actor=principal.user_id)
    except SameSignerError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "same_signer",
                "signer": str(exc.signer),
            },
        ) from exc
    except PlanTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "invalid_plan_transition",
                "from": exc.from_status,
                "to": exc.to_status,
            },
        ) from exc

    await session.flush()
    await session.refresh(plan)
    return to_plan_response(plan)


async def _resolve_tenant_rate(
    session: AsyncSession, tenant_id: UUID
) -> tuple[Decimal | None, str | None]:
    """Read the per-tenant hourly_rate override (task_03_26).

    Returns (None, None) when the tenant has not configured a rate;
    callers fall back to the platform default in that case.
    """
    result = await session.execute(select(Organization).where(Organization.id == tenant_id))
    org = result.scalar_one_or_none()
    if org is None:
        return None, None
    return org.hourly_rate, org.hourly_rate_currency


async def _resolve_first_signature_target(session: AsyncSession, plan: Plan) -> str:
    """Single firma when the AI cost falls under the platform threshold,
    double firma otherwise. Threshold of 0 (the default) forces single
    firma for everything; the operator raises it from the admin panel
    once they want a four-eye review on expensive plans."""
    threshold_raw = await get_double_signature_threshold(session)
    try:
        threshold = Decimal(threshold_raw)
    except (ArithmeticError, ValueError):
        threshold = Decimal("0")

    spec = plan.specification or {}
    default_model_id = (spec.get("metadata") or {}).get("default_model_id") or "gpt-4o"
    ai = compute_ai_cost(spec, default_model_id=default_model_id)
    # We compare against `cost_max` (worst case) so the four-eye review
    # only kicks in when the plan is *potentially* expensive.
    if threshold > 0 and ai.cost_max > threshold:
        return PlanStatus.PENDING_SECOND_APPROVAL.value
    return PlanStatus.APPROVED.value


# ===========================================================================
# Sync to Kanban (task_03_27, task_03_28, task_03_29)
# ===========================================================================
@plans_router.post("/{plan_id}/sync-to-kanban", response_model=PlanSyncResponse)
async def sync_plan_kanban(
    plan_id: UUID,
    payload: PlanSyncRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> PlanSyncResponse:
    """Materialise the plan's tasks into the Kanban.

    The scope mirrors the UI dialog: ``total`` syncs every spec task,
    ``phase`` only those of one ``phases[i]``, ``selection`` only the
    explicit list of spec task ids.

    Idempotent (task_03_29): tasks already materialised from this plan
    are reported under ``skipped_task_ids`` and reused as dependency
    targets for any new siblings. Calling the endpoint twice with the
    same scope is a no-op the second time.
    """
    require_tenant_id(principal)
    plan = await get_writable_or_404(
        session, Plan, plan_id, principal, not_found_detail="plan not found"
    )

    try:
        result = await sync_plan_to_kanban(
            session,
            plan,
            scope=payload.scope,
            phase_index=payload.phase_index,
            task_ids=payload.task_ids,
        )
    except SyncScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_sync_scope", "message": str(exc)},
        ) from exc

    return PlanSyncResponse(
        created_task_ids=result.created_task_ids,
        skipped_task_ids=result.skipped_task_ids,
        dependencies_created=result.dependencies_created,
    )


# ---------------------------------------------------------------------------
# Plan 06.5 task_06_5_06 — free task creation
# ---------------------------------------------------------------------------


class FreeTaskRequest(BaseModel):
    """Body of `POST /plans/{plan_id}/free-task`."""

    model_config = ConfigDict(populate_by_name=True, str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=200)
    description: str | None = None


@plans_router.post(
    "/{plan_id}/free-task",
    status_code=status.HTTP_201_CREATED,
)
async def create_free_task(
    plan_id: UUID,
    payload: FreeTaskRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, object]:
    """Create a plan-scoped task NOT bound to any checkbox of the spec.

    Useful when the human, during plan validation, detects work that
    wasn't in the original plan and wants to add it without going back
    through the planning chat. The created task lives under the plan
    (so the Kanban filtered by plan shows it) and starts in `backlog`.

    `inputs.is_free_task=true` marks the row so analytics / dashboards
    can distinguish manually-added work from agent-driven tasks. The
    field is not a column — we keep it inside the JSONB to avoid yet
    another migration for a UI hint.
    """
    tenant_id = require_tenant_id(principal)
    plan = await _load_plan(session, plan_id)

    task = Task(
        tenant_id=tenant_id,
        project_id=plan.project_id,
        plan_id=plan.id,
        title=payload.title,
        description=payload.description,
        status="backlog",
        priority="medium",
        inputs={"is_free_task": True},
    )
    session.add(task)
    await session.flush()

    return {
        "id": str(task.id),
        "plan_id": str(plan.id),
        "project_id": str(task.project_id),
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "is_free_task": True,
    }


# ---------------------------------------------------------------------------
# Plan 06.5 task_06_5_07 — escalated tasks listing
# ---------------------------------------------------------------------------


@plans_router.get("/{plan_id}/escalated-tasks")
async def list_escalated_tasks(
    plan_id: UUID,
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, list[dict[str, object]]]:
    """Tasks of the plan currently in `awaiting_human_approval`.

    Each entry carries its `retry_count` and the latest 20 audit
    events so the UI can render the timeline of rejections + actions
    without a second round-trip per task.

    Shape:

        {"tasks": [
          {"id": "...", "title": "...", "description": "...",
           "retry_count": 3,
           "history": [
             {"id": "...", "at": 1716889200.123,
              "kind": "review_comment", ...},
             ...
           ]}
        ]}
    """
    from api_server.db.task_audit_repo import list_history as _list_history
    from api_server.db.task_audit_repo import to_dict as _audit_to_dict

    await _load_plan(session, plan_id)  # raises 404 if not visible

    task_rows = (
        (
            await session.execute(
                select(Task)
                .where(
                    Task.plan_id == plan_id,
                    Task.status == "awaiting_human_approval",
                )
                .order_by(Task.created_at, Task.id)
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    out: list[dict[str, object]] = []
    for task in task_rows:
        events = await _list_history(session, task.id, limit=20)
        out.append(
            {
                "id": str(task.id),
                "title": task.title,
                "description": task.description,
                "retry_count": task.retry_count,
                "history": [_audit_to_dict(e) for e in events],
            }
        )
    return {"tasks": out}


__all__ = ["plans_router", "project_plans_router"]
