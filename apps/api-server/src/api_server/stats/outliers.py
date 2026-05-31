"""Agent OUTLIER detection + configurable alerts (Plan 14 Fase D, task_14_13).

Identify the tenant's outlier agents — those whose success rate / cost /
latency over a window deviates significantly from the tenant norm — and, when a
tenant-configured :class:`~api_server.db.outlier_alert_rule.OutlierAlertRule`
trips, fire ONE alert per breaching agent through the Plan 10 notifier
(reusing the guardrail-alert / drift-alert dispatch seam).

Two outlier notions, both expressed as configurable rules — NEVER magic
numbers:

  * a success-rate FLOOR ("if agent X success rate < 70%, alert"): an agent
    whose success rate over the window is below ``success_rate_floor`` is an
    outlier. Absolute, per-agent.
  * a statistical deviation ABOVE the tenant norm: an agent whose mean cost /
    latency is more than ``stddev_k`` population standard deviations above the
    tenant mean over the window is an outlier — "el que destaca o flaquea"
    relative to its peers.

Two layers, both small + testable (the same split Fase B/C/D uses):

  * **Detection** — :func:`detect_outliers` is a PURE function over a sequence
    of per-agent :class:`AgentMetric` rows + the rule config. It returns the
    flagged agents (with the value, the comparison bound and a reason), so a
    test asserts the exact verdict with no DB. An agent below ``min_runs`` is
    never flagged (a tiny sample is not significant); the stddev branch needs
    at least two qualifying agents (a population of one has no spread).

  * **Evaluation + alert** — :func:`evaluate_outlier_rules` loads a tenant's
    enabled rules + the per-agent execution aggregates for each rule's window
    under the caller's TENANT-SCOPED RLS session, runs the pure detector per
    rule, and on a breach dispatches ONE ``agent_outlier_alert`` event per
    breaching agent through the Plan 10 notifier to the tenant's Tenant Admins.
    A per-rule ``last_fired_at`` debounces a still-outlying agent so it does
    not spam — mirroring the guardrail-alert / drift-alert debounce.

Multi-tenancy (NON-NEGOTIABLE): everything runs on the caller's tenant-scoped
RLS session and additionally filters ``tenant_id ==`` (defence in depth), so
tenant A's agents can never alert / debounce tenant B. This is a TENANT
surface; cross-tenant comparison is the separate System-Admin-only task_14_15.
Costs are CANONICAL USD (the tenant-currency display toggle depends on the
unbuilt FX system — Plan 11 scope gap — so it is not surfaced here).

``now`` is injectable so the debounce is deterministic in tests, and the
dispatcher is a seam so tests assert the enqueue without a live broker.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from uuid import UUID

import structlog
from sqlalchemy import ColumnElement, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Execution
from api_server.db.outlier_alert_rule import OutlierAlertRule, OutlierMetric

_log = structlog.get_logger("api_server.stats.outliers")

# The notification event_type the Plan 10 dispatcher maps a fired outlier alert
# to (registered in the dispatcher's EVENT_REGISTRY + builtin templates). A
# named constant, not an inline literal.
AGENT_OUTLIER_ALERT_EVENT_TYPE = "agent_outlier_alert"

# Quantum the success-rate fraction is rounded to (mirrors the dashboard's
# rate quantum) so the detector's comparison is stable.
_RATE_QUANTUM = Decimal("0.001")

# The terminal execution status that counts as a SUCCESS (mirrors the agent
# runtime's done state + the tenant_stats router; kept as a literal so this
# module stays import-light).
_DONE = "done"


# =============================================================================
# Per-agent metric row (the unit the pure detector consumes)
# =============================================================================
@dataclass(frozen=True)
class AgentMetric:
    """One agent's aggregated statistics over the window.

    ``success_rate`` is the fraction of the agent's runs that ended ``done``
    (``None`` when the agent had no runs — undefined, not zero). ``mean_cost``
    is the mean cost per run (canonical USD); ``mean_latency_ms`` is the mean
    finished-run duration (``None`` when no run finished). ``run_count`` gates
    the ``min_runs`` significance check.
    """

    agent_id: UUID | None
    agent_name: str | None
    agent_role: str | None
    run_count: int
    success_rate: Decimal | None
    mean_cost: Decimal | None
    mean_latency_ms: Decimal | None

    def metric_value(self, metric: OutlierMetric) -> Decimal | None:
        """The value of the rule's ``metric`` for this agent (``None`` if absent)."""
        if metric is OutlierMetric.SUCCESS_RATE:
            return self.success_rate
        if metric is OutlierMetric.COST:
            return self.mean_cost
        return self.mean_latency_ms


