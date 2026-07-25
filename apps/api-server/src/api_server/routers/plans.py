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

import contextlib
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import partial
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_redis,
    get_tenant_session,
    require_can_approve_plan,
    require_tenant_admin,
    require_tenant_member,
    schedule_after_commit,
)
from api_server.celery_client import (
    compute_plan_code_diff_and_wait,
    enqueue_compose_review_runtime,
    revoke_job_callback,
)
from api_server.chat.corrections_llm import generate_corrective_tasks
from api_server.chat.cost import (
    DEFAULT_HOURLY_RATE_EUR,
    AICostBreakdown,
    compute_ai_cost,
    compute_human_cost,
)
from api_server.chat.cost_resolution import load_price_catalog, resolve_plan_task_models
from api_server.chat.dag import DAGCycleError, validate_dag
from api_server.chat.plan_corrections import (
    append_corrections,
    find_correction_for_session,
    mark_corrections_accepted,
)
from api_server.chat.plan_state_machine import (
    PlanPutForbiddenError,
    PlanTransitionError,
    SameSignerError,
    assert_generic_put_transition,
    transition_plan_status,
)
from api_server.chat.responder import _resolve_chat_provider, resolve_chat_model_config
from api_server.chat.sync_to_kanban import SyncScopeError, sync_plan_to_kanban
from api_server.dag_promotion import announce_ready_tasks, promote_ready_tasks
from api_server.db.conversation import Conversation, Message
from api_server.db.domain import Execution, Plan, PlanStatus, Project, Task
from api_server.db.execution_repo import cancel_tasks_and_executions
from api_server.db.models import Organization
from api_server.db.plan_comment import PlanComment
from api_server.db.platform_settings import get_double_signature_threshold
from api_server.db.review_session_repo import (
    list_active_preview_sessions,
    list_review_sessions_for_plan,
)
from api_server.events import publish_task_status_changed
from api_server.llm_providers.vault import LLMProviderVaultStore
from api_server.plan_progress import TaskSnapshot, compute_plan_progress
from api_server.preview_launch import build_preview_request
from api_server.routers._helpers import (
    get_writable_or_404,
    require_project_active,
    require_tenant_id,
    soft_delete,
)
from api_server.routers._pagination import (
    apply_pagination,
    limit_query,
    offset_query,
)
from api_server.routers.llm_providers import get_provider_vault_store
from api_server.routers.review import build_review_urls
from api_server.routers.task_lifecycle import apply_task_retry, reactivate_plan_if_unstuck
from api_server.schemas.plans import (
    AICostBreakdownResponse,
    CostBreakdownResponse,
    HumanCostBreakdownResponse,
    PlanAcceptCorrectionsRequest,
    PlanCommentCreateRequest,
    PlanCommentResponse,
    PlanCreateRequest,
    PlanGenerateCorrectionsResponse,
    PlanResponse,
    PlanSyncRequest,
    PlanSyncResponse,
    PlanUpdateRequest,
    TaskAICostResponse,
    TaskHumanCostResponse,
    to_plan_comment_response,
    to_plan_response,
)
from api_server.slug import slugify

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


# PROY2-02: estados de tarea NO terminales — un plan con alguno de estos no
# puede entrar en pending_human_validation. Espejo de plan_progress._OPEN_TASK_STATUSES.
_OPEN_TASK_STATUSES = ("backlog", "ready", "in_progress", "in_review", "blocked")

# PROY2-13: estados de plan CERRADO que no aceptan tareas nuevas.
_CLOSED_PLAN_STATUSES = (
    PlanStatus.COMPLETED.value,
    PlanStatus.CANCELLED.value,
    PlanStatus.ARCHIVED.value,
)


async def _plan_has_open_tasks(session: AsyncSession, plan_id: UUID) -> bool:
    """¿Le queda al plan alguna tarea no terminal (ni done ni cancelled)?"""
    # `tasks` no es soft-deletable (no tiene deleted_at); un plan cancelado ya
    # cancela sus tareas, así que basta el filtro por estado abierto.
    count = (
        await session.execute(
            select(func.count())
            .select_from(Task)
            .where(
                Task.plan_id == plan_id,
                Task.status.in_(_OPEN_TASK_STATUSES),
            )
        )
    ).scalar_one()
    return int(count) > 0


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


