"""Pydantic schemas for the tenant quality dashboard (Plan 14 task_14_11).

The read shapes that back the eval QUALITY dashboard — a tenant view of how
its agents score over time, broken down by AGENT, by PROMPT RELEASE
(``EvalRun.subject_prompt_version``) and by DATASET (the per-tenant golden
benchmark; ``EvalRun`` is dataset-scoped, not project-scoped, so the dataset is
the natural "by project / by benchmark" grouping — there is no ``project_id``
on an eval run).

Two response surfaces, both tenant-scoped (RLS) so a tenant only ever sees its
own runs / results:

  * :class:`EvalQualityDashboardResponse` — the aggregated dashboard: headline
    totals, the pass-rate trend (per UTC day), the per-agent / per-prompt-version
    / per-dataset breakdowns and the per-criterion pass-rate breakdown. Drives
    the dashboard's charts / tables without the client aggregating client-side.

  * :class:`EvalRunHistoryItem` — one completed (or terminal) run as returned by
    the paginated, filterable run-history list (``/eval-quality/runs``).

Costs are in CANONICAL USD (``mean_cost_usd``). A tenant-currency display toggle
is intentionally NOT modelled here: the FX / display-currency system it depends
on (exchange_rates) has no numbered task and was not built (flagged as a scope
gap in Plan 11's changelog). USD is the only currency surfaced.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(from_attributes=True)


# =============================================================================
# Breakdown rows (grouped aggregates)
# =============================================================================
class AgentQualityBreakdown(BaseModel):
    """Quality roll-up for one subject agent across the window.

    ``pass_rate`` is the items-weighted pass rate (passed_items / total_items)
    over the agent's completed runs — NOT a naive average of per-run rates, so a
    big run is not outweighed by a tiny one. ``None`` when no items were
    measured. ``mean_cost_usd`` / ``mean_tokens`` are canonical-USD / token
    means over the runs that reported them.
    """

    model_config = _CONFIG

    subject_agent_id: UUID | None
    agent_name: str | None
    agent_role: str | None
    run_count: int
    total_items: int
    passed_items: int
    pass_rate: Decimal | None
    mean_cost_usd: Decimal | None
    mean_tokens: Decimal | None


class PromptVersionQualityBreakdown(BaseModel):
    """Quality roll-up for one prompt release (``subject_prompt_version``)."""

    model_config = _CONFIG

    subject_prompt_version: str | None
    run_count: int
    total_items: int
    passed_items: int
    pass_rate: Decimal | None
    mean_cost_usd: Decimal | None


class DatasetQualityBreakdown(BaseModel):
    """Quality roll-up for one golden dataset (the per-tenant benchmark).

    The dataset stands in for the "by project / by benchmark" dimension — an
    :class:`~api_server.db.evals.EvalRun` is scoped to a dataset, not a project.
    """

    model_config = _CONFIG

    dataset_id: UUID
    dataset_name: str | None
    run_count: int
    total_items: int
    passed_items: int
    pass_rate: Decimal | None


class CriterionQualityBreakdown(BaseModel):
    """Per-criterion pass rate across all results in the window.

    Computed over the ``criterion_scores`` JSONB on each
    :class:`~api_server.db.evals.EvalResult` (one entry per criterion the judge
    scored): ``passed`` / ``scored`` is the fraction of item-judgements that
    passed this criterion. ``criterion_name`` is resolved from
    ``eval_criteria`` when the criterion still exists (it may have been
    soft-deleted after the run).
    """

    model_config = _CONFIG

    criterion_id: UUID | None
    criterion_name: str | None
    scored: int
    passed: int
    pass_rate: Decimal | None


class QualityTrendPoint(BaseModel):
    """One day of the pass-rate trend (UTC day).

    ``pass_rate`` is the items-weighted pass rate of the runs that COMPLETED on
    that day; ``run_count`` is how many runs landed that day.
    """

    day: str
    run_count: int
    total_items: int
    passed_items: int
    pass_rate: Decimal | None


# =============================================================================
# Run history (paginated list item)
# =============================================================================
class EvalRunHistoryItem(BaseModel):
    """One run as shown in the run-history table (newest first).

    Tenant-scoped (RLS) — never another tenant's run. ``agent_name`` /
    ``agent_role`` / ``dataset_name`` are resolved labels for display.
    """

    model_config = _CONFIG

    id: UUID
    dataset_id: UUID
    dataset_name: str | None
    status: str
    subject_agent_id: UUID | None
    agent_name: str | None
    agent_role: str | None
    subject_prompt_version: str | None
    judge_model: str | None
    started_at: datetime | None
    finished_at: datetime | None
    total_items: int
    passed_items: int
    pass_rate: Decimal | None
    mean_latency_ms: Decimal | None
    mean_tokens: Decimal | None
    mean_cost_usd: Decimal | None
    created_at: datetime


# =============================================================================
# Aggregated dashboard
# =============================================================================
class EvalQualityDashboardResponse(BaseModel):
    """The aggregated quality dashboard for the caller's tenant (task_14_11).

    All figures are over COMPLETED runs (``status == 'completed'``) in the
    window; costs are canonical USD. ``currency`` is always ``"USD"`` — the
    tenant-currency display toggle depends on the unbuilt FX system (Plan 11
    scope gap) and is not surfaced.
    """

    model_config = _CONFIG

    window_days: int
    currency: str = Field(default="USD")
    total_runs: int
    total_items: int
    passed_items: int
    overall_pass_rate: Decimal | None
    by_agent: list[AgentQualityBreakdown]
    by_prompt_version: list[PromptVersionQualityBreakdown]
    by_dataset: list[DatasetQualityBreakdown]
    by_criterion: list[CriterionQualityBreakdown]
    trend: list[QualityTrendPoint]


__all__ = [
    "AgentQualityBreakdown",
    "CriterionQualityBreakdown",
    "DatasetQualityBreakdown",
    "EvalQualityDashboardResponse",
    "EvalRunHistoryItem",
    "PromptVersionQualityBreakdown",
    "QualityTrendPoint",
]