@dataclass(frozen=True)
class FlaggedAgent:
    """One agent the detector flagged as an outlier for a rule.

    ``value`` is the agent's metric value; ``bound`` is the threshold it
    breached (the floor for ``success_rate``, ``mean + k*stddev`` for the
    statistical metrics). ``reason`` is a human string echoed into the alert.
    """

    agent_id: UUID | None
    agent_name: str | None
    agent_role: str | None
    run_count: int
    value: Decimal
    bound: Decimal
    reason: str


@dataclass(frozen=True)
class OutlierDecision:
    """The verdict of :func:`detect_outliers` for one rule over the metrics.

    ``flagged`` lists every outlier agent; ``population_mean`` /
    ``population_stddev`` are the tenant-norm statistics the stddev branch used
    (``None`` for the floor branch or when the population was too small).
    """

    metric: OutlierMetric
    flagged: tuple[FlaggedAgent, ...]
    considered: int
    population_mean: Decimal | None
    population_stddev: Decimal | None


def detect_outliers(
    metrics: Sequence[AgentMetric],
    *,
    metric: OutlierMetric,
    min_runs: int,
    success_rate_floor: Decimal | None = None,
    stddev_k: Decimal | None = None,
) -> OutlierDecision:
    """Decide which agents are outliers for a rule — PURE, no I/O.

    Only agents with ``run_count >= min_runs`` AND a defined metric value are
    *considered* (a tiny / metric-less sample is never flagged). Then:

      * ``success_rate`` — an agent whose value is BELOW ``success_rate_floor``
        is flagged (a LOWER bound; ``success_rate_floor`` required).
      * ``cost`` / ``latency`` — compute the population mean + standard
        deviation over the considered agents and flag any whose value exceeds
        ``mean + stddev_k * stddev`` (an UPPER bound; ``stddev_k`` required).
        With fewer than two considered agents there is no spread, so nothing is
        flagged.

    Raises :class:`ValueError` on a metric/threshold mismatch (the API + the DB
    CHECK already prevent this; this guards a direct caller).
    """
    if min_runs < 1:
        raise ValueError(f"min_runs must be >= 1, got {min_runs}")

    considered = [
        m for m in metrics if m.run_count >= min_runs and m.metric_value(metric) is not None
    ]

    if metric is OutlierMetric.SUCCESS_RATE:
        if success_rate_floor is None:
            raise ValueError("success_rate metric requires a success_rate_floor")
        flagged: list[FlaggedAgent] = []
        for m in considered:
            value = m.success_rate
            assert value is not None  # guaranteed by `considered`
            if value < success_rate_floor:
                flagged.append(
                    FlaggedAgent(
                        agent_id=m.agent_id,
                        agent_name=m.agent_name,
                        agent_role=m.agent_role,
                        run_count=m.run_count,
                        value=value,
                        bound=success_rate_floor,
                        reason=(
                            f"success rate {value} is below the floor "
                            f"{success_rate_floor} over {m.run_count} run(s)"
                        ),
                    )
                )
        return OutlierDecision(
            metric=metric,
            flagged=tuple(flagged),
            considered=len(considered),
            population_mean=None,
            population_stddev=None,
        )

    # Statistical (cost / latency) branch: deviation ABOVE the tenant mean.
    if stddev_k is None:
        raise ValueError(f"{metric.value} metric requires a stddev_k")
    values = [m.metric_value(metric) for m in considered]
    typed_values = [v for v in values if v is not None]
    if len(typed_values) < 2:
        # No spread with fewer than two agents — nothing can deviate.
        return OutlierDecision(
            metric=metric,
            flagged=(),
            considered=len(considered),
            population_mean=(typed_values[0] if typed_values else None),
            population_stddev=None,
        )

    mean, stddev = _mean_stddev(typed_values)
    bound = mean + stddev_k * stddev
    flagged = []
    for m in considered:
        value = m.metric_value(metric)
        assert value is not None
        if value > bound:
            flagged.append(
                FlaggedAgent(
                    agent_id=m.agent_id,
                    agent_name=m.agent_name,
                    agent_role=m.agent_role,
                    run_count=m.run_count,
                    value=value,
                    bound=bound,
                    reason=(
                        f"{metric.value} {value} exceeds tenant mean {mean} + "
                        f"{stddev_k}·stddev {stddev} (= {bound}) over "
                        f"{m.run_count} run(s)"
                    ),
                )
            )
    return OutlierDecision(
        metric=metric,
        flagged=tuple(flagged),
        considered=len(considered),
        population_mean=mean,
        population_stddev=stddev,
    )