async def _draft_from_conversation(
    session: AsyncSession, conversation_id: UUID
) -> tuple[str | None, dict[str, Any]] | None:
    """The plan draft the planning chat produced: the latest ``agent`` message's
    ``{kind: planning_directive, intent: finish_planning}`` attachment, as
    ``(title, specification)``. ``None`` when the chat never finalised a plan.

    This is the chat→plan materialisation (task_03_14): the planning sub-graph
    attaches a structured ``specification`` when the PM finishes; ``create_plan``
    with only a ``conversation_id`` lifts it so the Plan is born with its tasks."""
    rows = (
        (
            await session.execute(
                select(Message)
                .where(
                    Message.conversation_id == conversation_id,
                    Message.author_kind == "agent",
                )
                .order_by(Message.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for msg in rows:
        for att in msg.attachments or []:
            if (
                isinstance(att, dict)
                and att.get("kind") == "planning_directive"
                and att.get("intent") == "finish_planning"
                and isinstance(att.get("specification"), dict)
            ):
                title = att.get("title")
                return (str(title) if title else None, dict(att["specification"]))
    return None


# Estados en los que un plan ya NO retiene su conversación: volver a generar
# desde ese mismo chat es legítimo (A-05).
_SUPERSEDABLE_PLAN_STATUSES: frozenset[str] = frozenset({"cancelled", "rejected"})


async def _resolve_initial_spec(
    session: AsyncSession, payload: PlanCreateRequest
) -> tuple[str | None, dict[str, Any]]:
    """El spec con el que NACE un plan, ya normalizado y validado.

    Tres orígenes, en orden: el cuerpo inline gana; si no, el attachment del
    chat de planning (materialización chat→plan, task_03_14); si no, vacío.
    Devuelve ``(título_del_borrador, spec)``.

    Extraído de ``create_plan`` porque la función pasaba de 12 ramas: aquí vive
    todo lo que decide QUÉ spec se persiste, y allí solo el ciclo de vida del
    plan. Lanza 422 (nunca 500) ante un DAG inválido.
    """
    draft_title: str | None = None
    if payload.specification is not None:
        spec_dict: dict[str, Any] = payload.specification.model_dump()
    elif payload.conversation_id is not None:
        drafted = await _draft_from_conversation(session, payload.conversation_id)
        spec_dict = drafted[1] if drafted is not None else {}
        draft_title = drafted[0] if drafted is not None else None
    else:
        spec_dict = {}

    # A-03: el draft de conversación NO pasa por Pydantic (ver abajo), así que un
    # `summary` en forma antigua —cadena— se colaba al JSONB y hacía fallar con
    # 422 cualquier `PUT` posterior que reenviara el spec, además de pintar una
    # tarjeta «Resumen» vacía. El emisor ya manda el objeto; esto cubre los
    # borradores viejos que sigan vivos en una conversación.
    if isinstance(spec_dict.get("summary"), str):
        text = spec_dict["summary"].strip()
        spec_dict["summary"] = {"description": text} if text else {}

    # Cycle check (task_03_15). The Pydantic validator handles unknown deps +
    # duplicate ids for the INLINE spec; the conversation draft (PROY2-12)
    # bypasses Pydantic, so validate_dag's ValueError (duplicate id, missing id)
    # must land as 422, never a 500.
    if spec_dict.get("tasks"):
        try:
            validate_dag(spec_dict["tasks"])
        except DAGCycleError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"error": "dag_cycle", "cycle": exc.cycle},
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"error": "invalid_spec", "message": str(exc)},
            ) from exc
    return draft_title, spec_dict


async def _live_plan_of_conversation(session: AsyncSession, conversation_id: UUID) -> Plan | None:
    """El plan VIVO que esta conversación ya produjo, si lo hay (A-05).

    Se resuelve por el back-link `conversation.related_plan_id`, que es el que
    `create_plan` escribe. ``None`` cuando la conversación no existe, no tiene
    plan, o el que tiene está cancelado/rechazado — en ese caso generar otro es
    el comportamiento correcto, no un duplicado."""
    conv = await session.get(Conversation, conversation_id)
    if conv is None or conv.related_plan_id is None:
        return None
    plan = await session.get(Plan, conv.related_plan_id)
    if plan is None or plan.status in _SUPERSEDABLE_PLAN_STATUSES:
        return None
    return plan


# ===========================================================================
# Project-scoped endpoints
# ===========================================================================
@project_plans_router.post("", response_model=PlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    project_id: UUID,
    payload: PlanCreateRequest,
    response: Response,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> PlanResponse:
    tenant_id = require_tenant_id(principal)
    project = await _verify_project_visible(session, project_id)
    # P1-01: un proyecto pausado/archivado no acepta planes nuevos.
    require_project_active(project)

    # A-05: idempotencia por conversación. Sin esto, volver al chat y pulsar
    # «Generar Plan» otra vez creaba un plan GEMELO: el attachment sigue ahí,
    # `_draft_from_conversation` lo vuelve a levantar y `related_plan_id` se
    # sobrescribe — el primero queda huérfano del back-link pero vivo,
    # sincronizable y ejecutable, compitiendo por el mismo worktree.
    # Se devuelve el existente con 200 (no 201): decir «created» de algo que no
    # se ha creado es mentir, y es la señal que la UI necesita para avisar.
    # Un plan cancelado/rechazado NO cuenta: ahí re-planificar es legítimo.
    if payload.conversation_id is not None:
        existing = await _live_plan_of_conversation(session, payload.conversation_id)
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return to_plan_response(existing)

    # PROY2-01: un plan solo puede NACER como borrador o pendiente de
    # aprobación — no `approved`/`in_progress`/`completed` (esquivaría approve,
    # RBAC y la doble firma). Los estados avanzados se alcanzan por sus
    # transiciones con gate.
    if payload.status not in (PlanStatus.DRAFT, PlanStatus.PENDING_APPROVAL):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "error": "invalid_initial_status",
                "allowed": [PlanStatus.DRAFT.value, PlanStatus.PENDING_APPROVAL.value],
            },
        )

    if payload.conversation_id is not None:
        await _verify_conversation_in_project(session, payload.conversation_id, project_id)

    # Spec sources: inline body wins; else lift the planning chat's draft attachment
    # (chat→plan materialisation, task_03_14); else an empty draft.
    draft_title, spec_dict = await _resolve_initial_spec(session, payload)

    plan_title = payload.title or draft_title or "Borrador del plan"
    plan = Plan(
        tenant_id=tenant_id,
        project_id=project_id,
        title=plan_title,
        # prod-18 / ADR 0085: stable slug for the plan branch (plan/{id8}-{slug}).
        slug=slugify(plan_title),
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
# ADR 0099: diff de CODIGO de la rama del plan (read-only)
# ===========================================================================
@project_plans_router.get("/{plan_id}/code-diff")
async def get_plan_code_diff(
    project_id: UUID,
    plan_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, Any]:
    """Que cambio la rama del plan respecto a su merge-base con la default.

    Read-only sobre el BARE real del proyecto (misma identidad de coordenadas
    que provision/commit/review: worktree_coordinates). Cuerpo acotado
    (MAX_DIFF_CHARS, truncado honesto) + resumen numstat completo + lineas
    clasificadas para el renderer del visor de docs. Tenant-safe via RLS; un
    plan sin rama material (aun sin commits) responde 404 con detalle neutro.
    """
    # Fix 2026-07-24: el diff se calcula EN EL WORKER. Antes corría en la
    # api-server, que NO monta el volumen agent-data (data_root default
    # /data/agent-platform inexistente allí) → subprocess.run(cwd=bare) lanzaba
    # FileNotFoundError NO capturado → 500 SIEMPRE. El worker posee el data_root
    # real + corre como owner de los bares; la api-server delega y relaya.
    tenant_id = require_tenant_id(principal)
    await _verify_project_visible(session, project_id)
    plan = await _load_plan(session, plan_id)
    if plan.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="plan not found")
    project = await session.get(Project, project_id)
    org = (
        await session.execute(select(Organization).where(Organization.id == tenant_id))
    ).scalar_one_or_none()
    if project is None or not project.slug or org is None or not org.slug or not plan.slug:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="plan has no materialised branch yet",
        )
    result = await compute_plan_code_diff_and_wait(
        tenant_slug=org.slug,
        project_slug=project.slug,
        plan_id=str(plan.id),
        plan_slug=plan.slug,
    )
    if not result.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no diff available for this plan (branch or repo not materialised)",
        )
    return {
        "plan_id": str(plan.id),
        "plan_branch": result.get("plan_branch"),
        "default_branch": result.get("default_branch"),
        "base_sha": result.get("base_sha"),
        "head_sha": result.get("head_sha"),
        "unchanged": result.get("unchanged"),
        "truncated": result.get("truncated"),
        "files": result.get("files") or [],
        "lines": result.get("lines") or [],
    }


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


