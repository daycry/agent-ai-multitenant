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

Costs are CANONICAL USD. The runs explorer ALSO drives the tenant-currency
DISPLAY toggle (Plan 11.1 task_11_1_03): when the caller's display currency is
not USD (the tenant's ``Organization.display_currency``, overridable per request
with the ``display_currency`` query param), each row additionally carries its
USD cost converted with the FX rate of that run's OWN date plus the applied rate
for traceability (``api_server.fx`` / the global ``exchange_rates`` catalog). The
stored USD is never changed — conversion is presentation only, computed on the
fly per row. Cached-token counts are not captured per step yet (the price
snapshot freezes the cached *price*, not the count) so cached tokens report 0 —
never fabricated.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import (
    ColumnElement,
    Select,
    case,
    func,
    literal,
    select,
    tuple_,
)
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_admin
from api_server.budgets.human_cost import compute_human_cost_usd
from api_server.db.domain import Agent, Execution, Plan, Task
from api_server.db.exchange_rates import CANONICAL_CURRENCY
from api_server.db.models import Organization
from api_server.fx import convert_from_usd_with_rate, resolve_rates_for_dates
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
from api_server.stats.export import (
    ExportFormat,
    build_runs_export,
    filename_for,
    media_type_for,
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
    """El modelo del ÚLTIMO ``model_call`` del run (NULL si no llamó a ninguno).

    Desde la migración **0139** (prod-13 task_prod13_18) esto es una COLUMNA y
    no una subconsulta. Lo que había antes: desenrollar el ``steps_log`` de cada
    fila con ``jsonb_array_elements``, quedarse con los pasos ``model_call`` que
    nombran modelo y escoger el de mayor ``index`` — una subconsulta
    correlacionada que se ejecutaba por fila del listado y, cuando se usaba como
    PREDICADO (``?model=``), no le dejaba al planificador nada que indexar.

    La columna la escribe `db/execution_repo.py::apply_steps_rollup`, con la
    misma definición (incluido el NULL, que significa «ningún modelo», no
    «desconocido»), y el backfill de la 0139 la rellenó para todo el histórico.

    Esa función es PÚBLICA a propósito: hay dos escritores de `steps_log` —el
    repositorio y `workers/execution.py::_mark_commit_failed`—, así que la
    proyección no puede depender de que todo el mundo pase por un helper
    privado de un módulo. Quien asigne `steps_log` la llama.

    Se conserva la FUNCIÓN, y no se sustituye por la columna en los tres
    llamantes, porque es donde vive esta explicación y el punto único por el que
    volver atrás si la denormalización resultara equivocada.
    """
    return cast("ColumnElement[str | None]", Execution.last_model)


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
    return await _compute_consumption(
        session,
        base,
        window_days=window_days,
        tenant_id=tenant_id,
        since=since,
        plan_id=plan_id,
    )


async def _compute_consumption(
    session: AsyncSession,
    base: list[ColumnElement[bool]],
    *,
    window_days: int,
    tenant_id: UUID,
    since: datetime,
    plan_id: UUID | None = None,
) -> ConsumptionSummaryResponse:
    """Compute the consumption summary for ``base`` (tenant-scoped filters).

    Shared by the JSON ``/consumption`` endpoint and the export's PDF/HTML
    summary block so the two never drift. ``accumulated_cost_usd`` is the AI
    cost (the executions roll-up, unchanged); the human cost (Plan 16
    task_16_12: rate * hours from human_work_sessions) is computed over the SAME
    window so the card / export can SEGMENT AI vs human and show the combined
    total. The human roll-up is tenant-scoped (RLS) over the window and narrowed
    to ``plan_id`` when the consumption query is (so the two halves agree).
    """
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

    # Human cost over the same window (segmentation, task_16_12). Canonical USD.
    human = await compute_human_cost_usd(
        session,
        tenant_id=tenant_id,
        plan_id=plan_id,
        window_start=since.date(),
    )
    ai_cost = accumulated
    human_cost = _quantize_cost(human.human_cost_usd)

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
        ai_cost_usd=ai_cost,
        human_cost_usd=human_cost,
        total_cost_usd=_quantize_cost(ai_cost + human_cost),
        human_hours_logged=human.hours_logged,
    )


