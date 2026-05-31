"""Tenant eval QUALITY dashboard — aggregation endpoints (Plan 14 task_14_11).

The read side of the quality-eval substrate: a tenant view of how its agents
score over time, aggregated from :class:`~api_server.db.evals.EvalRun` /
:class:`~api_server.db.evals.EvalResult` (the Fase B roll-ups). Two endpoints,
both tenant-scoped (``get_tenant_session`` → RLS) and gated to ``tenant_admin``
(the dashboard is an admin surface; the frontend mirrors this with
``<RoleGuard min="tenant_admin">``):

  - ``GET /eval-quality/dashboard`` — aggregated quality over a window: headline
    totals, the pass-rate trend (per UTC day) and the per-AGENT /
    per-PROMPT-RELEASE / per-DATASET / per-CRITERION breakdowns. Optional
    ``agent_id`` / ``dataset_id`` / ``prompt_version`` filters narrow every
    aggregate to the matching subset.
  - ``GET /eval-quality/runs`` — the paginated, filterable run history (one row
    per completed/terminal run, newest first) with resolved agent / dataset
    labels.

Dimensions (CLAUDE.md: NEVER a query without tenant scope). An ``EvalRun`` is
DATASET-scoped, not project-scoped — there is no ``project_id`` on a run — so
the plan's "by project" maps to "by dataset" (the per-tenant golden benchmark).
Pass rate is ITEMS-WEIGHTED (``sum(passed_items) / sum(total_items)``), never a
naive mean of per-run rates, so a large run is not outweighed by a tiny one.

Multi-tenancy: every aggregate is computed under the caller's RLS scope with a
defence-in-depth ``tenant_id ==`` predicate, so a tenant only ever sees its own
runs / results. This is a TENANT dashboard — cross-tenant comparison is a
separate, System-Admin-only surface (task_14_15).

Costs are CANONICAL USD (``mean_cost_usd``). The tenant-currency display toggle
the plan mentions depends on the FX / display-currency system (exchange_rates),
which has no numbered task and was not built (Plan 11 scope gap); USD is the
only currency surfaced here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import ColumnElement, column, func, literal, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_admin
from api_server.db.domain import Agent
from api_server.db.evals import (
    EvalCriterion,
    EvalDataset,
    EvalResult,
    EvalRun,
    EvalRunStatus,
)
from api_server.evals.metrics import pass_rate
from api_server.routers._helpers import require_tenant_id
from api_server.routers._pagination import apply_pagination, limit_query, offset_query
from api_server.schemas.eval_quality import (
    AgentQualityBreakdown,
    CriterionQualityBreakdown,
    DatasetQualityBreakdown,
    EvalQualityDashboardResponse,
    EvalRunHistoryItem,
    PromptVersionQualityBreakdown,
    QualityTrendPoint,
)

router = APIRouter(prefix="/eval-quality", tags=["evals"])

# Default + max window (in days) the dashboard aggregates over. Named constants,
# not magic numbers — platform invariants of the dashboard contract (a request
# above the max is a clean 422, not a silent clamp).
DEFAULT_DASHBOARD_WINDOW_DAYS = 90
MAX_DASHBOARD_WINDOW_DAYS = 730

_COMPLETED = EvalRunStatus.COMPLETED.value


# ---------------------------------------------------------------------------
# Query-parameter helper (the int window; the optional filters use inline
# ``fastapi.Query`` which is whitelisted for B008)
# ---------------------------------------------------------------------------
def _window_days() -> int:
    return cast(
        int,
        Query(
            default=DEFAULT_DASHBOARD_WINDOW_DAYS,
            ge=1,
            le=MAX_DASHBOARD_WINDOW_DAYS,
            description=f"Aggregation window in days (1..{MAX_DASHBOARD_WINDOW_DAYS}).",
        ),
    )


# ---------------------------------------------------------------------------
# Shared filter predicate
# ---------------------------------------------------------------------------
def _run_filters(
    *,
    tenant_id: UUID,
    since: datetime | None = None,
    status: str | None = None,
    agent_id: UUID | None = None,
    dataset_id: UUID | None = None,
    prompt_version: str | None = None,
) -> list[ColumnElement[bool]]:
    """The tenant scope + optional filters for an EvalRun aggregate / list.

    The ``tenant_id`` equality is defence in depth on top of RLS — the policy
    already restricts the rows, but pinning it keeps the plan tight and intent
    explicit. ``since`` bounds the dashboard window (compares completion time
    via ``created_at`` fallback when a run never stamped ``finished_at``).
    """
    filters: list[ColumnElement[bool]] = [EvalRun.tenant_id == tenant_id]
    if status is not None:
        filters.append(EvalRun.status == status)
    if since is not None:
        # Window on when the run happened: prefer finished_at, fall back to
        # created_at so a run that never stamped finished_at is still windowed.
        filters.append(func.coalesce(EvalRun.finished_at, EvalRun.created_at) >= since)
    if agent_id is not None:
        filters.append(EvalRun.subject_agent_id == agent_id)
    if dataset_id is not None:
        filters.append(EvalRun.dataset_id == dataset_id)
    if prompt_version is not None:
        filters.append(EvalRun.subject_prompt_version == prompt_version)
    return filters


# ===========================================================================
# GET /eval-quality/dashboard — aggregated quality
# ===========================================================================
@router.get("/dashboard", response_model=EvalQualityDashboardResponse)
async def eval_quality_dashboard(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    window_days: int = _window_days(),
    agent_id: UUID | None = Query(default=None, description="Narrow to one subject agent."),
    dataset_id: UUID | None = Query(default=None, description="Narrow to one golden dataset."),
    prompt_version: str | None = Query(
        default=None, max_length=64, description="Narrow to one prompt release."
    ),
) -> EvalQualityDashboardResponse:
    """Aggregated eval quality for this tenant over the last N days. tenant_admin.

    Over COMPLETED runs only (a pending/failed run has no trustworthy roll-up):
    headline totals, the per-UTC-day pass-rate trend, and the per-agent /
    per-prompt-version / per-dataset / per-criterion breakdowns. Optional
    ``agent_id`` / ``dataset_id`` / ``prompt_version`` filters narrow every
    aggregate. All tenant-scoped (RLS); costs in canonical USD.
    """
    tenant_id = require_tenant_id(principal)
    since = datetime.now(tz=UTC) - timedelta(days=window_days)
    base = _run_filters(
        tenant_id=tenant_id,
        since=since,
        status=_COMPLETED,
        agent_id=agent_id,
        dataset_id=dataset_id,
        prompt_version=prompt_version,
    )

    totals = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(EvalRun.total_items), 0),
                func.coalesce(func.sum(EvalRun.passed_items), 0),
            ).where(*base)
        )
    ).one()
    total_runs, total_items, passed_items = int(totals[0]), int(totals[1]), int(totals[2])

    by_agent = await _by_agent(session, base)
    by_prompt = await _by_prompt_version(session, base)
    by_dataset = await _by_dataset(session, base)
    by_criterion = await _by_criterion(session, tenant_id=tenant_id, base=base)
    trend = await _trend(session, base)

    return EvalQualityDashboardResponse(
        window_days=window_days,
        total_runs=total_runs,
        total_items=total_items,
        passed_items=passed_items,
        overall_pass_rate=pass_rate(passed_items, total_items),
        by_agent=by_agent,
        by_prompt_version=by_prompt,
        by_dataset=by_dataset,
        by_criterion=by_criterion,
        trend=trend,
    )


# ===========================================================================
# GET /eval-quality/runs — paginated, filterable run history
# ===========================================================================
@router.get("/runs", response_model=list[EvalRunHistoryItem])
async def list_eval_quality_runs(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    limit: int = limit_query(),
    offset: int = offset_query(),
    status_filter: str | None = Query(
        default=None, max_length=16, description="Filter run history by status."
    ),
    agent_id: UUID | None = Query(default=None, description="Narrow to one subject agent."),
    dataset_id: UUID | None = Query(default=None, description="Narrow to one golden dataset."),
    prompt_version: str | None = Query(
        default=None, max_length=64, description="Narrow to one prompt release."
    ),
) -> list[EvalRunHistoryItem]:
    """List this tenant's eval runs, newest first, paginated + filtered. tenant_admin.

    The run history behind the dashboard: one row per run with resolved agent /
    dataset labels. Filterable by ``status`` / ``agent_id`` / ``dataset_id`` /
    ``prompt_version``. Tenant-scoped (RLS) — never another tenant's run. Costs
    in canonical USD.
    """
    tenant_id = require_tenant_id(principal)
    filters = _run_filters(
        tenant_id=tenant_id,
        status=status_filter,
        agent_id=agent_id,
        dataset_id=dataset_id,
        prompt_version=prompt_version,
    )
    stmt = (
        select(
            EvalRun,
            EvalDataset.name,
            Agent.name,
            Agent.role,
        )
        .outerjoin(EvalDataset, EvalDataset.id == EvalRun.dataset_id)
        .outerjoin(Agent, Agent.id == EvalRun.subject_agent_id)
        .where(*filters)
        .order_by(
            func.coalesce(EvalRun.finished_at, EvalRun.created_at).desc(),
            EvalRun.id.desc(),
        )
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    rows = (await session.execute(stmt)).all()
    return [
        EvalRunHistoryItem(
            id=run.id,
            dataset_id=run.dataset_id,
            dataset_name=dataset_name,
            status=run.status,
            subject_agent_id=run.subject_agent_id,
            agent_name=agent_name,
            agent_role=agent_role,
            subject_prompt_version=run.subject_prompt_version,
            judge_model=run.judge_model,
            started_at=run.started_at,
            finished_at=run.finished_at,
            total_items=run.total_items,
            passed_items=run.passed_items,
            pass_rate=run.pass_rate,
            mean_latency_ms=run.mean_latency_ms,
            mean_tokens=run.mean_tokens,
            mean_cost_usd=run.mean_cost_usd,
            created_at=run.created_at,
        )
        for run, dataset_name, agent_name, agent_role in rows
    ]


# ---------------------------------------------------------------------------
# Aggregation internals (pure SQL over EvalRun / EvalResult, tenant-scoped)
# ---------------------------------------------------------------------------
async def _by_agent(
    session: AsyncSession, base: list[ColumnElement[bool]]
) -> list[AgentQualityBreakdown]:
    """Per-subject-agent roll-up, items-weighted, with resolved agent labels."""
    rows = (
        await session.execute(
            select(
                EvalRun.subject_agent_id,
                Agent.name,
                Agent.role,
                func.count(),
                func.coalesce(func.sum(EvalRun.total_items), 0),
                func.coalesce(func.sum(EvalRun.passed_items), 0),
                func.avg(EvalRun.mean_cost_usd),
                func.avg(EvalRun.mean_tokens),
            )
            .outerjoin(Agent, Agent.id == EvalRun.subject_agent_id)
            .where(*base)
            .group_by(EvalRun.subject_agent_id, Agent.name, Agent.role)
            .order_by(func.count().desc(), Agent.name)
        )
    ).all()
    return [
        AgentQualityBreakdown(
            subject_agent_id=agent_id,
            agent_name=name,
            agent_role=role,
            run_count=int(run_count),
            total_items=int(total),
            passed_items=int(passed),
            pass_rate=pass_rate(int(passed), int(total)),
            mean_cost_usd=_quantize_cost(mean_cost),
            mean_tokens=_quantize_tokens(mean_tokens),
        )
        for agent_id, name, role, run_count, total, passed, mean_cost, mean_tokens in rows
    ]


async def _by_prompt_version(
    session: AsyncSession, base: list[ColumnElement[bool]]
) -> list[PromptVersionQualityBreakdown]:
    """Per-prompt-release roll-up (``subject_prompt_version``), items-weighted."""
    rows = (
        await session.execute(
            select(
                EvalRun.subject_prompt_version,
                func.count(),
                func.coalesce(func.sum(EvalRun.total_items), 0),
                func.coalesce(func.sum(EvalRun.passed_items), 0),
                func.avg(EvalRun.mean_cost_usd),
            )
            .where(*base)
            .group_by(EvalRun.subject_prompt_version)
            .order_by(func.count().desc(), EvalRun.subject_prompt_version)
        )
    ).all()
    return [
        PromptVersionQualityBreakdown(
            subject_prompt_version=version,
            run_count=int(run_count),
            total_items=int(total),
            passed_items=int(passed),
            pass_rate=pass_rate(int(passed), int(total)),
            mean_cost_usd=_quantize_cost(mean_cost),
        )
        for version, run_count, total, passed, mean_cost in rows
    ]


async def _by_dataset(
    session: AsyncSession, base: list[ColumnElement[bool]]
) -> list[DatasetQualityBreakdown]:
    """Per-dataset roll-up (the "by project / by benchmark" dimension)."""
    rows = (
        await session.execute(
            select(
                EvalRun.dataset_id,
                EvalDataset.name,
                func.count(),
                func.coalesce(func.sum(EvalRun.total_items), 0),
                func.coalesce(func.sum(EvalRun.passed_items), 0),
            )
            .outerjoin(EvalDataset, EvalDataset.id == EvalRun.dataset_id)
            .where(*base)
            .group_by(EvalRun.dataset_id, EvalDataset.name)
            .order_by(func.count().desc(), EvalDataset.name)
        )
    ).all()
    return [
        DatasetQualityBreakdown(
            dataset_id=dataset_id,
            dataset_name=name,
            run_count=int(run_count),
            total_items=int(total),
            passed_items=int(passed),
            pass_rate=pass_rate(int(passed), int(total)),
        )
        for dataset_id, name, run_count, total, passed in rows
    ]


async def _by_criterion(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    base: list[ColumnElement[bool]],
) -> list[CriterionQualityBreakdown]:
    """Per-criterion pass rate over the ``criterion_scores`` JSONB of results.

    Each :class:`EvalResult` row carries a JSONB array of
    ``{criterion_id, score, passed, rationale}``; we unnest it server-side and
    count passes per criterion. The result set is scoped to the runs matching
    ``base`` (the window + filters) via a correlated EXISTS on ``eval_runs`` so
    a criterion is only counted for the dashboard's run subset. ``criterion_id``
    is resolved against ``eval_criteria`` for a display name when it still
    exists. Tenant-scoped (RLS) + explicit ``tenant_id`` predicate.
    """
    # Unnest the per-result JSONB array of {criterion_id, score, passed, ...} as
    # a lateral table-valued function. The "value" column is typed JSONB so the
    # ``[...]`` json-path operators are available; ``ON true`` is the lateral
    # cross join (a set-returning function in FROM is correlated by position).
    elem = func.jsonb_array_elements(EvalResult.criterion_scores).table_valued(
        column("value", JSONB), name="cs"
    )
    crit_id = elem.c.value["criterion_id"].astext
    passed_flag = elem.c.value["passed"].astext

    # Only results whose run is in the dashboard subset (window + filters).
    run_subset = select(EvalRun.id).where(*base).scalar_subquery()

    rows = (
        await session.execute(
            select(
                crit_id,
                func.count(),
                func.count().filter(passed_flag == "true"),
            )
            .select_from(EvalResult)
            .join(elem, literal(True))
            .where(
                EvalResult.tenant_id == tenant_id,
                EvalResult.run_id.in_(run_subset),
            )
            .group_by(crit_id)
            .order_by(func.count().desc(), crit_id)
        )
    ).all()

    criterion_ids = [_safe_uuid(cid) for cid, _scored, _passed in rows]
    names = await _criterion_names(
        session, tenant_id=tenant_id, ids=[c for c in criterion_ids if c is not None]
    )
    out: list[CriterionQualityBreakdown] = []
    for (_cid_text, scored, passed), cid in zip(rows, criterion_ids, strict=True):
        out.append(
            CriterionQualityBreakdown(
                criterion_id=cid,
                criterion_name=names.get(cid) if cid is not None else None,
                scored=int(scored),
                passed=int(passed),
                pass_rate=pass_rate(int(passed), int(scored)),
            )
        )
    return out


async def _criterion_names(
    session: AsyncSession, *, tenant_id: UUID, ids: list[UUID]
) -> dict[UUID, str]:
    """Resolve criterion ids -> name (tenant-scoped). Empty for no ids."""
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(EvalCriterion.id, EvalCriterion.name).where(
                EvalCriterion.tenant_id == tenant_id,
                EvalCriterion.id.in_(ids),
            )
        )
    ).all()
    return {row[0]: row[1] for row in rows}


async def _trend(session: AsyncSession, base: list[ColumnElement[bool]]) -> list[QualityTrendPoint]:
    """Per-UTC-day items-weighted pass-rate trend over the windowed runs."""
    day_expr = func.coalesce(EvalRun.finished_at, EvalRun.created_at)
    day_col = func.to_char(func.date_trunc("day", day_expr), "YYYY-MM-DD")
    rows = (
        await session.execute(
            select(
                day_col,
                func.count(),
                func.coalesce(func.sum(EvalRun.total_items), 0),
                func.coalesce(func.sum(EvalRun.passed_items), 0),
            )
            .where(*base)
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()
    return [
        QualityTrendPoint(
            day=day,
            run_count=int(run_count),
            total_items=int(total),
            passed_items=int(passed),
            pass_rate=pass_rate(int(passed), int(total)),
        )
        for day, run_count, total, passed in rows
    ]


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------
def _quantize_cost(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.000001"))


def _quantize_tokens(value: object) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"))


def _safe_uuid(value: object) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (ValueError, AttributeError, TypeError):
        return None


__all__ = ["router"]