def _mean_stddev(values: Sequence[Decimal]) -> tuple[Decimal, Decimal]:
    """Population mean + population standard deviation over ``values`` (Decimal).

    Population (not sample) stddev — the agents are the WHOLE population for the
    window, not a sample of a larger one. Pure Decimal arithmetic so there is no
    float drift in the bound the test asserts against.
    """
    n = Decimal(len(values))
    mean = sum(values, Decimal("0")) / n
    variance = sum(((v - mean) ** 2 for v in values), Decimal("0")) / n
    return mean, _sqrt(variance)


def _sqrt(value: Decimal) -> Decimal:
    """Decimal square root (``value`` is a non-negative variance)."""
    if value <= 0:
        return Decimal("0")
    return value.sqrt()


# =============================================================================
# Alert dispatch seam (reuses the Plan 10 / guardrail-alert pattern)
# =============================================================================
class OutlierDispatcher(Protocol):
    """The seam through which a fired outlier alert reaches the Plan 10 notifier.

    Implementations enqueue an ``agent_outlier_alert`` event for the tenant; the
    notification-dispatcher resolves the tenant's channels / Tenant-Admin
    preferences and sends. Tests inject a fake to assert the enqueue without a
    live broker. Returns True iff the event was accepted.
    """

    async def dispatch(self, event: dict[str, object]) -> bool: ...  # pragma: no cover - protocol


class CeleryOutlierDispatcher:
    """Default dispatcher: enqueue the event onto the Plan 10 dispatcher lane.

    Goes THROUGH the Plan 10 notification system — it produces the
    ``notification_dispatcher.dispatch_event`` task by name (the api-server never
    imports the dispatcher package). The dispatcher then fans the event out to
    the tenant's Tenant Admins' subscribed channels.
    """

    async def dispatch(self, event: dict[str, object]) -> bool:
        # Imported lazily so importing this module does not pull the Celery
        # producer (and its broker config) into every consumer.
        from api_server.celery_client import enqueue_event_dispatch

        return await enqueue_event_dispatch(event)


@dataclass(frozen=True)
class OutlierFiring:
    """The record of one rule that fired during an evaluation pass.

    ``flagged_count`` is how many agents the rule flagged; ``dispatched`` is
    True when the alert(s) were handed to the Plan 10 notifier.
    """

    rule_id: UUID
    rule_name: str
    metric: str
    flagged_count: int
    dispatched: bool


@dataclass
class OutlierEvaluationResult:
    """The outcome of evaluating all of a tenant's outlier rules once."""

    tenant_id: UUID
    fired: list[OutlierFiring] = field(default_factory=list)
    # Rules that flagged an outlier but were SUPPRESSED by the debounce
    # (already fired within the current window). Surfaced for observability.
    suppressed_rule_ids: list[UUID] = field(default_factory=list)
    evaluated: int = 0
    # All decisions (including no-flag) for observability / a future dashboard.
    decisions: dict[UUID, OutlierDecision] = field(default_factory=dict)