# ===========================================================================
# Reusable runs query — the single source of truth for the runs explorer.
# Both this admin endpoint and the member-facing GET /runs (routers/runs.py)
# call it, so the filtering / fetch / currency logic lives in one place (DRY).
# ===========================================================================
async def query_execution_runs(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    limit: int,
    offset: int,
    window_days: int,
    agent_id: UUID | None = None,
    role: str | None = None,
    plan_id: UUID | None = None,
    task_id: UUID | None = None,
    verdict: str | None = None,
    model: str | None = None,
    min_cost: Decimal | None = None,
    display_currency: str | None = None,
    cursor: str | None = None,
) -> list[ExecutionRunRow]:
    """This tenant's executions, newest first, paginated + filtered + currency-applied.

    Tenant-scoped (the caller's session is RLS-bound) with a defence-in-depth
    ``tenant_id`` predicate inside ``_exec_filters``. Returns one
    :class:`ExecutionRunRow` per execution; never leaks prompts / completions /
    credentials / ``steps_log``.
    """
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
    target_currency = await _resolve_display_currency(
        session, tenant_id=tenant_id, override=display_currency
    )
    rows = await _fetch_runs(session, filters, limit=limit, offset=offset, cursor=cursor)
    return await _apply_display_currency(session, rows, target_currency)


# ===========================================================================
# GET /tenant-stats/runs — paginated, filterable runs explorer
# ===========================================================================
@router.get("/runs", response_model=list[ExecutionRunRow])
async def list_execution_runs(
    response: Response,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    limit: int = limit_query(),
    offset: int = offset_query(),
    cursor: str | None = Query(
        default=None,
        max_length=256,
        description=(
            "Keyset pagination token from a previous page's X-Next-Cursor header. "
            "When present it takes precedence over `offset` (mixing both would "
            "skip rows). Prefer it over `offset` for deep pagination."
        ),
    ),
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
    display_currency: str | None = Query(
        default=None,
        max_length=3,
        description=(
            "Override the tenant's display currency for this request (ISO-4217, "
            "e.g. EUR). Defaults to the tenant's Organization.display_currency. "
            "When not USD, each row also carries its cost converted at the run's "
            "date plus the applied rate. USD costs are never changed."
        ),
    ),
) -> list[ExecutionRunRow]:
    """List this tenant's executions, newest first, paginated + filtered. tenant_admin.

    One row per execution with resolved plan / task / agent labels, the model of
    the run's last model call, the verdict (terminal status), the owning task's
    retry count and the duration. Filterable by time window / agent / role /
    plan / task / verdict / model / min-cost. Tenant-scoped (RLS); canonical USD.

    Currency toggle (Plan 11.1): when the effective display currency (the
    ``display_currency`` query override, else the tenant's
    ``Organization.display_currency``) is not USD, every row additionally
    carries ``display_cost`` / ``applied_rate`` / ``applied_rate_date`` computed
    with the FX rate of that run's OWN date. The stored USD is never changed.
    """
    tenant_id = require_tenant_id(principal)
    rows = await query_execution_runs(
        session,
        tenant_id=tenant_id,
        limit=limit,
        offset=offset,
        window_days=window_days,
        agent_id=agent_id,
        role=role,
        plan_id=plan_id,
        task_id=task_id,
        verdict=verdict,
        model=model,
        min_cost=min_cost,
        display_currency=display_currency,
        cursor=cursor,
    )
    # El cursor viaja en cabecera y no en el cuerpo: la respuesta es una LISTA
    # y meterlo dentro obligaría a envolverla, rompiendo a todos los clientes.
    next_cursor = next_runs_cursor(rows, limit=limit)
    if next_cursor is not None:
        response.headers["X-Next-Cursor"] = next_cursor
    return rows


# ===========================================================================
# GET /tenant-stats/runs/export — CSV / XLSX / PDF(→HTML) of the runs explorer
# ===========================================================================
# Hard cap on the number of rows a single export may serialise. Exports are
# bounded (not streamed): an export is a synchronous build of one response
# body, so we bound it instead of letting a tenant ask for an unbounded report.
# A named constant, not a magic number — a platform invariant of the export
# contract.
#
# prod-13 task_prod13_18: el tope ya no es un callejón. El export acepta el MISMO
# `cursor` opaco que el explorador y devuelve `X-Next-Cursor` cuando llenó la
# página, así que un tenant con 20.000 runs los baja en cuatro ficheros en vez de
# perder los 15.000 últimos en silencio. Se resuelve con keyset y NO con `offset`
# a propósito: el offset que necesitaría la cuarta página son 15.000 filas que
# PostgreSQL produce y tira, y es justo el crecimiento que degrada solo.
#
# Lo que este cambio NO hace, dicho aquí para que nadie lo dé por hecho: el
# export sigue MATERIALIZANDO su página entera en memoria antes de serializar
# (`build_runs_export` devuelve bytes). Trocear la consulta en lotes internos no
# arreglaría eso —el cuerpo se construye igual— y saldría más caro: N consultas
# donde hoy hay una. Bajar el pico de memoria pide streaming del cuerpo, que es
# otro contrato y otra tarea.
MAX_EXPORT_ROWS = 5000