@plans_router.get("", response_model=list[PlanResponse])
async def list_all_plans(
    project_id: UUID | None = Query(default=None),
    status_: str | None = Query(default=None, alias="status"),
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[PlanResponse]:
    """Tenant-wide plan list (c8/T11, ADR 0008): every plan across the tenant's
    projects, for the management board (a Kanban of PLANS). RLS scopes the query to the
    caller's tenant; optional ``?project_id`` / ``?status`` filters + pagination. This
    replaces the board's obsolete project-as-plan placeholder without an N+1 fan-out."""
    stmt = select(Plan).where(Plan.deleted_at.is_(None))
    if project_id is not None:
        stmt = stmt.where(Plan.project_id == project_id)
    if status_ is not None:
        stmt = stmt.where(Plan.status == status_)
    stmt = stmt.order_by(Plan.created_at.desc(), Plan.id)
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    result = await session.execute(stmt)
    return [to_plan_response(p) for p in result.scalars().all()]


@plans_router.get("/{plan_id}", response_model=PlanResponse)
async def get_plan(
    plan_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> PlanResponse:
    return to_plan_response(await _load_plan(session, plan_id))


class PlanProgressResponse(BaseModel):
    total: int
    done: int
    open: int
    label: str


class PlanPRResponse(BaseModel):
    url: str | None = None
    branch: str | None = None
    error: str | None = None


class PlanCostStatusResponse(BaseModel):
    ai_currency: str
    human_currency: str
    estimated_ai_min: Decimal
    estimated_ai_max: Decimal
    estimated_human_hours: Decimal
    estimated_human_cost: Decimal
    actual_ai_cost: Decimal
    actual_tokens: int
    actual_runs: int
    over_estimate: bool


class PlanStatusResponse(BaseModel):
    """Everything the plan header shows, in ONE call (task_wf_30)."""

    plan_id: UUID
    status: str
    progress: PlanProgressResponse
    pr: PlanPRResponse
    cost: PlanCostStatusResponse


@plans_router.get("/{plan_id}/status", response_model=PlanStatusResponse)
async def get_plan_status(
    plan_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> PlanStatusResponse:
    """The plan's state of play: progress, PR and estimated-vs-actual cost.

    ONE endpoint instead of the four sections the first version of this plan
    proposed — less code, and the operator reads the plan's state in one place
    rather than four (task_wf_30). It folds three separate blind spots:

    * **D-01** — ``compute_plan_progress`` has been written and tested since Plan
      06 and its only consumer was the un-wired demo ``plan_runner``. No endpoint,
      no progress anywhere in the product.
    * **D-02** — ``pr_url``/``pr_branch``/``pr_error`` travelled in the plan
      response with ZERO occurrences in the frontend: the operator approved a plan
      and never saw the PR, nor why it failed.
    * **D-04** — the estimate was computed in full (``/cost-breakdown``) and the
      actual spend was aggregated nowhere. A budget that is never contrasted is
      not a budget.
    """
    plan = await _load_plan(session, plan_id)

    task_rows = (
        (await session.execute(select(Task.id, Task.status).where(Task.plan_id == plan.id)))
        .tuples()
        .all()
    )
    progress = compute_plan_progress(
        str(plan.id),
        [TaskSnapshot(id=str(tid), status=str(tstatus)) for tid, tstatus in task_rows],
    )

    # Las filas de SQLAlchemy exponen las columnas como atributos, así que
    # `aggregate_actual_spend` las lee igual que a un `Execution` completo — sin
    # traer las columnas pesadas (steps_log, output) que la cabecera no usa.
    executions: list[Any] = []
    if task_rows:
        executions = list(
            (
                await session.execute(
                    select(Execution.total_cost_usd, Execution.total_tokens).where(
                        Execution.task_id.in_([tid for tid, _ in task_rows])
                    )
                )
            ).all()
        )
    spend = aggregate_actual_spend(executions)

    tenant_rate, tenant_currency = await _resolve_tenant_rate(session, plan.tenant_id)
    human = compute_human_cost(
        plan.specification or {},
        hourly_rate=tenant_rate if tenant_rate is not None else DEFAULT_HOURLY_RATE_EUR,
        currency=tenant_currency or "EUR",
    )
    ai = await _compute_plan_ai_cost(session, plan)

    cost = build_plan_cost_status(
        estimated_ai_usd_min=ai.cost_min,
        estimated_ai_usd_max=ai.cost_max,
        estimated_human_hours=human.total_hours,
        estimated_human_cost=human.total_cost,
        human_currency=human.currency,
        actual_cost_usd=spend.cost_usd,
        actual_tokens=spend.tokens,
        actual_runs=spend.runs,
    )

    return PlanStatusResponse(
        plan_id=plan.id,
        status=str(plan.status),
        progress=PlanProgressResponse(
            total=progress.total,
            done=progress.done,
            open=progress.open,
            label=progress.label,
        ),
        pr=PlanPRResponse(url=plan.pr_url, branch=plan.pr_branch, error=plan.pr_error),
        cost=PlanCostStatusResponse(**cost.__dict__),
    )


@plans_router.post("/{plan_id}/unblock")
async def unblock_plan(
    plan_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, object]:
    """Un-stick a blocked plan in one gesture (T7c/c3 part D): reactivate it
    (blocked→in_progress) and re-enqueue ALL its `blocked` tasks (each → ready/backlog +
    reset retry budget, the same as the per-task ``retry`` action). The natural
    counterpart of the plan-level ``plan_blocked`` notification. 409 if not blocked."""
    plan = await _load_plan(session, plan_id)
    if plan.status != PlanStatus.BLOCKED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"plan is '{plan.status}', not 'blocked'",
        )
    transition_plan_status(plan, PlanStatus.IN_PROGRESS.value)
    blocked_tasks = (
        (
            await session.execute(
                select(Task).where(Task.plan_id == plan_id, Task.status == "blocked")
            )
        )
        .scalars()
        .all()
    )
    for task in blocked_tasks:
        await apply_task_retry(session, task)
        if task.status == "ready":
            schedule_after_commit(
                session,
                partial(
                    publish_task_status_changed,
                    get_redis(),
                    task,
                    old_status="blocked",
                    new_status="ready",
                ),
            )
    await session.flush()
    return {
        "plan_id": str(plan_id),
        "status": plan.status,
        "tasks_retried": len(blocked_tasks),
    }


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
        # ADR 0107: la tarjeta de correcciones del plan rechazado muestra el
        # motivo antes de generar las tareas correctivas.
        "rejection_reason": row.rejection_reason,
        "expires_at": row.expires_at.isoformat() if row.expires_at else None,
        "review_url": urls["review_url"],
        "app_url": urls["app_url"],
        "verdict_url": urls["verdict_url"],
    }


# ---------------------------------------------------------------------------
# App-preview on-demand de un PLAN (ADR 0130) — levantar la app de la rama del
# plan durante 24h, sin veredicto. Útil para re-inspeccionar el resultado de un
# plan cuya validación humana (48h) ya caducó.
# ---------------------------------------------------------------------------
def _plan_preview_payload(row: object) -> dict[str, object]:
    urls = build_review_urls(row.id, row.expires_at.timestamp())  # type: ignore[attr-defined]
    return {
        "session_id": str(row.id),  # type: ignore[attr-defined]
        "status": row.status,  # type: ignore[attr-defined]
        "app_url": urls["app_url"],
        "expires_at": (
            row.expires_at.isoformat() if row.expires_at else None  # type: ignore[attr-defined]
        ),
        "app_configured": bool((row.spec or {}).get("app_configured", True)),  # type: ignore[attr-defined]
    }


@plans_router.post("/{plan_id}/preview", status_code=status.HTTP_202_ACCEPTED)
async def launch_plan_preview(
    plan_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, object]:
    """Launch an on-demand app-preview of a PLAN's branch (ADR 0130, 24h, no
    verdict). Idempotent per plan; 409 when the project pins no app-preview
    image; 404 if the plan (or its project) isn't visible."""
    tenant_id = require_tenant_id(principal)
    plan = await _load_plan(session, plan_id)
    existing = await list_active_preview_sessions(session, plan_id=plan_id)
    if existing:
        return {"status": "running", **_plan_preview_payload(existing[0])}
    project = (
        await session.execute(
            select(Project).where(Project.id == plan.project_id, Project.deleted_at.is_(None))
        )
    ).scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    org = await session.get(Organization, tenant_id)
    request = build_preview_request(tenant_id=tenant_id, project=project, org=org, plan=plan)
    if request is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "El proyecto no tiene imagen de app-preview configurada. Configúra "
                "'repository_config.review_image' en el proyecto primero."
            ),
        )
    await enqueue_compose_review_runtime(request)
    return {"status": "provisioning"}