def _build_alert_event(
    rule: OutlierAlertRule,
    *,
    decision: OutlierDecision,
) -> dict[str, object]:
    """Build the JSON-safe ``agent_outlier_alert`` event payload for the notifier.

    Carries ONLY non-sensitive metadata into the template context (the rule
    name, the metric, the flagged agents' names/roles + values) — never any task
    output or content. One event per firing rule listing every breaching agent;
    the template summarises the worst.
    """
    flagged = [
        {
            "agent_id": str(f.agent_id) if f.agent_id is not None else None,
            "agent_name": f.agent_name,
            "agent_role": f.agent_role,
            "run_count": f.run_count,
            "value": float(f.value),
            "bound": float(f.bound),
        }
        for f in decision.flagged
    ]
    worst = flagged[0] if flagged else {}
    return {
        "event_type": AGENT_OUTLIER_ALERT_EVENT_TYPE,
        "tenant_id": str(rule.tenant_id),
        "context": {
            "rule_name": rule.name,
            "metric": rule.metric,
            "flagged_count": len(flagged),
            "agent_name": worst.get("agent_name") or "",
            "agent_role": worst.get("agent_role") or "",
            "value": worst.get("value"),
            "bound": worst.get("bound"),
            "window_days": rule.window_days,
            "agents": flagged,
        },
    }


# =============================================================================
# DB-backed aggregation (tenant-scoped) — the per-agent metrics over a window
# =============================================================================
def _succeeded_flag() -> ColumnElement[int]:
    return case((Execution.status == _DONE, 1), else_=0)


def _duration_ms() -> ColumnElement[float]:
    delta = func.extract("epoch", Execution.completed_at - Execution.started_at) * 1000.0
    return case((Execution.completed_at.isnot(None) & Execution.started_at.isnot(None), delta))


async def load_agent_metrics(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    window_days: int,
    now: datetime | None = None,
) -> list[AgentMetric]:
    """Aggregate this tenant's executions per agent over the trailing window.

    One :class:`AgentMetric` per agent that ran at least once in the window:
    run count, success rate (done/total), mean cost (USD) and mean finished-run
    duration (ms). Tenant-scoped (RLS) with a defence-in-depth ``tenant_id ==``
    predicate; the :class:`~api_server.db.domain.Agent` table is left-joined for
    the name / role labels.
    """
    from api_server.db.domain import Agent

    now = now or datetime.now(tz=UTC)
    since = now - timedelta(days=window_days)
    success = _succeeded_flag()
    dur = _duration_ms()
    rows = (
        await session.execute(
            select(
                Execution.agent_id,
                Agent.name,
                Agent.role,
                func.count(),
                func.coalesce(func.sum(success), 0),
                func.avg(Execution.total_cost_usd),
                func.avg(dur),
            )
            .select_from(Execution)
            .outerjoin(Agent, Agent.id == Execution.agent_id)
            .where(Execution.tenant_id == tenant_id, Execution.created_at >= since)
            .group_by(Execution.agent_id, Agent.name, Agent.role)
        )
    ).all()
    metrics: list[AgentMetric] = []
    for agent_id, name, role, raw_run_count, succeeded, mean_cost, mean_dur in rows:
        run_count = int(raw_run_count)
        metrics.append(
            AgentMetric(
                agent_id=agent_id,
                agent_name=name,
                agent_role=role,
                run_count=run_count,
                success_rate=_rate(int(succeeded), run_count),
                mean_cost=_to_decimal(mean_cost),
                mean_latency_ms=_to_decimal(mean_dur),
            )
        )
    return metrics


