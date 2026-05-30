"""Tenant STATISTICS dashboard + consumption summary + runs explorer (task_14_12).

The read side of the tenant's operational statistics: how its agents perform
(success rate, mean time, mean cost), what it consumed (cost + tokens) and a
filterable per-execution explorer. Everything aggregates the
:class:`~api_server.db.domain.Execution` table (one row per run of the agent
loop against a task) joined to its task / plan / agent — NOT the eval-quality
``EvalRun`` roll-ups (those back task_14_11). The denormalised per-execution
usage columns (``total_tokens`` / ``total_cost_usd``) and the Plan 11 per-call
price snapshot in ``steps_log`` are the canonical sources.

Three endpoints, all tenant-scoped (``get_tenant_session`` → RLS) and gated to
``tenant_admin`` (the dashboard is an admin surface; the frontend mirrors this
with ``<RoleGuard min="tenant_admin">``):

  - ``GET /tenant-stats/dashboard`` — agent statistics over a window: headline
    totals, the per-AGENT breakdown (success rate / mean duration / mean cost),
    the TOP / BOTTOM agents by success rate and the per-UTC-day temporal trend.
  - ``GET /tenant-stats/consumption`` — the consumption summary: accumulated
    cost, total tokens (input / output / cached), run count, mean cost and the
    single costliest run.
  - ``GET /tenant-stats/runs`` — the paginated, filterable runs explorer (one
    row per execution, newest first) with resolved plan / task / agent labels,
    the model of the run's last model call and the owning task's retry count.

Multi-tenancy (CLAUDE.md: NEVER a query without tenant scope). Every aggregate
is computed under the caller's RLS scope with a defence-in-depth
``tenant_id ==`` predicate, so a tenant only ever sees its own executions. This
is a TENANT dashboard — cross-tenant comparison is a separate, System-Admin-only
surface (task_14_15).

Costs are CANONICAL USD. The tenant-currency display toggle the plan mentions
depends on the FX / display-currency system (exchange_rates), which has no
numbered task and was not built (Plan 11 scope gap); USD is the only currency
surfaced here. Cached-token counts are not captured per step yet (the price
snapshot freezes the cached *price*, not the count) so cached tokens report 0 —
never fabricated.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import (
    BigInteger,
    ColumnElement,
    case,
    column,
    func,
    literal,
    select,
)
from sqlalchemy import (
    cast as sa_cast,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_admin
from api_server.db.domain import Agent, Execution, Plan, Task
from api_server.routers._helpers import require_tenant_id
from api_server.routers._pagination import apply_pagination, limit_query, offset_query
from api_server.schemas.tenant_stats import (
    AgentStatsBreakdown,
    ConsumptionSummaryResponse,
    CostliestRun,
    ExecutionRunRow,
    StatsTrendPoint,
    TenantStatsDashboardResponse,
)

router = APIRouter(prefix="/tenant-stats", tags=["stats"])

# Default + max window (in days) the dashboard aggregates over. Named constants,
# not magic numbers — platform invariants of the dashboard contract (a request
# above the max is a clean 422, not a silent clamp).
DEFAULT_STATS_WINDOW_DAYS = 90
MAX_STATS_WINDOW_DAYS = 730

# How many agents the dashboard returns in each of the top / bottom lists.
TOP_BOTTOM_LIMIT = 5

# The terminal execution status that counts as a SUCCESS (mirrors the agent
# runtime's ``state.STATUS_DONE`` — kept as a literal so this router stays
# import-light and does not depend on the runtime package).
_DONE = "done"

# Cost / rate quantisation scales (mirror the Numeric columns they roll up).
_COST_QUANTUM = Decimal("0.000001")  # executions.total_cost_usd is Numeric(14, 6)
_RATE_QUANTUM = Decimal("0.001")
_MS_QUANTUM = Decimal("0.01")


# ---------------------------------------------------------------------------
# Query-parameter helper (the int window; the optional filters use inline
# ``fastapi.Query`` which is whitelisted for B008)
# ---------------------------------------------------------------------------
def _window_days() -> int:
    return cast(
        int,
        Query(
            default=DEFAULT_STATS_WINDOW_DAYS,
            ge=1,
            le=MAX_STATS_WINDOW_DAYS,
            description=f"Aggregation window in days (1..{MAX_STATS_WINDOW_DAYS}).",
        ),
    )


# ---------------------------------------------------------------------------
# Reusable SQL expressions over the executions table
# ---------------------------------------------------------------------------
def _duration_ms() -> ColumnElement[float]:
    """``completed_at - started_at`` in milliseconds (NULL when not finished).

    NULL when either endpoint is missing so an unfinished run is *skipped*
    by the mean, not counted as a zero-duration run.
    """
    delta = func.extract("epoch", Execution.completed_at - Execution.started_at) * 1000.0
    return cast(
        "ColumnElement[float]",
        case((Execution.completed_at.isnot(None) & Execution.started_at.isnot(None), delta)),
    )


def _succeeded_flag() -> ColumnElement[int]:
    """1 when the execution ended ``done`` else 0 — summable into a success count."""
    return cast(
        "ColumnElement[int]",
        case((Execution.status == _DONE, 1), else_=0),
    )


def _last_model_expr() -> ColumnElement[str | None]:
    """The model of the run's LAST ``model_call`` step (NULL when none).

    ``steps_log`` is a JSONB array of step dicts; the model-call steps carry a
    ``model`` field and a monotonically increasing ``index`` (see
    ``agent_runtime.steps``). We unnest, keep only ``model_call`` steps that
    name a model, and pick the highest-``index`` one — a correlated scalar
    subquery so it composes into a SELECT / WHERE without a GROUP BY. Ordering
    by the step's own ``index`` (not SQL ordinality) keeps the pick
    deterministic and portable.
    """
    elem = func.jsonb_array_elements(Execution.steps_log).table_valued(
        column("value", JSONB), name="sl"
    )
    model_txt = elem.c.value["model"].astext
    index_int = sa_cast(elem.c.value["index"].astext, BigInteger)
    return (
        select(model_txt)
        .select_from(elem)
        .where(elem.c.value["kind"].astext == "model_call", model_txt.isnot(None))
        .order_by(index_int.desc())
        .limit(1)
        .scalar_subquery()
    )


# ---------------------------------------------------------------------------
# Shared filter predicate
# ---------------------------------------------------------------------------
def _exec_filters(
    *,
    tenant_id: UUID,
    since: datetime | None = None,
    agent_id: UUID | None = None,
    role: str | None = None,
    plan_id: UUID | None = None,
    task_id: UUID | None = None,
    verdict: str | None = None,
    model: str | None = None,
    min_cost: Decimal | None = None,
) -> list[ColumnElement[bool]]:
    """The tenant scope + optional filters for an Execution aggregate / list.

    The ``tenant_id`` equality is defence in depth on top of RLS. ``since``
    bounds the window on ``created_at`` (when the run started being recorded).
    ``role`` / ``model`` join through the agent / steps_log respectively.
    """
    filters: list[ColumnElement[bool]] = [Execution.tenant_id == tenant_id]
    if since is not None:
        filters.append(Execution.created_at >= since)
    if agent_id is not None:
        filters.append(Execution.agent_id == agent_id)
    if role is not None:
        filters.append(Agent.role == role)
    if plan_id is not None:
        filters.append(Task.plan_id == plan_id)
    if task_id is not None:
        filters.append(Execution.task_id == task_id)
    if verdict is not None:
        filters.append(Execution.status == verdict)
    if min_cost is not None:
        filters.append(Execution.total_cost_usd >= min_cost)
    if model is not None:
        filters.append(_last_model_expr() == model)
    return filters


# ===========================================================================
# GET /tenant-stats/dashboard — agent statistics
# ===========================================================================
@router.get("/dashboard", response_model=TenantStatsDashboardResponse)
async def tenant_stats_dashboard(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    window_days: int = _window_days(),
    agent_id: UUID | None = Query(default=None, description="Narrow to one agent."),
    role: str | None = Query(default=None, max_length=32, description="Narrow to one agent role."),
    plan_id: UUID | None = Query(default=None, description="Narrow to one plan."),
) -> TenantStatsDashboardResponse:
    """Agent statistics for this tenant over the last N days. tenant_admin.

    Headline success rate / mean duration / mean cost, the per-agent breakdown,
    the TOP and BOTTOM agents by success rate and the per-UTC-day temporal
    trend. All tenant-scoped (RLS); costs in canonical USD.
    """
    tenant_id = require_tenant_id(principal)
    since = datetime.now(tz=UTC) - timedelta(days=window_days)
    base = _exec_filters(
        tenant_id=tenant_id, since=since, agent_id=agent_id, role=role, plan_id=plan_id
    )

    dur = _duration_ms()
    success = _succeeded_flag()
    totals = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(success), 0),
                func.avg(dur),
                func.avg(Execution.total_cost_usd),
                func.coalesce(func.sum(Execution.total_cost_usd), 0),
            )
            .select_from(Execution)
            .outerjoin(Agent, Agent.id == Execution.agent_id)
            .outerjoin(Task, Task.id == Execution.task_id)
            .where(*base)
        )
    ).one()
    total_runs = int(totals[0])
    succeeded = int(totals[1])

    by_agent = await _by_agent(session, base)
    trend = await _trend(session, base)

    # TOP / BOTTOM by success rate over agents that ran at least once, ranked
    # high→low. With fewer than 2*limit agents the lists may overlap — that is
    # intentional (a small tenant sees the same handful both ways).
    ranked = [a for a in by_agent if a.run_count > 0 and a.success_rate is not None]
    ranked.sort(key=lambda a: (a.success_rate or Decimal(0), a.run_count), reverse=True)
    top_agents = ranked[:TOP_BOTTOM_LIMIT]
    bottom_agents = list(reversed(ranked[-TOP_BOTTOM_LIMIT:]))

    return TenantStatsDashboardResponse(
        window_days=window_days,
        total_runs=total_runs,
        succeeded_runs=succeeded,
        overall_success_rate=_rate(succeeded, total_runs),
        mean_duration_ms=_quantize(totals[2], _MS_QUANTUM),
        mean_cost_usd=_quantize(totals[3], _COST_QUANTUM),
        total_cost_usd=_quantize_cost(totals[4]),
        by_agent=by_agent,
        top_agents=top_agents,
        bottom_agents=bottom_agents,
        trend=trend,
    )


# ===========================================================================
# GET /tenant-stats/consumption — consumption summary
# ===========================================================================
@router.get("/consumption", response_model=ConsumptionSummaryResponse)
async def tenant_consumption_summary(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    window_days: int = _window_days(),
    agent_id: UUID | None = Query(default=None, description="Narrow to one agent."),
    plan_id: UUID | None = Query(default=None, description="Narrow to one plan."),
) -> ConsumptionSummaryResponse:
    """Consumption summary for this tenant over the last N days. tenant_admin.

    Accumulated cost, total tokens (input / output / cached), run count, mean
    cost per run and the single costliest run. Tenant-scoped (RLS); canonical
    USD. ``total_tokens_cached`` is 0 (cached counts not captured per step yet).
    """
    tenant_id = require_tenant_id(principal)
    since = datetime.now(tz=UTC) - timedelta(days=window_days)
    base = _exec_filters(tenant_id=tenant_id, since=since, agent_id=agent_id, plan_id=plan_id)

    headline = (
        await session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(Execution.total_cost_usd), 0),
                func.coalesce(func.sum(Execution.total_tokens), 0),
                func.avg(Execution.total_cost_usd),
            )
            .select_from(Execution)
            .outerjoin(Task, Task.id == Execution.task_id)
            .where(*base)
        )
    ).one()
    run_count = int(headline[0])
    accumulated = _quantize_cost(headline[1])
    total_tokens = int(headline[2])

    tin, tout = await _token_split(session, base)

    costliest = await _costliest_run(session, base)

    return ConsumptionSummaryResponse(
        window_days=window_days,
        run_count=run_count,
        accumulated_cost_usd=accumulated,
        mean_cost_usd=_quantize(headline[3], _COST_QUANTUM),
        total_tokens=total_tokens,
        total_tokens_input=tin,
        total_tokens_output=tout,
        # Cached token COUNTS are not recorded per step (the Plan 11 snapshot
        # freezes the cached PRICE, not the count). Reported as 0, not faked.
        total_tokens_cached=0,
        costliest_run=costliest,
    )


# ===========================================================================
# GET /tenant-stats/runs — paginated, filterable runs explorer
# ===========================================================================
@router.get("/runs", response_model=list[ExecutionRunRow])
async def list_execution_runs(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    limit: int = limit_query(),
    offset: int = offset_query(),
    window_days: int = _window_days(),
    agent_id: UUID | None = Query(default=None, description="Narrow to one agent."),
    role: str | None = Query(default=None, max_length=32, description="Narrow to one agent role."),
    plan_id: UUID | None = Query(default=None, description="Narrow to one plan."),
    task_id: UUID | None = Query(default=None, description="Narrow to one task."),
    verdict: str | None = Query(
        default=None, max_length=32, description="Narrow to one execution verdict/status."
    ),
    model: str | None = Query(default=None, max_length=120, description="Narrow to one model."),
    min_cost: Decimal | None = Query(
        default=None, ge=0, description="Minimum total cost USD threshold."
    ),
) -> list[ExecutionRunRow]:
    """List this tenant's executions, newest first, paginated + filtered. tenant_admin.

    One row per execution with resolved plan / task / agent labels, the model of
    the run's last model call, the verdict (terminal status), the owning task's
    retry count and the duration. Filterable by time window / agent / role /
    plan / task / verdict / model / min-cost. Tenant-scoped (RLS); canonical USD.
    """
    tenant_id = require_tenant_id(principal)
    since = datetime.now(tz=UTC) - timedelta(days=window_days)
    filters = _exec_filters(
        tenant_id=tenant_id,
        since=since,
        agent_id=agent_id,
        role=role,
        plan_id=plan_id,
        task_id=task_id,
        verdict=verdict,
        model=model,
        min_cost=min_cost,
    )
    dur = _duration_ms()
    model_expr = _last_model_expr()
    stmt = (
        select(
            Execution,
            Task.title,
            Task.plan_id,
            Task.retry_count,
            Plan.title,
            Agent.name,
            Agent.role,
            model_expr,
            dur,
        )
        .select_from(Execution)
        .outerjoin(Task, Task.id == Execution.task_id)
        .outerjoin(Plan, Plan.id == Task.plan_id)
        .outerjoin(Agent, Agent.id == Execution.agent_id)
        .where(*filters)
        .order_by(Execution.created_at.desc(), Execution.id.desc())
    )
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    rows = (await session.execute(stmt)).all()
    return [
        ExecutionRunRow(
            id=ex.id,
            created_at=ex.created_at,
            task_id=ex.task_id,
            task_title=task_title,
            plan_id=plan_id,
            plan_title=plan_title,
            agent_id=ex.agent_id,
            agent_name=agent_name,
            agent_role=agent_role,
            model=model_name,
            verdict=ex.status,
            succeeded=ex.status == _DONE,
            retry_count=int(retry_count) if retry_count is not None else 0,
            duration_ms=int(duration) if duration is not None else None,
            total_tokens=ex.total_tokens,
            total_cost_usd=ex.total_cost_usd,
            started_at=ex.started_at,
            completed_at=ex.completed_at,
        )
        for (
            ex,
            task_title,
            plan_id,
            retry_count,
            plan_title,
            agent_name,
            agent_role,
            model_name,
            duration,
        ) in rows
    ]


# ---------------------------------------------------------------------------
# Aggregation internals (pure SQL over executions, tenant-scoped)
# ---------------------------------------------------------------------------
async def _by_agent(
    session: AsyncSession, base: list[ColumnElement[bool]]
) -> list[AgentStatsBreakdown]:
    """Per-agent roll-up (success rate, mean duration, mean cost) with labels."""
    dur = _duration_ms()
    success = _succeeded_flag()
    rows = (
        await session.execute(
            select(
                Execution.agent_id,
                Agent.name,
                Agent.role,
                func.count(),
                func.coalesce(func.sum(success), 0),
                func.avg(dur),
                func.avg(Execution.total_cost_usd),
                func.coalesce(func.sum(Execution.total_cost_usd), 0),
                func.coalesce(func.sum(Execution.total_tokens), 0),
            )
            .select_from(Execution)
            .outerjoin(Agent, Agent.id == Execution.agent_id)
            .outerjoin(Task, Task.id == Execution.task_id)
            .where(*base)
            .group_by(Execution.agent_id, Agent.name, Agent.role)
            .order_by(func.count().desc(), Agent.name)
        )
    ).all()
    return [
        AgentStatsBreakdown(
            agent_id=agent_id,
            agent_name=name,
            agent_role=agent_role,
            run_count=int(run_count),
            succeeded=int(succeeded),
            success_rate=_rate(int(succeeded), int(run_count)),
            mean_duration_ms=_quantize(mean_dur, _MS_QUANTUM),
            mean_cost_usd=_quantize(mean_cost, _COST_QUANTUM),
            total_cost_usd=_quantize_cost(total_cost),
            total_tokens=int(total_tokens),
        )
        for (
            agent_id,
            name,
            agent_role,
            run_count,
            succeeded,
            mean_dur,
            mean_cost,
            total_cost,
            total_tokens,
        ) in rows
    ]


async def _trend(session: AsyncSession, base: list[ColumnElement[bool]]) -> list[StatsTrendPoint]:
    """Per-UTC-day trend: run count, success rate and accumulated cost."""
    success = _succeeded_flag()
    day_col = func.to_char(func.date_trunc("day", Execution.created_at), "YYYY-MM-DD")
    rows = (
        await session.execute(
            select(
                day_col,
                func.count(),
                func.coalesce(func.sum(success), 0),
                func.coalesce(func.sum(Execution.total_cost_usd), 0),
            )
            .select_from(Execution)
            .outerjoin(Agent, Agent.id == Execution.agent_id)
            .outerjoin(Task, Task.id == Execution.task_id)
            .where(*base)
            .group_by(day_col)
            .order_by(day_col)
        )
    ).all()
    return [
        StatsTrendPoint(
            day=day,
            run_count=int(run_count),
            succeeded=int(succeeded),
            success_rate=_rate(int(succeeded), int(run_count)),
            total_cost_usd=_quantize_cost(total_cost),
        )
        for day, run_count, succeeded, total_cost in rows
    ]


async def _token_split(session: AsyncSession, base: list[ColumnElement[bool]]) -> tuple[int, int]:
    """(sum tokens_in, sum tokens_out) over the windowed runs' model calls."""
    elem = func.jsonb_array_elements(Execution.steps_log).table_valued(
        column("value", JSONB), name="sl"
    )
    is_call = elem.c.value["kind"].astext == "model_call"
    row = (
        await session.execute(
            select(
                func.coalesce(
                    func.sum(sa_cast(elem.c.value["tokens_in"].astext, BigInteger)).filter(is_call),
                    0,
                ),
                func.coalesce(
                    func.sum(sa_cast(elem.c.value["tokens_out"].astext, BigInteger)).filter(
                        is_call
                    ),
                    0,
                ),
            )
            .select_from(Execution)
            .outerjoin(Agent, Agent.id == Execution.agent_id)
            .outerjoin(Task, Task.id == Execution.task_id)
            .join(elem, literal(True))
            .where(*base)
        )
    ).one()
    return int(row[0]), int(row[1])