@plans_router.get("/{plan_id}/preview-session")
async def get_plan_preview_session(
    plan_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, object]:
    """Latest live on-demand preview of a plan + a freshly-signed app URL (ADR
    0130). 404 while none is live (the UI polls this after launching)."""
    await _load_plan(session, plan_id)
    sessions = await list_active_preview_sessions(session, plan_id=plan_id)
    if not sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="no live preview for this plan"
        )
    return _plan_preview_payload(sessions[0])


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
        # PROY2-02: el PUT genérico (require_tenant_member) no puede ejecutar
        # transiciones privilegiadas (aprobar/completar) — van por sus
        # endpoints con gate (POST /approve, submit_verdict).
        try:
            assert_generic_put_transition(plan.status, payload.status.value)
        except PlanPutForbiddenError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "privileged_transition_requires_gated_endpoint",
                    "from": exc.from_status,
                    "to": exc.to_status,
                    "use": exc.endpoint,
                },
            ) from exc
        # PROY2-02: entrar en validación humana exige que TODAS las tareas
        # estén hechas (mismo invariante que la transición del reconciler);
        # si no, es un salto manual que dejaría un plan "listo para validar"
        # con trabajo a medias.
        if payload.status == PlanStatus.PENDING_HUMAN_VALIDATION and await _plan_has_open_tasks(
            session, plan.id
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "plan_has_open_tasks",
                    "reason": "cannot enter pending_human_validation with unfinished tasks",
                },
            )
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
        # prod-06 task_prod06_cancel_02: cancelling a plan cascades — cancel its
        # non-terminal tasks and request cancellation of their running executions
        # (the worker kills the containers), then revoke the queued jobs after
        # commit. Without this a cancelled plan left its tasks/runs in flight.
        if plan.status == PlanStatus.CANCELLED.value:
            for execution in await cancel_tasks_and_executions(session, plan_id=plan.id):
                if execution.celery_task_id:
                    schedule_after_commit(session, revoke_job_callback(execution.celery_task_id))

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
    # PROY2-13: borrar el plan sin cancelar su trabajo dejaba tareas/runs en
    # vuelo colgando de un plan soft-deleted — el dispatch los seguía
    # despachando invisibles. Espejo de la cascada de cancelación (PUT
    # →cancelled) y del soft-delete de proyecto.
    for execution in await cancel_tasks_and_executions(session, plan_id=plan.id):
        if execution.celery_task_id:
            schedule_after_commit(session, revoke_job_callback(execution.celery_task_id))
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
# ===========================================================================
# Estado del plan de un vistazo (task_wf_30 — D-01, D-02, D-04)
# ===========================================================================