@router.get("/runs/export")
async def export_execution_runs(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    fmt: ExportFormat = Query(
        default=ExportFormat.CSV,
        alias="format",
        description="Export format: csv | xlsx | pdf (pdf is a print-ready HTML document).",
    ),
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
    cursor: str | None = Query(
        default=None,
        max_length=256,
        description=(
            "Keyset pagination token from a previous export's X-Next-Cursor "
            "header. Lets a tenant with more than MAX_EXPORT_ROWS runs download "
            "them in consecutive files instead of losing everything past the cap."
        ),
    ),
) -> Response:
    """Export this tenant's runs explorer to CSV / XLSX / PDF. tenant_admin.

    Same filters as ``GET /tenant-stats/runs``, serialised to a downloadable
    file (``Content-Disposition: attachment``). Tenant-scoped (RLS) with the
    same defence-in-depth ``tenant_id`` predicate, so an export NEVER contains
    another tenant's rows. Costs are canonical USD. The export carries only the
    operational columns the JSON explorer already exposes — no prompts /
    completions / credentials / ``steps_log`` leak through this surface.

    Formats: ``csv`` (stdlib), ``xlsx`` (openpyxl, pure-Python) and ``pdf`` —
    which is DEGRADED to a print-ready ``text/html`` document (the api-server
    image carries no native PDF renderer; the browser's "Save as PDF" closes
    the loop). If the optional ``openpyxl`` wheel is absent, ``xlsx`` returns a
    clean ``501`` rather than a 500.

    Bounded, not streamed: at most :data:`MAX_EXPORT_ROWS` rows in one export.
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
    rows = await _fetch_runs(session, filters, limit=MAX_EXPORT_ROWS, offset=0, cursor=cursor)

    # The PDF/HTML report embeds a consumption summary; CSV/XLSX are raw rows.
    consumption = (
        await _compute_consumption(
            session,
            filters,
            window_days=window_days,
            tenant_id=tenant_id,
            since=since,
            plan_id=plan_id,
        )
        if fmt is ExportFormat.PDF
        else None
    )

    try:
        content = build_runs_export(
            rows,
            fmt,
            title="Tenant runs export",
            window_days=window_days,
            consumption=consumption,
        )
    except ImportError as exc:  # openpyxl absent → degrade cleanly, never 500
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="xlsx export is not configured in this runtime; use format=csv",
        ) from exc

    headers = {
        "Content-Disposition": f'attachment; filename="{filename_for(fmt, stem="tenant-runs")}"'
    }
    # Sólo cuando la página se llenó: un cursor en la última página obligaría al
    # cliente a pedir un fichero vacío para descubrir que ya no hay nada.
    next_cursor = next_runs_cursor(rows, limit=MAX_EXPORT_ROWS)
    if next_cursor is not None:
        headers["X-Next-Cursor"] = next_cursor

    return Response(content=content, media_type=media_type_for(fmt), headers=headers)


# ---------------------------------------------------------------------------
# Paginación por keyset del explorador (prod-13)
# ---------------------------------------------------------------------------
# `OFFSET n` obliga a PostgreSQL a producir y descartar las n primeras filas: la
# página 500 cuesta 500 páginas de trabajo, y con el histórico de runs creciendo
# eso degrada sin que nadie cambie nada. El keyset usa el índice
# `(tenant_id, created_at)` de la migración 0126 para saltar directo.
#
# El `offset` NO se retira: los clientes de hoy paginan con él. Cuando llega un
# `cursor`, manda el cursor (sumar los dos saltaría filas).
_CURSOR_SEPARATOR = "|"


def encode_runs_cursor(created_at: datetime, execution_id: UUID) -> str:
    """Token opaco que apunta a la ÚLTIMA fila de una página.

    Opaco (base64url) a propósito: un cursor que parece una fecha invita a que
    el cliente lo fabrique, y el día que la clave de orden cambie —añadir un
    tercer desempate, por ejemplo— esos clientes se rompen en silencio.
    """
    raw = f"{created_at.isoformat()}{_CURSOR_SEPARATOR}{execution_id}"
    return base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii")


def decode_runs_cursor(cursor: str) -> tuple[datetime, UUID]:
    """Descodifica un cursor. Un token corrupto es un **400**, no un 500.

    Lo manda el cliente, así que un cursor roto es un error suyo; devolver 500
    lo convertiría en una alerta de servidor y en ruido de guardia.
    """
    try:
        raw = base64.urlsafe_b64decode(cursor.encode("ascii")).decode("utf-8")
        moment_text, _, id_text = raw.partition(_CURSOR_SEPARATOR)
        return datetime.fromisoformat(moment_text), UUID(id_text)
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="cursor is not a valid pagination token",
        ) from exc


def next_runs_cursor(rows: list[ExecutionRunRow], *, limit: int) -> str | None:
    """Cursor de la página siguiente, o ``None`` si esta ya era la última.

    Una página incompleta (``len(rows) < limit``) significa que no queda nada
    detrás; devolver cursor igualmente obligaría al cliente a una petición de
    más para descubrir el vacío.
    """
    if not rows or len(rows) < limit:
        return None
    last = rows[-1]
    return encode_runs_cursor(last.created_at, last.id)


def runs_select(
    filters: list[ColumnElement[bool]],
    *,
    limit: int,
    offset: int = 0,
    cursor: str | None = None,
) -> Select[Any]:
    """El SELECT del explorador de runs — **columnas escalares explícitas**.

    Antes era ``select(Execution, …)``, que trae la entidad ENTERA y con ella
    ``steps_log`` (hallazgo perf-6). Medido el 2026-08-01 sobre la instancia de
    desarrollo: ``steps_log`` es el 76 % de la tabla ``executions``, 9,5 KiB de
    media por run y hasta 64 KiB. El export llega a ``MAX_EXPORT_ROWS`` = 5.000
    filas — del orden de 50 MiB de JSONB materializados en el proceso para
    producir un CSV que no publica ni un byte de esa traza.

    Desde la migración **0139** ``steps_log`` ya NO aparece tampoco dentro de
    la resolución del modelo del último paso (:func:`_last_model_expr`), que era
    la mitad que faltaba de task_prod13_18: es una columna denormalizada. Con
    eso, este SELECT no toca el JSONB por ningún lado.
    """
    stmt = (
        select(
            Execution.id,
            Execution.created_at,
            Execution.task_id,
            Execution.agent_id,
            Execution.status,
            Execution.finish_status,
            Execution.total_tokens,
            Execution.total_cost_usd,
            Execution.started_at,
            Execution.completed_at,
            Task.title,
            Task.plan_id,
            Task.retry_count,
            Plan.title,
            Agent.name,
            Agent.role,
            _last_model_expr(),
            _duration_ms(),
        )
        .select_from(Execution)
        .outerjoin(Task, Task.id == Execution.task_id)
        .outerjoin(Plan, Plan.id == Task.plan_id)
        .outerjoin(Agent, Agent.id == Execution.agent_id)
        .where(*filters)
        .order_by(Execution.created_at.desc(), Execution.id.desc())
    )
    if cursor is not None:
        moment, last_id = decode_runs_cursor(cursor)
        # Comparación de FILA y no dos predicados sueltos: con
        # `created_at < :m AND id < :i` se perderían las filas del mismo
        # instante con id mayor, y con `created_at <= :m` se repetirían.
        stmt = stmt.where(
            tuple_(Execution.created_at, Execution.id) < tuple_(literal(moment), literal(last_id))
        )
        return stmt.limit(limit)
    return apply_pagination(stmt, limit=limit, offset=offset)


async def _fetch_runs(
    session: AsyncSession,
    filters: list[ColumnElement[bool]],
    *,
    limit: int,
    offset: int,
    cursor: str | None = None,
) -> list[ExecutionRunRow]:
    """Load the runs-explorer rows for ``filters`` (newest first, paginated).

    The single query behind both the JSON ``/runs`` endpoint and the export
    surface (task_14_14) so the two never drift. ``filters`` already carries the
    tenant scope + the time window + the optional narrowing predicates.
    """
    rows = (
        await session.execute(runs_select(filters, limit=limit, offset=offset, cursor=cursor))
    ).all()
    return [
        ExecutionRunRow(
            id=execution_id,
            created_at=created_at,
            task_id=task_id,
            task_title=task_title,
            plan_id=plan_id,
            plan_title=plan_title,
            agent_id=agent_id,
            agent_name=agent_name,
            agent_role=agent_role,
            model=model_name,
            verdict=exec_status,
            succeeded=exec_status == _DONE,
            finish_status=finish_status,
            retry_count=int(retry_count) if retry_count is not None else 0,
            duration_ms=int(duration) if duration is not None else None,
            total_tokens=total_tokens,
            total_cost_usd=total_cost_usd,
            started_at=started_at,
            completed_at=completed_at,
        )
        for (
            execution_id,
            created_at,
            task_id,
            agent_id,
            exec_status,
            finish_status,
            total_tokens,
            total_cost_usd,
            started_at,
            completed_at,
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
# Display-currency toggle (Plan 11.1 task_11_1_03) — USD canonical, FX display
# only, converted per row at each run's OWN date.
# ---------------------------------------------------------------------------
async def _resolve_display_currency(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    override: str | None,
) -> str:
    """The effective ISO-4217 display currency for the request (upper-cased).

    The ``display_currency`` query override wins when present; otherwise the
    tenant's ``Organization.display_currency`` (default EUR). Falls back to USD
    (the canonical currency, i.e. no conversion) when the tenant row cannot be
    read. Always returns an upper-cased code so the FX lookup is case-stable.
    """
    if override is not None and override.strip():
        return override.strip().upper()
    stored = await session.scalar(
        select(Organization.display_currency).where(Organization.id == tenant_id)
    )
    return (stored or CANONICAL_CURRENCY).strip().upper()


async def _apply_display_currency(
    session: AsyncSession,
    rows: list[ExecutionRunRow],
    target_currency: str,
) -> list[ExecutionRunRow]:
    """Annotate ``rows`` in place with the per-row display conversion.

    No-op (USD canonical) when ``target_currency`` is USD — the rows stay
    USD-only and the display fields remain ``None``. Otherwise the FX rate for
    each run's OWN date is resolved in a SINGLE query (no N+1), and each row's
    USD cost is converted at its date. A run whose date has no rate on or before
    it keeps ``None`` display fields (the UI falls back to the USD figure).
    """
    if not rows or target_currency == CANONICAL_CURRENCY:
        return rows

    run_dates = {row.created_at.date() for row in rows}
    rates = await resolve_rates_for_dates(session, target_currency, run_dates)

    for row in rows:
        rate_row = rates.get(row.created_at.date())
        if rate_row is None:
            # No rate on or before this run's date — leave the display fields
            # None; the UI falls back to the canonical USD figure.
            continue
        row.display_currency = target_currency
        row.display_cost = convert_from_usd_with_rate(row.total_cost_usd, rate_row.rate_vs_usd)
        row.applied_rate = rate_row.rate_vs_usd
        row.applied_rate_date = rate_row.as_of_date
    return rows


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
    """(suma de tokens de entrada, de salida) de los runs de la ventana.

    Desde la 0139 se suman DOS COLUMNAS. Antes esto desenrollaba el `steps_log`
    de todos los runs del período —el 76 % del peso de la tabla— para sumar dos
    enteros que ya se conocían al cerrar cada run.

    Un run sin llamadas a modelo aporta 0 y no desaparece del agregado: la forma
    anterior lo perdía (el `join` lateral con un array vacío no producía fila) y
    la suma es la misma, pero conviene tenerlo escrito porque es justo el tipo de
    diferencia que un refactor así introduce sin que nada falle.
    """
    row = (
        await session.execute(
            select(
                func.coalesce(func.sum(Execution.tokens_in), 0),
                func.coalesce(func.sum(Execution.tokens_out), 0),
            )
            .select_from(Execution)
            .outerjoin(Agent, Agent.id == Execution.agent_id)
            .outerjoin(Task, Task.id == Execution.task_id)
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


@router.get("/prompt-cache")
async def prompt_cache_report(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    window_days: int = _window_days(),
) -> dict[str, Any]:
    """Reutilización de la caché de prompt y coste por iteración, por proveedor
    (`task_wf_63`).

    La tarea es de MEDICIÓN, y el orden importa: `_decide_messages` reconstruye
    un mensaje de usuario grande cada turno, y pasarlo a una lista incremental
    —lo que dejaría a los proveedores con caché por prefijo aprovechar también
    el histórico— es un cambio con riesgo real sobre la convergencia. Antes de
    tocarlo hay que saber si sirve de algo.

    Se calcula sobre los `steps_log` que ya se persisten: sin tabla nueva, sin
    telemetría paralela, sin coste en el camino caliente.

    Lee `reports_cache` antes que `cached_prefix_pct`: un proveedor que no
    reporta caché y otro que la reporta siempre a cero son situaciones
    distintas, y confundirlas llevaría a optimizar a ciegas.
    """
    from api_server.prompt_cache_report import build_prompt_cache_report

    since = datetime.now(UTC) - timedelta(days=window_days)
    rows = list(
        (
            await session.execute(
                select(Execution.steps_log).where(
                    Execution.tenant_id == principal.tenant_id,
                    Execution.created_at >= since,
                )
            )
        ).all()
    )
    report = build_prompt_cache_report([(None, row[0] or []) for row in rows])
    payload = report.as_dict()
    payload["window_days"] = window_days
    return payload