async def evaluate_outlier_rules(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    dispatcher: OutlierDispatcher | None = None,
    now: datetime | None = None,
) -> OutlierEvaluationResult:
    """Evaluate all of a tenant's enabled outlier rules once and fire any breach.

    Runs on the caller's TENANT-SCOPED RLS session (the per-agent aggregation,
    the rule load/update and the alert are all scoped to ``tenant_id``), so
    tenant A's agents can never alert / debounce tenant B. For each enabled,
    live rule:

      1. Aggregate the tenant's executions per agent over the rule's window.
      2. Run the PURE :func:`detect_outliers`.
      3. If no agent is flagged → nothing fires.
      4. If flagged but a prior alert is still within the debounce window
         (``last_fired_at`` + ``window_days`` > now) → suppress (no spam).
      5. Otherwise dispatch ONE ``agent_outlier_alert`` event (listing the
         flagged agents) through the Plan 10 notifier and stamp
         ``last_fired_at``.

    The caller owns the transaction — the ``last_fired_at`` update is flushed,
    not committed. Returns the per-rule outcome.
    """
    now = now or datetime.now(tz=UTC)
    dispatcher = dispatcher or CeleryOutlierDispatcher()

    rules = (
        (
            await session.execute(
                select(OutlierAlertRule).where(
                    OutlierAlertRule.tenant_id == tenant_id,
                    OutlierAlertRule.enabled.is_(True),
                    OutlierAlertRule.deleted_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    result = OutlierEvaluationResult(tenant_id=tenant_id, evaluated=len(rules))

    for rule in rules:
        metric = OutlierMetric(rule.metric)
        metrics = await load_agent_metrics(
            session, tenant_id=tenant_id, window_days=rule.window_days, now=now
        )
        decision = detect_outliers(
            metrics,
            metric=metric,
            min_runs=rule.min_runs,
            success_rate_floor=rule.success_rate_floor,
            stddev_k=rule.stddev_k,
        )
        result.decisions[rule.id] = decision

        if not decision.flagged:
            continue
        if _is_debounced(rule, now=now):
            result.suppressed_rule_ids.append(rule.id)
            _log.info(
                "agent_outlier_alert.debounced",
                tenant_id=str(tenant_id),
                rule_id=str(rule.id),
                flagged=len(decision.flagged),
            )
            continue

        # Stamp the debounce anchor BEFORE awaiting the dispatch so a concurrent
        # evaluation in the same window cannot double-fire (the row is locked in
        # this transaction).
        rule.last_fired_at = now
        await session.flush()

        dispatched = await dispatcher.dispatch(_build_alert_event(rule, decision=decision))
        result.fired.append(
            OutlierFiring(
                rule_id=rule.id,
                rule_name=rule.name,
                metric=rule.metric,
                flagged_count=len(decision.flagged),
                dispatched=dispatched,
            )
        )
        _log.info(
            "agent_outlier_alert.fired",
            tenant_id=str(tenant_id),
            rule_id=str(rule.id),
            rule_name=rule.name,
            metric=rule.metric,
            flagged=len(decision.flagged),
            dispatched=dispatched,
        )

    return result


async def maybe_alert_outliers(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    dispatcher: OutlierDispatcher | None = None,
    now: datetime | None = None,
) -> OutlierEvaluationResult:
    """Best-effort wrapper over :func:`evaluate_outlier_rules` that never raises.

    The seam a periodic sweep / a stats-refresh host can call: outlier alerting
    is observability layered on top, so a failure here must not break the host.
    """
    try:
        return await evaluate_outlier_rules(
            session, tenant_id=tenant_id, dispatcher=dispatcher, now=now
        )
    except Exception as exc:  # pragma: no cover - defensive best-effort
        _log.warning(
            "agent_outlier_alert.evaluation_failed",
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        return OutlierEvaluationResult(tenant_id=tenant_id)


# =============================================================================
# Debounce + small pure helpers
# =============================================================================
def _is_debounced(rule: OutlierAlertRule, *, now: datetime) -> bool:
    """True when the rule already fired within its current window.

    The debounce window equals the rule's own ``window_days`` measured from
    ``last_fired_at``: a rule may fire again only once a full window has elapsed
    since its last alert, so a sustained outlier yields at most one alert per
    rule per window.
    """
    if rule.last_fired_at is None:
        return False
    last = rule.last_fired_at
    if last.tzinfo is None:  # defensive: treat a naive timestamp as UTC
        last = last.replace(tzinfo=UTC)
    return now - last < timedelta(days=rule.window_days)


def _rate(succeeded: int, total: int) -> Decimal | None:
    """Success fraction in [0, 1]; None when no runs (undefined, not zero)."""
    if total <= 0:
        return None
    return (Decimal(succeeded) / Decimal(total)).quantize(_RATE_QUANTUM, rounding=ROUND_HALF_UP)


def _to_decimal(value: object) -> Decimal | None:
    """Coerce a SQL aggregate (Decimal / float / None) to a Decimal or None."""
    if value is None:
        return None
    return Decimal(str(value))


__all__ = [
    "AGENT_OUTLIER_ALERT_EVENT_TYPE",
    "AgentMetric",
    "CeleryOutlierDispatcher",
    "FlaggedAgent",
    "OutlierDecision",
    "OutlierDispatcher",
    "OutlierEvaluationResult",
    "OutlierFiring",
    "detect_outliers",
    "evaluate_outlier_rules",
    "load_agent_metrics",
    "maybe_alert_outliers",
]