@dataclass(frozen=True)
class ActualSpend:
    """What a plan's runs REALLY cost, aggregated over its executions."""

    cost_usd: Decimal
    tokens: int
    runs: int


@dataclass(frozen=True)
class PlanCostStatus:
    """Estimated vs actual, with the two currencies kept apart."""

    ai_currency: str
    human_currency: str
    estimated_ai_min: Decimal
    estimated_ai_max: Decimal
    estimated_human_hours: Decimal
    estimated_human_cost: Decimal
    actual_ai_cost: Decimal
    actual_tokens: int
    actual_runs: int
    over_estimate: bool


def aggregate_actual_spend(executions: Iterable[Any]) -> ActualSpend:
    """Sum what a plan's executions actually spent.

    A FAILED run still burned tokens, so it counts: excluding failures would
    flatter the real cost exactly on the plans that cost the most. NULL columns
    (a run still in flight) contribute zero rather than breaking the sum.
    """
    cost = Decimal("0")
    tokens = 0
    runs = 0
    for row in executions:
        runs += 1
        raw_cost = getattr(row, "total_cost_usd", None)
        if raw_cost is not None:
            cost += Decimal(str(raw_cost))
        raw_tokens = getattr(row, "total_tokens", None)
        if raw_tokens:
            tokens += int(raw_tokens)
    return ActualSpend(cost_usd=cost, tokens=tokens, runs=runs)


def build_plan_cost_status(
    *,
    estimated_ai_usd_min: Decimal,
    estimated_ai_usd_max: Decimal,
    estimated_human_hours: Decimal,
    estimated_human_cost: Decimal,
    human_currency: str,
    actual_cost_usd: Decimal,
    actual_tokens: int,
    actual_runs: int,
) -> PlanCostStatus:
    """Put the estimate and the actual side by side (D-04).

    The AI estimate and the actual spend are both USD, so they compare directly.
    The HUMAN estimate is EUR and measures something else entirely (person-hours
    a human would have spent), so it is reported alongside and never subtracted:
    a single number mixing the two currencies would be fabricated.

    ``over_estimate`` only fires when there IS an estimate to exceed. A plan with
    no ``estimates`` in its spec spends what it spends, but calling that "over
    budget" would assert something nobody computed.
    """
    has_estimate = estimated_ai_usd_max > 0
    return PlanCostStatus(
        ai_currency="USD",
        human_currency=human_currency,
        estimated_ai_min=estimated_ai_usd_min,
        estimated_ai_max=estimated_ai_usd_max,
        estimated_human_hours=estimated_human_hours,
        estimated_human_cost=estimated_human_cost,
        actual_ai_cost=actual_cost_usd,
        actual_tokens=actual_tokens,
        actual_runs=actual_runs,
        over_estimate=has_estimate and actual_cost_usd > estimated_ai_usd_max,
    )


async def _compute_plan_ai_cost(
    session: AsyncSession,
    plan: Plan,
    *,
    default_model_override: str | None = None,
) -> AICostBreakdown:
    """AI cost for a plan, pricing each task by its assigned agent's resolved
    model (override or inherited — ADR 0065) instead of a blanket ``gpt-4o``.

    Tasks whose ``role`` maps to a team agent are priced with that agent's
    effective model; the rest fall back to the ``default_model_override`` (the
    ``?model=`` query) → plan ``metadata.default_model_id`` → ``gpt-4o`` chain.
    The price catalog comes from the ``model_prices`` table. Shared by the
    cost-breakdown endpoint and the approval double-signature threshold so both
    see the same numbers."""
    spec = plan.specification or {}
    default_model_id = (
        default_model_override or (spec.get("metadata") or {}).get("default_model_id") or "gpt-4o"
    )
    task_models = await resolve_plan_task_models(session, plan)
    catalog = await load_price_catalog(session)
    return compute_ai_cost(
        spec,
        default_model_id=default_model_id,
        catalog=catalog,
        task_models=task_models,
    )


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

    ai = await _compute_plan_ai_cost(session, plan, default_model_override=model)

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
async def _plan_has_any_tasks(session: AsyncSession, plan: Plan) -> bool:
    """PROY2-11: ¿el plan declara al menos una tarea? Cuenta las del spec o, si
    el spec está vacío, las tareas ya materializadas en el Kanban (un plan
    hecho solo de free-tasks). Un plan de 0 tareas no debe aprobarse ni
    arrancarse: el reconciler lo rebota a pending_human_validation al instante."""
    spec_tasks = (plan.specification or {}).get("tasks") or []
    if spec_tasks:
        return True
    count = (
        await session.execute(select(func.count()).select_from(Task).where(Task.plan_id == plan.id))
    ).scalar_one()
    return bool(count)


