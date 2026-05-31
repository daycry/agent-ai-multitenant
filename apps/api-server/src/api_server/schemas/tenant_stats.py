"""Pydantic schemas for the tenant STATISTICS dashboard (Plan 14 task_14_12).

The read shapes behind the tenant statistics dashboard, the consumption
summary and the per-execution runs explorer. Everything here aggregates the
:class:`~api_server.db.domain.Execution` table (one row per run of the agent
loop against a task) joined to its task / plan / agent — NOT the eval-quality
``EvalRun`` roll-ups (those back task_14_11). The denormalised per-execution
usage columns ``total_tokens`` / ``total_cost_usd`` / ``model_call_count`` and
the Plan 11 per-call price snapshot are the canonical sources; the dashboard
rolls them up so the client never has to.

Three response surfaces, all tenant-scoped (RLS) so a tenant only ever sees
its own executions:

  * :class:`TenantStatsDashboardResponse` — agent statistics over a window:
    headline totals + the per-AGENT breakdown (success rate, mean duration,
    mean cost USD, runs), the TOP / BOTTOM agents by success rate and the
    per-UTC-day temporal trend (run count + success rate + cost).

  * :class:`ConsumptionSummaryResponse` — the consumption summary card:
    accumulated cost (USD), total tokens (input / output / cached), run count,
    mean cost per run and the single costliest run.

  * :class:`ExecutionRunRow` — one execution as returned by the paginated,
    filterable runs explorer (``/tenant-stats/runs``).

Costs are CANONICAL USD. On top of that, the runs explorer (:class:`ExecutionRunRow`)
ALSO surfaces a per-row DISPLAY conversion when the caller's display currency is
not USD: the USD cost converted with the FX rate of each run's own date plus the
applied rate (for traceability). The stored USD is never mutated — conversion is
presentation only (Plan 11.1 task_11_1_03, ``api_server.fx`` / ``exchange_rates``).

Cached-token counts: the per-step ``steps_log`` records only ``tokens_in`` /
``tokens_out`` (the Plan 11 price snapshot freezes the *cached price*, not the
cached token *count*), so ``total_tokens_cached`` is reported as ``0`` until
the runtime captures cached counts per call — never fabricated.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(from_attributes=True)


# =============================================================================
# Agent statistics dashboard
# =============================================================================
class AgentStatsBreakdown(BaseModel):
    """Statistics roll-up for one agent over the window.

    ``success_rate`` is the fraction of the agent's executions that ended
    ``done`` (a fraction in ``[0, 1]``; ``None`` when the agent had no runs).
    ``mean_duration_ms`` / ``mean_cost_usd`` are means over the runs that
    reported them (a run that never stamped ``completed_at`` or whose cost is
    unknown is skipped, not counted as zero).
    """

    model_config = _CONFIG

    agent_id: UUID | None
    agent_name: str | None
    agent_role: str | None
    run_count: int
    succeeded: int
    success_rate: Decimal | None
    mean_duration_ms: Decimal | None
    mean_cost_usd: Decimal | None
    total_cost_usd: Decimal
    total_tokens: int


class StatsTrendPoint(BaseModel):
    """One UTC day of the temporal trend.

    ``success_rate`` is the fraction of that day's executions that ended
    ``done``; ``total_cost_usd`` is the day's accumulated canonical-USD cost.
    """

    day: str
    run_count: int
    succeeded: int
    success_rate: Decimal | None
    total_cost_usd: Decimal


class TenantStatsDashboardResponse(BaseModel):
    """The tenant statistics dashboard for the caller's tenant (task_14_12).

    Aggregated over the tenant's :class:`~api_server.db.domain.Execution` rows
    in the window: headline success rate / mean duration / mean cost, the
    per-agent breakdown, the TOP and BOTTOM agents by success rate (agents with
    at least one run, ranked; the same agent may appear in both lists when there
    are fewer than ``2 * limit`` agents) and the per-day temporal trend. Costs
    are canonical USD; ``currency`` is always ``"USD"`` (tenant-currency display
    depends on the unbuilt FX system — Plan 11 scope gap).
    """

    model_config = _CONFIG

    window_days: int
    currency: str = Field(default="USD")
    total_runs: int
    succeeded_runs: int
    overall_success_rate: Decimal | None
    mean_duration_ms: Decimal | None
    mean_cost_usd: Decimal | None
    total_cost_usd: Decimal
    by_agent: list[AgentStatsBreakdown]
    top_agents: list[AgentStatsBreakdown]
    bottom_agents: list[AgentStatsBreakdown]
    trend: list[StatsTrendPoint]


# =============================================================================
# Consumption summary
# =============================================================================
class CostliestRun(BaseModel):
    """The single most expensive execution in the window (or None)."""

    model_config = _CONFIG

    execution_id: UUID
    task_id: UUID
    task_title: str | None
    agent_name: str | None
    total_cost_usd: Decimal
    total_tokens: int
    created_at: datetime


class ConsumptionSummaryResponse(BaseModel):
    """The consumption summary card for the caller's tenant (task_14_12).

    Accumulated cost (USD), total tokens broken into input / output / cached,
    the run count, the mean cost per run and the single costliest run, all over
    the tenant's executions in the window. Costs canonical USD.

    ``total_tokens_cached`` is ``0`` until the runtime captures cached token
    counts per call (the price snapshot freezes the cached *price*, not the
    count) — never fabricated. ``total_tokens`` is the authoritative
    denormalised total (``Execution.total_tokens``); the input/output split is
    summed from the ``steps_log`` model-call records.
    """

    model_config = _CONFIG

    window_days: int
    currency: str = Field(default="USD")
    run_count: int
    accumulated_cost_usd: Decimal
    mean_cost_usd: Decimal | None
    total_tokens: int
    total_tokens_input: int
    total_tokens_output: int
    total_tokens_cached: int
    costliest_run: CostliestRun | None


# =============================================================================
# Runs explorer (paginated list item)
# =============================================================================
class ExecutionRunRow(BaseModel):
    """One execution as shown in the runs explorer (newest first).

    Tenant-scoped (RLS) — never another tenant's execution. ``verdict`` is the
    execution's terminal status (``done`` / ``aborted`` / ``failed`` / …);
    ``succeeded`` is the boolean shorthand (``status == 'done'``). ``model`` is
    the model of the run's last ``model_call`` step (``None`` when the run made
    no priced model call). ``retry_count`` is the owning task's retry count
    (per the task, shared across its executions). ``duration_ms`` is
    ``completed_at - started_at`` in ms (``None`` when the run never finished).

    Cost is CANONICAL USD (``total_cost_usd``, the stored value — never
    mutated). When the caller's display currency is not USD the explorer ALSO
    surfaces a *display* conversion, computed on the fly with the FX rate of
    **this run's own date** (Plan 11.1, FX is display-only):

      * ``display_currency`` — the currency the conversion targets (``None``
        when display is USD, i.e. no conversion was done);
      * ``display_cost`` — ``total_cost_usd`` converted to ``display_currency``
        at this run's date (``None`` when no conversion / no rate available —
        the UI falls back to the USD figure);
      * ``applied_rate`` — the FX rate applied (units of ``display_currency``
        per 1 USD), for traceability / the tooltip;
      * ``applied_rate_date`` — the ``as_of_date`` of the rate row actually
        used (this run's date or the most recent prior publish), so the UI can
        show *which* day's rate priced the row.

    The display fields are populated per-row, so two runs on different dates may
    carry different ``applied_rate`` / ``applied_rate_date`` for the same
    currency. A run whose date has no rate on or before it leaves them ``None``.
    """

    model_config = _CONFIG

    id: UUID
    created_at: datetime
    task_id: UUID
    task_title: str | None
    plan_id: UUID | None
    plan_title: str | None
    agent_id: UUID | None
    agent_name: str | None
    agent_role: str | None
    model: str | None
    verdict: str
    succeeded: bool
    retry_count: int
    duration_ms: int | None
    total_tokens: int
    total_cost_usd: Decimal
    display_currency: str | None = None
    display_cost: Decimal | None = None
    applied_rate: Decimal | None = None
    applied_rate_date: date | None = None
    started_at: datetime | None
    completed_at: datetime | None


__all__ = [
    "AgentStatsBreakdown",
    "ConsumptionSummaryResponse",
    "CostliestRun",
    "ExecutionRunRow",
    "StatsTrendPoint",
    "TenantStatsDashboardResponse",
]