async def _costliest_run(
    session: AsyncSession, base: list[ColumnElement[bool]]
) -> CostliestRun | None:
    """The single most expensive execution in the window (or None)."""
    row = (
        await session.execute(
            select(
                Execution.id,
                Execution.task_id,
                Task.title,
                Agent.name,
                Execution.total_cost_usd,
                Execution.total_tokens,
                Execution.created_at,
            )
            .select_from(Execution)
            .outerjoin(Task, Task.id == Execution.task_id)
            .outerjoin(Agent, Agent.id == Execution.agent_id)
            .where(*base)
            .order_by(Execution.total_cost_usd.desc(), Execution.created_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    return CostliestRun(
        execution_id=row[0],
        task_id=row[1],
        task_title=row[2],
        agent_name=row[3],
        total_cost_usd=row[4],
        total_tokens=int(row[5]),
        created_at=row[6],
    )


# ---------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------
def _quantize(value: object, quantum: Decimal) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)


def _quantize_cost(value: object) -> Decimal:
    """Like :func:`_quantize` for a NON-NULL coalesced cost SUM.

    The SUM columns use ``coalesce(..., 0)`` so they are never NULL; this
    always returns the 6-decimal canonical-USD :class:`Decimal` (``0.000000``
    for an empty window — note ``Decimal("0.000000")`` is falsy, so callers must
    NOT guard it with ``or``).
    """
    return Decimal(str(value)).quantize(_COST_QUANTUM, rounding=ROUND_HALF_UP)


def _rate(succeeded: int, total: int) -> Decimal | None:
    """Success fraction in [0, 1]; None when no runs (not 0 — undefined)."""
    if total <= 0:
        return None
    return (Decimal(succeeded) / Decimal(total)).quantize(_RATE_QUANTUM, rounding=ROUND_HALF_UP)


__all__ = ["router"]