def _plan_has_no_tasks_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={
            "error": "plan_has_no_tasks",
            "message": "Un plan sin tareas no puede aprobarse ni arrancarse.",
        },
    )


@plans_router.post("/{plan_id}/approve", response_model=PlanResponse)
async def approve_plan(
    plan_id: UUID,
    principal: AuthPrincipal = Depends(require_can_approve_plan),
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

    # PROY2-11: tras validar el estado — un plan sin ninguna tarea no se firma.
    if not await _plan_has_any_tasks(session, plan):
        raise _plan_has_no_tasks_error()

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
    # NOTIF-3 (auditoría 2026-07-12): plan_approved estaba registrado (+plantillas
    # ES/EN) pero NADIE lo emitía. Solo cuando la aprobación queda COMPLETA
    # (no en la primera de dos firmas). Post-commit y best-effort.
    if plan.status == PlanStatus.APPROVED.value:
        plan_title, plan_tenant, plan_id_str = plan.title or "", str(plan.tenant_id), str(plan.id)

        async def _notify_plan_approved() -> None:
            from api_server.celery_client import enqueue_event_dispatch

            await enqueue_event_dispatch(
                {
                    "event_type": "plan_approved",
                    "tenant_id": plan_tenant,
                    "context": {"plan_name": plan_title, "plan_id": plan_id_str},
                }
            )

        schedule_after_commit(session, _notify_plan_approved)
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

    ai = await _compute_plan_ai_cost(session, plan)
    # We compare against `cost_max` (worst case) so the four-eye review
    # only kicks in when the plan is *potentially* expensive.
    if threshold > 0 and ai.cost_max > threshold:
        return PlanStatus.PENDING_SECOND_APPROVAL.value
    return PlanStatus.APPROVED.value


# ===========================================================================
# Sync to Kanban (task_03_27, task_03_28, task_03_29)
# ===========================================================================
# Materialising a plan's tasks is only legal once the plan is signed off: an
# unapproved draft must not seed the Kanban with work. `in_progress` is included
# so start-execution (and re-syncs while running) keep working.
_SYNCABLE_STATUSES = frozenset({PlanStatus.APPROVED.value, PlanStatus.IN_PROGRESS.value})


def _require_syncable_status(plan: Plan) -> None:
    """409 unless the plan is approved (or already in progress). Blocks
    materialising tasks from a draft / pending-approval plan."""
    if plan.status not in _SYNCABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "plan_not_approved",
                "message": (
                    "Solo un plan aprobado (o en curso) puede sincronizar tareas al Kanban; "
                    f"este plan está en estado '{plan.status}'. Apruébalo primero."
                ),
                "status": plan.status,
            },
        )


@plans_router.post("/{plan_id}/start-execution", response_model=PlanResponse)
async def start_plan_execution(
    plan_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    redis: Redis = Depends(get_redis),
) -> PlanResponse:
    """Mark an APPROVED plan as ``in_progress`` and ensure its tasks exist in the
    Kanban so the team can start implementing them.

    This is the explicit, operator-driven hand-off the lifecycle was missing: the
    plan stays APPROVED (signed off, not running) until someone starts it. The
    transition ``approved -> in_progress`` goes through the state machine (a draft
    or pending-approval plan yields 409); then we materialise every still-missing
    spec task (idempotent — already-synced tasks are skipped). Calling it again on
    an already-running plan is a no-op that just re-ensures the Kanban.
    """
    require_tenant_id(principal)
    plan = await get_writable_or_404(
        session, Plan, plan_id, principal, not_found_detail="plan not found"
    )
    # P1-01: un proyecto pausado/archivado no arranca ejecuciones.
    require_project_active(await _verify_project_visible(session, plan.project_id))
    try:
        transition_plan_status(plan, PlanStatus.IN_PROGRESS.value, actor=principal.user_id)
    except PlanTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "invalid_plan_transition",
                "from": exc.from_status,
                "to": exc.to_status,
                "message": "Solo un plan aprobado puede marcarse en curso.",
            },
        ) from exc
    # PROY2-11: tras validar la transición — un plan vacío no arranca (el
    # reconciler lo rebotaría a pending_human_validation al instante). El raise
    # revierte la transacción, así que la transición de arriba no persiste.
    if not await _plan_has_any_tasks(session, plan):
        raise _plan_has_no_tasks_error()

    # Ensure the tasks are in the Kanban (creates any missing ones; idempotent).
    await sync_plan_to_kanban(session, plan, scope="total")
    # prod-06 task_prod06_dag_02: promote the plan's ROOT tasks (and any whose
    # deps are already done) to `ready` and announce them, so the orchestrator
    # dispatches them. Without this a started plan sat in `backlog` forever —
    # nothing left it without a human moving cards by hand.
    ready_tasks = await promote_ready_tasks(session, plan.id)
    await session.flush()
    await session.refresh(plan)
    await announce_ready_tasks(redis, ready_tasks)
    return to_plan_response(plan)


