"""Plan cost computation (Plan 03 Fase F).

Two flavours, kept as pure functions so the routers and the planning
sub-graph can share them:

  - `compute_human_cost(spec, hourly_rate, currency)` — tarifa única
    del tenant (CLAUDE.md §6, ADR 0009 sucedánea) por horas estimadas
    de cada tarea. La tarifa horaria por tenant se configura en
    `task_03_26`; aquí la recibimos como argumento.

  - `compute_ai_cost(spec, price_catalog)` — catálogo de precios por
    modelo + multiplicador por complejidad. Devuelve un **rango**
    (min, max) porque la incertidumbre de tokens es alta a nivel de
    plan; en `executions` se materializa el coste real (Plan 02 §13).

Cada función devuelve un breakdown por tarea + un total, agnóstico
de la base de datos para que tests y planning sub-graph la puedan
invocar sin DB. La persistencia (snapshot en `plans.specification.
estimates`) la hace el router al persistir el Plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

# Default fallback when a task lacks `estimated_hours`. Conservative:
# 4 h is one half-day for an "average" task in this product's scope.
DEFAULT_TASK_HOURS = Decimal("4")

# Default hourly rate when the tenant has not configured one yet.
# Matches the placeholder cited in CLAUDE.md §6 "tarifa única tenant
# (50 €/h default)".
DEFAULT_HOURLY_RATE_EUR = Decimal("50")


@dataclass(frozen=True)
class TaskHumanCost:
    """Per-task human cost breakdown."""

    task_id: str
    title: str
    hours: Decimal
    cost: Decimal


@dataclass(frozen=True)
class HumanCostBreakdown:
    """The shape `plans.specification.estimates.cost_human_eur` snapshots
    + the per-task rows the UI renders (task_03_24)."""

    currency: str
    hourly_rate: Decimal
    total_hours: Decimal
    total_cost: Decimal
    tasks: tuple[TaskHumanCost, ...] = field(default_factory=tuple)


def _q2(value: Decimal) -> Decimal:
    """Round to 2 decimals, half-up — the EUR cents convention."""
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _q3(value: Decimal) -> Decimal:
    """Round hours to 3 decimals — finer granularity than cost."""
    return value.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def compute_human_cost(
    specification: dict[str, Any] | None,
    *,
    hourly_rate: Decimal = DEFAULT_HOURLY_RATE_EUR,
    currency: str = "EUR",
    default_task_hours: Decimal = DEFAULT_TASK_HOURS,
) -> HumanCostBreakdown:
    """Compute human cost for a plan specification.

    Sums ``estimated_hours`` for every task (falling back to
    ``default_task_hours`` when missing) and multiplies by the
    tenant's ``hourly_rate``. The per-task breakdown lets the UI
    render the table at task_03_24; the total goes into
    ``plans.specification.estimates.cost_human_eur``.

    The function tolerates a missing or malformed ``specification``
    (returns zeros). Callers should validate the spec upstream via
    Pydantic; this is the cost layer, not the validation layer.
    """
    tasks_raw = (specification or {}).get("tasks") or []
    rate = Decimal(hourly_rate)

    breakdown: list[TaskHumanCost] = []
    total_hours = Decimal("0")
    total_cost = Decimal("0")
    for task in tasks_raw:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id") or "")
        title = str(task.get("title") or "")
        hours = _coerce_hours(task.get("estimated_hours"), default_task_hours)
        cost = _q2(hours * rate)
        breakdown.append(TaskHumanCost(task_id=tid, title=title, hours=hours, cost=cost))
        total_hours += hours
        total_cost += cost

    return HumanCostBreakdown(
        currency=currency,
        hourly_rate=_q2(rate),
        total_hours=_q3(total_hours),
        total_cost=_q2(total_cost),
        tasks=tuple(breakdown),
    )


def _coerce_hours(raw: Any, default: Decimal) -> Decimal:
    """Pull a positive Decimal out of a task's ``estimated_hours``
    field. Anything not-coercible falls back to ``default``."""
    if raw is None:
        return _q3(default)
    try:
        value = Decimal(str(raw))
    except (ArithmeticError, ValueError, TypeError):
        return _q3(default)
    if value <= 0:
        return _q3(default)
    return _q3(value)


# ===========================================================================
# Human-agent plan estimate (Plan 16 task_16_13)
# ===========================================================================
@dataclass(frozen=True)
class HumanAgentEstimateInput:
    """The planning-estimate inputs of ONE assignable Human Agent.

    Sourced from a tenant's :class:`~api_server.db.domain.HumanAgentConfig`
    (Plan 16 task_16_02): the tarifa (``hourly_rate`` + ``currency``) and the
    two expected-time figures the PM uses to size a human task. ``None`` rates /
    times fall back to the platform defaults at estimate time (see
    :func:`compute_human_agent_plan_estimate`), so a half-configured Human Agent
    never crashes the estimate — it just costs the default rate / time.
    """

    agent_id: str
    name: str
    hourly_rate: Decimal | None = None
    currency: str = "EUR"
    expected_response_time_hours: int | None = None
    expected_execution_time_hours: int | None = None


@dataclass(frozen=True)
class TaskHumanAgentEstimate:
    """Per-(human-)task estimate for a task assigned to a Human Agent.

    ``duration_hours`` is the wall-clock the PM should budget — the human's
    ``expected_response_time_hours`` (time to pick the task up) PLUS the
    ``expected_execution_time_hours`` (time to do the work). ``cost`` is the
    chargeable part only: ``hourly_rate * expected_execution_time_hours``
    (response time is wait, not paid work).
    """

    task_id: str
    title: str
    human_agent_id: str
    response_hours: Decimal
    execution_hours: Decimal
    duration_hours: Decimal
    hourly_rate: Decimal
    currency: str
    cost: Decimal


@dataclass(frozen=True)
class HumanAgentPlanEstimate:
    """Aggregate of every human-agent-assigned task in a plan spec.

    Snapshot shape for ``plans.specification.estimates.human_agents`` + the
    rows the planning chat surfaces. Sums the per-task durations and costs;
    ``currency`` is the dominant tenant currency (the first non-empty one seen,
    EUR otherwise — mixing currencies in one plan is a Plan 11 ``exchange_rates``
    concern, not this layer's). Tasks NOT assigned to a Human Agent are ignored
    here (they ride the generic ``compute_human_cost`` / ``compute_ai_cost``
    paths unchanged).
    """

    currency: str
    total_duration_hours: Decimal
    total_cost: Decimal
    task_count: int
    tasks: tuple[TaskHumanAgentEstimate, ...] = field(default_factory=tuple)


def compute_human_agent_plan_estimate(
    specification: dict[str, Any] | None,
    human_agents: dict[str, HumanAgentEstimateInput],
    *,
    default_hourly_rate: Decimal = DEFAULT_HOURLY_RATE_EUR,
    default_response_hours: Decimal = DEFAULT_TASK_HOURS,
    default_execution_hours: Decimal = DEFAULT_TASK_HOURS,
) -> HumanAgentPlanEstimate:
    """Estimate duration + cost of the tasks a plan assigns to Human Agents.

    Mirrors :func:`compute_human_cost` (same DB-agnostic, pure-function shape)
    but for the *planning* estimate of Plan 16 task_16_13: a plan task may name
    a Human Agent via ``task['human_agent_id']`` exactly like an AI task names
    its ``model`` / agent. For each such task we look the agent up in
    ``human_agents`` (the gallery the planning context exposes) and compute:

      - ``duration_hours = expected_response_time_hours
                           + expected_execution_time_hours``  (wall-clock to
        budget — the human's pick-up wait plus their work time); and
      - ``cost = hourly_rate * expected_execution_time_hours``  (chargeable
        work only — waiting to accept is not paid).

    A task whose ``human_agent_id`` is missing OR is not in ``human_agents`` is
    skipped (it is not a recognised human-agent assignment). A recognised agent
    with a ``None`` rate / time falls back to the platform defaults so the
    estimate is always well-defined.

    AI-agent assignment + estimation behaviour is untouched: this function only
    ever LOOKS at tasks carrying a ``human_agent_id``.
    """
    tasks_raw = (specification or {}).get("tasks") or []

    rows: list[TaskHumanAgentEstimate] = []
    total_duration = Decimal("0")
    total_cost = Decimal("0")
    currency = "EUR"
    currency_seen = False

    for task in tasks_raw:
        if not isinstance(task, dict):
            continue
        agent_id = task.get("human_agent_id")
        if not agent_id:
            continue
        agent = human_agents.get(str(agent_id))
        if agent is None:
            continue

        tid = str(task.get("id") or "")
        title = str(task.get("title") or "")
        response_hours = _coerce_hours(agent.expected_response_time_hours, default_response_hours)
        execution_hours = _coerce_hours(
            agent.expected_execution_time_hours, default_execution_hours
        )
        duration_hours = _q3(response_hours + execution_hours)
        rate = agent.hourly_rate if agent.hourly_rate is not None else default_hourly_rate
        rate = Decimal(rate)
        cost = _q2(rate * execution_hours)

        if not currency_seen:
            currency = agent.currency or "EUR"
            currency_seen = True

        rows.append(
            TaskHumanAgentEstimate(
                task_id=tid,
                title=title,
                human_agent_id=str(agent_id),
                response_hours=response_hours,
                execution_hours=execution_hours,
                duration_hours=duration_hours,
                hourly_rate=_q2(rate),
                currency=agent.currency or "EUR",
                cost=cost,
            )
        )
        total_duration += duration_hours
        total_cost += cost

    return HumanAgentPlanEstimate(
        currency=currency,
        total_duration_hours=_q3(total_duration),
        total_cost=_q2(total_cost),
        task_count=len(rows),
        tasks=tuple(rows),
    )


# ===========================================================================
# AI cost (task_03_23)
# ===========================================================================
@dataclass(frozen=True)
class ModelPrice:
    """Per-1M-token price of one model. Currency is per entry so
    Anthropic-priced-in-USD and Azure-priced-in-EUR coexist in the
    same catalog without a forced conversion at this layer (Plan 11
    will harmonise via exchange_rates)."""

    model_id: str
    currency: str
    input_per_million: Decimal
    output_per_million: Decimal


@dataclass(frozen=True)
class ComplexityTokenEstimate:
    """Per-complexity, per-task token budget estimate.

    `base_*` is the midpoint. `low_factor` and `high_factor` widen the
    midpoint to a range — the result of `compute_ai_cost` carries a
    `(min_cost, max_cost)` pair, never a single number, because token
    counts at plan time are genuinely uncertain.
    """

    complexity: str
    base_input_tokens: int
    base_output_tokens: int
    low_factor: Decimal = Decimal("0.5")
    high_factor: Decimal = Decimal("1.5")


# Placeholder token budgets per complexity. Numbers picked from
# observation of similar agent loops; they are NOT empirical for this
# project. Plan 11 (`task_11_15`) refines them with real data from
# `executions.total_tokens`.
DEFAULT_COMPLEXITY_ESTIMATES: dict[str, ComplexityTokenEstimate] = {
    "xs": ComplexityTokenEstimate("xs", base_input_tokens=2_000, base_output_tokens=500),
    "s": ComplexityTokenEstimate("s", base_input_tokens=5_000, base_output_tokens=1_500),
    "m": ComplexityTokenEstimate("m", base_input_tokens=15_000, base_output_tokens=4_000),
    "l": ComplexityTokenEstimate("l", base_input_tokens=40_000, base_output_tokens=10_000),
    "xl": ComplexityTokenEstimate("xl", base_input_tokens=100_000, base_output_tokens=25_000),
}


@dataclass(frozen=True)
class PriceCatalog:
    """Maps a model id to its `ModelPrice`. Tenants pick the model
    they run with; the breakdown uses that model for every task unless
    the task overrides it via `task.model`."""

    prices: dict[str, ModelPrice] = field(default_factory=dict)

    def get(self, model_id: str) -> ModelPrice | None:
        return self.prices.get(model_id)


# Placeholder prices (USD per 1M tokens) — Plan 11 wires the real catalog.
# Reference values from public 2026-Q2 pricing for Anthropic, OpenAI
# via Azure Foundry, and Ollama (local = $0).
DEFAULT_AI_PRICE_CATALOG = PriceCatalog(
    prices={
        "claude-opus-4-7": ModelPrice(
            "claude-opus-4-7",
            currency="USD",
            input_per_million=Decimal("15.00"),
            output_per_million=Decimal("75.00"),
        ),
        "claude-sonnet-4-6": ModelPrice(
            "claude-sonnet-4-6",
            currency="USD",
            input_per_million=Decimal("3.00"),
            output_per_million=Decimal("15.00"),
        ),
        "claude-haiku-4-5": ModelPrice(
            "claude-haiku-4-5",
            currency="USD",
            input_per_million=Decimal("0.80"),
            output_per_million=Decimal("4.00"),
        ),
        "gpt-4o": ModelPrice(
            "gpt-4o",
            currency="USD",
            input_per_million=Decimal("2.50"),
            output_per_million=Decimal("10.00"),
        ),
        # Ollama local = no API cost; the GPU bill is the operator's
        # problem and we surface it as 0 here.
        "llama3.1": ModelPrice(
            "llama3.1",
            currency="USD",
            input_per_million=Decimal("0"),
            output_per_million=Decimal("0"),
        ),
    }
)


@dataclass(frozen=True)
class TaskAICost:
    """Per-task AI cost range."""

    task_id: str
    title: str
    complexity: str
    model_id: str
    tokens_in_min: int
    tokens_in_max: int
    tokens_out_min: int
    tokens_out_max: int
    cost_min: Decimal
    cost_max: Decimal


@dataclass(frozen=True)
class AICostBreakdown:
    """The shape `plans.specification.estimates.cost_ai_<currency>` snapshots.

    `cost_min` / `cost_max` are the sum of every task's range. Tasks
    whose model is missing from the catalog count as 0 (we surface them
    in ``missing_models`` so the UI can warn)."""

    currency: str
    default_model_id: str
    cost_min: Decimal
    cost_max: Decimal
    tasks: tuple[TaskAICost, ...] = field(default_factory=tuple)
    missing_models: tuple[str, ...] = field(default_factory=tuple)


def _q4(value: Decimal) -> Decimal:
    """Round to 4 decimals (≈0.0001 USD precision — AI costs are tiny)."""
    return value.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def compute_ai_cost(
    specification: dict[str, Any] | None,
    *,
    default_model_id: str,
    catalog: PriceCatalog = DEFAULT_AI_PRICE_CATALOG,
    complexity_estimates: dict[str, ComplexityTokenEstimate] | None = None,
    default_complexity: str = "m",
    task_models: dict[str, str] | None = None,
) -> AICostBreakdown:
    """Compute AI cost range for a plan specification.

    For each task:
      1. Resolve the model id by precedence: ``task_models[task_id]``
         (the model resolved from the task's assigned agent — override or
         inherited, ADR 0065) > task-level ``model`` override > ``default_model_id``.
      2. Look up the price in the catalog. If missing, the task counts
         as 0 cost and the model id lands in ``missing_models``.
      3. Resolve the complexity to a `ComplexityTokenEstimate`. Unknown
         complexity falls back to ``default_complexity``.
      4. ``cost_min = (in_tokens * low + out_tokens * low) priced at /1M``.
         ``cost_max = same with high_factor``.

    Currency is taken from the **default model** — mixing currencies
    inside one plan is a Plan 11 concern (`exchange_rates`). For now,
    we trust the operator to pick a coherent catalog.
    """
    estimates = complexity_estimates or DEFAULT_COMPLEXITY_ESTIMATES
    tasks_raw = (specification or {}).get("tasks") or []
    resolved_models = task_models or {}

    default_price = catalog.get(default_model_id)
    currency = default_price.currency if default_price else "USD"

    breakdown: list[TaskAICost] = []
    missing: set[str] = set()
    total_min = Decimal("0")
    total_max = Decimal("0")

    for task in tasks_raw:
        if not isinstance(task, dict):
            continue
        tid = str(task.get("id") or "")
        title = str(task.get("title") or "")
        complexity = str(task.get("complexity") or default_complexity).lower()
        if complexity not in estimates:
            complexity = default_complexity
        est = estimates[complexity]

        model_id = resolved_models.get(tid) or str(task.get("model") or default_model_id)
        price = catalog.get(model_id)
        if price is None:
            missing.add(model_id)
            # Emit a row at 0 so the UI shows the missing-model warning.
            breakdown.append(
                TaskAICost(
                    task_id=tid,
                    title=title,
                    complexity=complexity,
                    model_id=model_id,
                    tokens_in_min=0,
                    tokens_in_max=0,
                    tokens_out_min=0,
                    tokens_out_max=0,
                    cost_min=Decimal("0.0000"),
                    cost_max=Decimal("0.0000"),
                )
            )
            continue

        in_min = int(Decimal(est.base_input_tokens) * est.low_factor)
        in_max = int(Decimal(est.base_input_tokens) * est.high_factor)
        out_min = int(Decimal(est.base_output_tokens) * est.low_factor)
        out_max = int(Decimal(est.base_output_tokens) * est.high_factor)

        cost_in_min = (Decimal(in_min) / Decimal(1_000_000)) * price.input_per_million
        cost_in_max = (Decimal(in_max) / Decimal(1_000_000)) * price.input_per_million
        cost_out_min = (Decimal(out_min) / Decimal(1_000_000)) * price.output_per_million
        cost_out_max = (Decimal(out_max) / Decimal(1_000_000)) * price.output_per_million

        c_min = _q4(cost_in_min + cost_out_min)
        c_max = _q4(cost_in_max + cost_out_max)
        total_min += c_min
        total_max += c_max

        breakdown.append(
            TaskAICost(
                task_id=tid,
                title=title,
                complexity=complexity,
                model_id=model_id,
                tokens_in_min=in_min,
                tokens_in_max=in_max,
                tokens_out_min=out_min,
                tokens_out_max=out_max,
                cost_min=c_min,
                cost_max=c_max,
            )
        )

    return AICostBreakdown(
        currency=currency,
        default_model_id=default_model_id,
        cost_min=_q4(total_min),
        cost_max=_q4(total_max),
        tasks=tuple(breakdown),
        missing_models=tuple(sorted(missing)),
    )


__all__ = [
    "DEFAULT_AI_PRICE_CATALOG",
    "DEFAULT_COMPLEXITY_ESTIMATES",
    "DEFAULT_HOURLY_RATE_EUR",
    "DEFAULT_TASK_HOURS",
    "AICostBreakdown",
    "ComplexityTokenEstimate",
    "HumanAgentEstimateInput",
    "HumanAgentPlanEstimate",
    "HumanCostBreakdown",
    "ModelPrice",
    "PriceCatalog",
    "TaskAICost",
    "TaskHumanAgentEstimate",
    "TaskHumanCost",
    "compute_ai_cost",
    "compute_human_agent_plan_estimate",
    "compute_human_cost",
]