@plans_router.post(
    "/{plan_id}/generate-corrections", response_model=PlanGenerateCorrectionsResponse
)
async def generate_plan_corrections(
    plan_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> PlanGenerateCorrectionsResponse:
    """Convierte el motivo del rechazo humano en tareas correctivas (ADR 0107).

    Lee el ``rejection_reason`` de la sesión de review RECHAZADA más reciente y
    se lo pasa al LLM del proyecto (mismo kit que generate-acceptance-criteria);
    la tanda normalizada se añade a ``specification.tasks`` (``origin:
    correction``) con su entrada ``proposed`` en ``specification.corrections``.
    NO reactiva el plan: eso es accept-corrections. Idempotente por sesión —
    repetir devuelve la tanda ya propuesta sin regenerar."""
    require_tenant_id(principal)
    plan = await get_writable_or_404(
        session, Plan, plan_id, principal, not_found_detail="plan not found"
    )
    if plan.status != PlanStatus.REJECTED.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "plan_not_rejected",
                "message": (
                    "Solo un plan rechazado puede generar correcciones; "
                    f"este plan está en estado '{plan.status}'."
                ),
                "status": plan.status,
            },
        )

    sessions = await list_review_sessions_for_plan(session, plan.id)
    rejected = next(
        (s for s in sessions if s.verdict == "rejected" and (s.rejection_reason or "").strip()),
        None,
    )
    if rejected is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "no_rejection_reason",
                "message": (
                    "Este plan no tiene ninguna sesión de review rechazada con motivo; "
                    "no hay nada que convertir en tareas correctivas."
                ),
            },
        )
    reason = (rejected.rejection_reason or "").strip()

    spec: dict[str, Any] = plan.specification or {}
    existing_entry = find_correction_for_session(spec, str(rejected.id))
    if existing_entry is not None:
        task_ids = [str(t) for t in existing_entry.get("task_ids") or []]
        by_id = {str(t.get("id")): t for t in spec.get("tasks") or [] if isinstance(t, dict)}
        return PlanGenerateCorrectionsResponse(
            session_id=rejected.id,
            reason=str(existing_entry.get("reason") or reason),
            task_ids=task_ids,
            tasks=[by_id[tid] for tid in task_ids if tid in by_id],
            already_generated=True,
        )

    project = await _verify_project_visible(session, plan.project_id)
    effective = await resolve_chat_model_config(session, project)
    provider, _kind, api_model = await _resolve_chat_provider(session, effective, vault)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No hay proveedor LLM configurado para el chat de este proyecto.",
        )
    existing_tasks = [t for t in spec.get("tasks") or [] if isinstance(t, dict)]
    raw_summary = spec.get("summary")
    try:
        fixes = await generate_corrective_tasks(
            provider,
            rejection_reason=reason,
            plan_title=plan.title,
            plan_summary=raw_summary if isinstance(raw_summary, str) else "",
            existing_tasks=existing_tasks,
            model=api_model,
        )
    finally:
        with contextlib.suppress(Exception):
            await provider.aclose()

    if not fixes:
        # Nada usable: el spec no se toca; la UI ofrece reintentar.
        return PlanGenerateCorrectionsResponse(
            session_id=rejected.id, reason=reason, task_ids=[], tasks=[]
        )

    plan.specification = append_corrections(
        spec,
        session_id=str(rejected.id),
        reason=reason,
        tasks=fixes,
        created_at=datetime.now(tz=UTC).isoformat(),
    )
    await session.flush()
    return PlanGenerateCorrectionsResponse(
        session_id=rejected.id,
        reason=reason,
        task_ids=[str(t["id"]) for t in fixes],
        tasks=fixes,
    )


# ADR 0107: estados desde los que se aceptan correcciones. `rejected` es el
# caso nominal; `in_progress` cubre el reintento idempotente (la primera
# aceptación ya reactivó el plan y la respuesta se perdió por red).
_CORRECTIONS_ACCEPTABLE_STATUSES = frozenset(
    {PlanStatus.REJECTED.value, PlanStatus.IN_PROGRESS.value}
)


@plans_router.post("/{plan_id}/accept-corrections", response_model=PlanResponse)
async def accept_plan_corrections(
    plan_id: UUID,
    payload: PlanAcceptCorrectionsRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    redis: Redis = Depends(get_redis),
) -> PlanResponse:
    """Acepta tareas correctivas de un plan RECHAZADO y lo reactiva (ADR 0107).

    En una única transacción: materializa la selección en el Kanban
    (``sync_plan_to_kanban(scope="selection")``, idempotente), transiciona el
    plan ``rejected -> in_progress`` por la state machine y marca las entradas
    de ``specification.corrections`` como aceptadas. El orden importa: el plan
    nunca es observable ``in_progress`` con todas sus tareas ``done`` — sin las
    tareas nuevas, el reconciler lo rebotaría a ``pending_human_validation`` y
    re-lanzaría una sesión de review. Tras el commit, promoción DAG + announce
    (patrón start-execution).
    """
    require_tenant_id(principal)
    plan = await get_writable_or_404(
        session, Plan, plan_id, principal, not_found_detail="plan not found"
    )
    if plan.status not in _CORRECTIONS_ACCEPTABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": "plan_not_rejected",
                "message": (
                    "Solo un plan rechazado puede aceptar correcciones; "
                    f"este plan está en estado '{plan.status}'."
                ),
                "status": plan.status,
            },
        )

    # PROY2-04: el LLM de correcciones (generate-corrections) puede emitir una
    # tanda cíclica. Validar el DAG del spec RESULTANTE antes de materializar —
    # si no, el plan queda in_progress con tareas que se bloquean entre sí.
    spec_tasks = (plan.specification or {}).get("tasks") or []
    if spec_tasks:
        try:
            validate_dag(spec_tasks)
        except DAGCycleError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"error": "dag_cycle", "cycle": exc.cycle},
            ) from exc

    try:
        await sync_plan_to_kanban(session, plan, scope="selection", task_ids=payload.task_ids)
    except SyncScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"error": "invalid_sync_scope", "message": str(exc)},
        ) from exc

    transition_plan_status(plan, PlanStatus.IN_PROGRESS.value, actor=principal.user_id)
    plan.specification = mark_corrections_accepted(plan.specification or {}, payload.task_ids)

    ready_tasks = await promote_ready_tasks(session, plan.id)
    await session.flush()
    await session.refresh(plan)
    await announce_ready_tasks(redis, ready_tasks)
    return to_plan_response(plan)


@plans_router.post("/{plan_id}/sync-to-kanban", response_model=PlanSyncResponse)
async def sync_plan_kanban(
    plan_id: UUID,
    payload: PlanSyncRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> PlanSyncResponse:
    """Materialise the plan's APPROVED tasks into the Kanban.

    Requires ``plan.status in (approved, in_progress)``: a draft (or a plan still
    awaiting approval) must NOT materialise tasks — that would seed the Kanban with
    work nobody signed off on. Tasks start in ``backlog``; the orchestrator promotes
    dependency-free ones to ``ready``.

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
    _require_syncable_status(plan)

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

    # PROY2-13: no colgar una tarea de un plan CERRADO — quedaría backlog
    # eterna bajo un plan completed/cancelled/archived, invisible al dispatch
    # pero contada por los boards. (rejected se permite: puede reactivarse por
    # la vía de correcciones, ADR 0107.)
    if plan.status in _CLOSED_PLAN_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": "plan_is_closed", "status": plan.status},
        )

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

    # hallazgo #2: añadir una tarea avanzable (backlog, sin deps) a un plan blocked
    # invalida el bloqueo (ya HAY una vía de avance) → re-evaluar. No-op si el plan
    # no está blocked.
    await reactivate_plan_if_unstuck(session, plan.id)

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

# Abort codes that mark a `blocked` task as escalated-to-human (vs. a plain
# block). They are the self-review escalation reasons written on the latest
# execution row (ADR 0087 / safeguards.SafeguardCode). At the TASK level the
# canonical human-escalation state is `blocked` + one of these abort codes —
# there is NO task-level `pending_human_validation` (that is a PLAN status,
# CLAUDE.md ppio 7), so escalation reuses `blocked` + the inbox/panel.
#
# Auditoría 2026-07-02 (F1.1): esta lista se quedó desactualizada — el runtime
# también escala con max_iterations_exceeded / repetitive_loop_detected /
# research_exhausted / self_review_stalemate, y esas tasks quedaban blocked e
# INVISIBLES en el panel (sin acciones humanas). El criterio autoritativo es
# ahora el ESTADO del último run (`needs_human_review` = el runtime pidió
# humano, sea cual sea el abort_code presente o futuro); la lista se conserva
# para filas históricas cuyo run escalado no llevaba ese estado.
_REVIEW_ESCALATION_ABORT_CODES: tuple[str, ...] = (
    "review_inconclusive",
    "max_review_retries_exhausted",
    "agent_reported_failure",
    # A worktree rebase conflict (a sibling task changed the same lines) needs a
    # human to resolve it — surface it on the panel even when the run's status is
    # not needs_human_review (P7, audit 2026-07-03).
    "rebase_conflict",
)
_ESCALATED_EXECUTION_STATUS = "needs_human_review"


@plans_router.get("/{plan_id}/escalated-tasks")
async def list_escalated_tasks(
    plan_id: UUID,
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, list[dict[str, object]]]:
    """Tasks of the plan escalated to a human.

    Two escalation paths converge on this panel:

      * ADR 0020's approval engine parks a task in `awaiting_human_approval`.
      * A runtime escalation: the LATEST execution ended `needs_human_review`
        (self-review exhausted/inconclusive, agent-reported failure,
        max_iterations, repetitive loop, research exhausted, stalemate — any
        current or future abort_code) and the task moved to `blocked`. A plain
        `blocked` (latest run not escalated) is a different kind of block and
        stays OUT of this panel.

    Each entry carries its `status`, the `escalation_reason` (the abort code,
    `None` for the approval path), its `retry_count` and the latest 20 audit
    events so the UI can render the timeline without a round-trip per task.

    Shape:

        {"tasks": [
          {"id": "...", "title": "...", "description": "...",
           "status": "blocked", "escalation_reason": "review_inconclusive",
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

    # Latest execution (by created_at, id) per task — its status/abort_code tell
    # a human-escalation `blocked` apart from any other block.
    ranked = select(
        Execution.task_id.label("task_id"),
        Execution.abort_code.label("abort_code"),
        Execution.status.label("status"),
        func.row_number()
        .over(
            partition_by=Execution.task_id,
            order_by=(Execution.created_at.desc(), Execution.id.desc()),
        )
        .label("rn"),
    ).subquery()
    latest = (
        select(ranked.c.task_id, ranked.c.abort_code, ranked.c.status)
        .where(ranked.c.rn == 1)
        .subquery()
    )

    rows = (
        await session.execute(
            select(Task, latest.c.abort_code)
            .outerjoin(latest, latest.c.task_id == Task.id)
            .where(
                Task.plan_id == plan_id,
                or_(
                    Task.status == "awaiting_human_approval",
                    and_(
                        Task.status == "blocked",
                        or_(
                            # F1.1: el runtime pidió humano — criterio por ESTADO
                            # del último run, robusto a abort_codes nuevos.
                            latest.c.status == _ESCALATED_EXECUTION_STATUS,
                            latest.c.abort_code.in_(_REVIEW_ESCALATION_ABORT_CODES),
                        ),
                    ),
                ),
            )
            .order_by(Task.created_at, Task.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()

    out: list[dict[str, object]] = []
    for task, abort_code in rows:
        events = await _list_history(session, task.id, limit=20)
        out.append(
            {
                "id": str(task.id),
                "title": task.title,
                "description": task.description,
                "status": task.status,
                "escalation_reason": abort_code,
                "retry_count": task.retry_count,
                "history": [_audit_to_dict(e) for e in events],
            }
        )
    return {"tasks": out}


__all__ = ["plans_router", "project_plans_router"]
