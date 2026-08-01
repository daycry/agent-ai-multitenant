"""Budget consumption + threshold-alert evaluation (Plan 11.1 task_11_1_05).

The trigger side of tenant/project budgets. A budget (on ``organizations``
for the tenant scope, on ``projects`` for each project scope) caps spend over
a recurring period (``budgets.period``). This module:

  1. **Computes consumption** — sums the CANONICAL-USD cost
     (``executions.total_cost_usd``, Plan 11 task_11_13) of the scope's
     executions whose date falls in the active budget window ``[start, end)``,
     converts the (own-currency) budget cap INTO USD (``fx.convert_to_usd``)
     so the comparison is apples-to-apples, and derives the percent used.
  2. **Evaluates thresholds** — for each platform-global threshold
     (``[80, 90, 100]`` by default, ``platform_settings``) that the percent
     used has crossed AND that has not already fired this period, dispatches
     ONE ``budget_alert`` event via the Plan 10 notifier to the tenant's
     Tenant Admins and records the firing in ``budget_alert_states`` (the
     debounce: one alert per threshold per period per scope).
  3. **Summarises** — :func:`tenant_budget_summary` builds the structured
     payload the personal-assistant ``tenant_budget_status`` tool returns
     (replacing the Plan 10 stub) with real spend / % / period / status.

Multi-tenancy (NON-NEGOTIABLE): everything runs on the caller's TENANT-SCOPED
RLS session. The org row (RLS: only the tenant's own), the tenant's projects,
the tenant's executions, and the debounce state are ALL filtered to
``tenant_id``, so tenant A's spend can NEVER alert / debounce tenant B. USD is
canonical; the cap is converted to USD using the rate of the period-start date
(a single, period-stable rate, not a per-run rate — the cap is a property of
the period, not of any one run).

``now`` is injectable so the period + the debounce are deterministic in tests,
and the dispatcher is a Protocol seam so tests assert the enqueue without a
live broker. Auto-pause at 100% is task_11_1_06 (this task only ALERTS).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from decimal import ROUND_HALF_UP, Decimal
from typing import Protocol
from uuid import UUID

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from api_server.budgets.human_cost import compute_human_cost_usd
from api_server.budgets.period import BudgetPeriodWindow, current_budget_period
from api_server.db.budget_alert_state import BudgetAlertState, BudgetScope
from api_server.db.domain import Execution, Project, Task
from api_server.db.models import Organization
from api_server.db.platform_settings import get_budget_alert_thresholds
from api_server.fx import UnknownCurrencyError, convert_to_usd

_log = structlog.get_logger("api_server.budgets.consumption")

# The notification event_type the Plan 10 dispatcher maps a fired budget alert
# to (registered in the dispatcher's EVENT_REGISTRY + builtin templates as
# ``budget_alert``). A named constant, not an inline literal.
BUDGET_ALERT_EVENT_TYPE = "budget_alert"

# Percent-used is rounded to a stable quantum for display + the threshold
# comparison (a percentage to one decimal is plenty for "80% crossed").
_PERCENT_QUANTUM = Decimal("0.1")


# =============================================================================
# Consumption snapshot (per scope)
# =============================================================================
@dataclass(frozen=True)
class BudgetConsumption:
    """One scope's spend vs its budget over the active period.

    ``budget_usd`` is the cap converted to canonical USD (``None`` when the
    cap currency has no FX rate — the scope is then reported but not alerted).
    ``percent_used`` is ``spend_usd / budget_usd * 100`` (``None`` when the
    budget is unknown / zero). ``crossed_thresholds`` are the configured
    thresholds the percent used currently meets-or-exceeds (ascending).

    ``spend_usd`` is the TOTAL that the thresholds / auto-pause compare against
    the cap. It is ``ai_spend_usd + human_spend_usd``, where ``human_spend_usd``
    is the project's human cost FOLDED INTO the budget only when the scope opted
    in via ``projects.budget_includes_human_cost`` (Plan 16 task_16_12) — 0
    otherwise, so by default ``spend_usd == ai_spend_usd`` (unchanged
    behaviour). ``ai_spend_usd`` / ``human_spend_usd`` are surfaced separately
    so the dashboard / assistant can segment the two.
    """

    scope: BudgetScope
    project_id: UUID | None
    project_name: str | None
    period: str
    window: BudgetPeriodWindow
    budget_amount: Decimal
    budget_currency: str
    budget_usd: Decimal | None
    spend_usd: Decimal
    ai_spend_usd: Decimal
    human_spend_usd: Decimal
    percent_used: Decimal | None
    crossed_thresholds: tuple[int, ...]

    @property
    def is_over_budget(self) -> bool:
        """True when spend has reached/exceeded 100% of the (USD) budget."""
        return self.percent_used is not None and self.percent_used >= Decimal(100)


@dataclass(frozen=True)
class BudgetFiring:
    """One threshold that fired during an evaluation pass (for assertions)."""

    scope: BudgetScope
    project_id: UUID | None
    threshold: int
    percent_used: Decimal | None
    dispatched: bool


@dataclass
class BudgetEvaluationResult:
    """The outcome of evaluating all of a tenant's budgets once."""

    tenant_id: UUID
    consumptions: list[BudgetConsumption] = field(default_factory=list)
    fired: list[BudgetFiring] = field(default_factory=list)
    # Thresholds that were crossed but SUPPRESSED by the debounce (already
    # fired this period). Surfaced for observability.
    suppressed: list[BudgetFiring] = field(default_factory=list)


# =============================================================================
# Dispatch seam (reuses the Plan 10 / guardrail-alert pattern)
# =============================================================================
class BudgetAlertDispatcher(Protocol):
    """The seam through which a fired budget alert reaches the Plan 10 notifier.

    Implementations enqueue a ``budget_alert`` event for the tenant; the
    notification-dispatcher resolves the tenant's channels / Tenant-Admin
    preferences and sends. Tests inject a fake to assert the enqueue without a
    live broker. Returns True iff the event was accepted.
    """

    async def dispatch(self, event: dict[str, object]) -> bool: ...  # pragma: no cover - protocol


class CeleryBudgetAlertDispatcher:
    """Default dispatcher: enqueue the event onto the Plan 10 dispatcher lane.

    Goes THROUGH the Plan 10 notification system — it produces the
    ``notification_dispatcher.dispatch_event`` task by name (the api-server
    never imports the dispatcher package). The dispatcher then fans the event
    out to the tenant's Tenant Admins' subscribed channels.
    """

    async def dispatch(self, event: dict[str, object]) -> bool:
        # Imported lazily so importing this module does not pull the Celery
        # producer (and its broker config) into every consumer.
        from api_server.celery_client import enqueue_event_dispatch

        return await enqueue_event_dispatch(event)


# =============================================================================
# Small pure helpers
# =============================================================================
def _percent_used(spend_usd: Decimal, budget_usd: Decimal | None) -> Decimal | None:
    """Spend as a percent of the USD budget, or None when the budget is
    unknown / non-positive (a 0 budget has no meaningful percentage)."""
    if budget_usd is None or budget_usd <= 0:
        return None
    return ((spend_usd / budget_usd) * Decimal(100)).quantize(
        _PERCENT_QUANTUM, rounding=ROUND_HALF_UP
    )


def _crossed(percent_used: Decimal | None, thresholds: list[int]) -> tuple[int, ...]:
    """The thresholds the percent used meets-or-exceeds (ascending)."""
    if percent_used is None:
        return ()
    return tuple(t for t in sorted(thresholds) if percent_used >= Decimal(t))


def _status(percent_used: Decimal | None, thresholds: list[int]) -> str:
    """A coarse status label for the assistant / UI.

    ``ok`` below every threshold, ``warning`` past a sub-100 threshold,
    ``over_budget`` at/above 100%, ``unknown`` when there is no usable budget.
    """
    if percent_used is None:
        return "unknown"
    if percent_used >= Decimal(100):
        return "over_budget"
    crossed = _crossed(percent_used, thresholds)
    return "warning" if crossed else "ok"


# =============================================================================
# Consumption computation (tenant-scoped)
# =============================================================================
def _utc_midnight(day: date) -> datetime:
    """El instante UTC en que empieza ``day``.

    El corte del período se define en UTC EXPLÍCITAMENTE, y no se deja al azar de
    la zona horaria de la sesión de PostgreSQL. Antes el predicado era
    ``date(executions.created_at)``, y `date(timestamptz)` se evalúa en la zona
    de la sesión: en un despliegue cuyo PostgreSQL no estuviese en UTC, un gasto
    de las 02:00 UTC del día 1 se contabilizaba en el período ANTERIOR. UTC es la
    zona en la que ya están definidos el resto de instantes de la plataforma.
    """
    return datetime.combine(day, time.min, tzinfo=UTC)


def spend_in_window_stmt(
    *,
    tenant_id: UUID,
    window: BudgetPeriodWindow,
    project_id: UUID | None,
) -> Select[tuple[Decimal]]:
    """El SELECT de gasto del scope en ``[start, end)``, sin ejecutarlo.

    Se expone separado del ``await`` para que un test pueda hacerle ``EXPLAIN``:
    lo que se afirma de esta consulta —que el rango de ``created_at`` llega al
    *Index Cond* de ``ix_executions_tenant_created_at`` y no a un *Filter*— no se
    puede comprobar mirando el resultado, solo el plan.

    El predicado es un RANGO sobre la columna, no `date(columna)`: envolver la
    columna en una función la vuelve no-sargable y PostgreSQL no puede usar el
    índice para acotar (no existe índice sobre esa expresión). Con el índice
    `(tenant_id, created_at)` que creó la migración 0126, la igualdad de tenant
    y el rango de fecha se resuelven ambos dentro del índice.
    """
    stmt = (
        select(func.coalesce(func.sum(Execution.total_cost_usd), 0))
        .select_from(Execution)
        .where(
            Execution.tenant_id == tenant_id,
            Execution.created_at >= _utc_midnight(window.start),
            Execution.created_at < _utc_midnight(window.end),
        )
    )
    if project_id is not None:
        stmt = stmt.join(Task, Task.id == Execution.task_id).where(Task.project_id == project_id)
    return stmt


async def _spend_usd_in_window(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    window: BudgetPeriodWindow,
    project_id: UUID | None,
) -> Decimal:
    """Sum the canonical-USD cost of the scope's executions in ``[start, end)``.

    Tenant-scoped (RLS) + a defence-in-depth ``tenant_id ==`` predicate. The
    window is a half-open TIMESTAMPTZ range in UTC (see
    :func:`spend_in_window_stmt`). For a project scope the executions are joined
    through their task's ``project_id``.
    """
    stmt = spend_in_window_stmt(tenant_id=tenant_id, window=window, project_id=project_id)
    total = (await session.execute(stmt)).scalar_one()
    return Decimal(str(total))


async def _budget_usd(
    session: AsyncSession,
    *,
    amount: Decimal,
    currency: str,
    on_date: date,
) -> Decimal | None:
    """Convert a budget cap into canonical USD on ``on_date`` (period start).

    Returns None (rather than raising) when the cap currency has no FX rate —
    the scope is then reported with an ``unknown`` status but never alerted,
    because we cannot honestly compare an unconvertible cap against USD spend.
    """
    try:
        return await convert_to_usd(session, amount, currency, on_date)
    except UnknownCurrencyError:
        _log.warning(
            "budget.cap_unconvertible",
            currency=currency,
            on_date=on_date.isoformat(),
        )
        return None


async def _consumption_for_scope(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    scope: BudgetScope,
    project_id: UUID | None,
    project_name: str | None,
    amount: Decimal,
    currency: str,
    period: str,
    start_day: int | None,
    length_days: int | None,
    on_date: date,
    thresholds: list[int],
    human_cost_project_ids: frozenset[UUID],
) -> BudgetConsumption | None:
    """Build the consumption snapshot for one scope (tenant or one project).

    Returns None when the budget config is malformed (an unknown period); a
    malformed config is logged and skipped rather than crashing the whole
    evaluation (the other scopes still evaluate).

    Human cost (Plan 16 task_16_12) is FOLDED into ``spend_usd`` only for the
    opted-in projects in ``human_cost_project_ids``:
      - PROJECT scope: the project's own human cost iff it is in the set;
      - TENANT scope: the SUM of the human cost of every opted-in project (a
        tenant budget that includes human cost counts the human spend of the
        projects that opted in, never the AI-only projects' human spend).
    When nothing is folded ``human_spend_usd`` is 0 and ``spend_usd`` equals
    the AI spend (unchanged behaviour)."""
    try:
        window = current_budget_period(
            period, start_day=start_day, length_days=length_days, on_date=on_date
        )
    except ValueError as exc:  # InvalidBudgetPeriodError is a ValueError
        _log.warning(
            "budget.invalid_period",
            tenant_id=str(tenant_id),
            scope=scope.value,
            project_id=str(project_id) if project_id else None,
            period=period,
            error=str(exc),
        )
        return None

    ai_spend_usd = await _spend_usd_in_window(
        session, tenant_id=tenant_id, window=window, project_id=project_id
    )
    human_spend_usd = await _folded_human_cost(
        session,
        tenant_id=tenant_id,
        scope=scope,
        project_id=project_id,
        window=window,
        human_cost_project_ids=human_cost_project_ids,
    )
    spend_usd = ai_spend_usd + human_spend_usd
    budget_usd = await _budget_usd(session, amount=amount, currency=currency, on_date=window.start)
    percent = _percent_used(spend_usd, budget_usd)
    return BudgetConsumption(
        scope=scope,
        project_id=project_id,
        project_name=project_name,
        period=period,
        window=window,
        budget_amount=amount,
        budget_currency=currency.upper(),
        budget_usd=budget_usd,
        spend_usd=spend_usd,
        ai_spend_usd=ai_spend_usd,
        human_spend_usd=human_spend_usd,
        percent_used=percent,
        crossed_thresholds=_crossed(percent, thresholds),
    )


async def _folded_human_cost(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    scope: BudgetScope,
    project_id: UUID | None,
    window: BudgetPeriodWindow,
    human_cost_project_ids: frozenset[UUID],
) -> Decimal:
    """The human cost (USD) to fold into this scope's budget for the window.

    PROJECT scope: the project's own human cost iff it opted in. TENANT scope:
    the sum of every opted-in project's human cost. Zero when nothing opted in
    (the default — AI-only budget)."""
    if not human_cost_project_ids:
        return Decimal("0")
    if scope is BudgetScope.PROJECT:
        if project_id is None or project_id not in human_cost_project_ids:
            return Decimal("0")
        scoped = await compute_human_cost_usd(
            session,
            tenant_id=tenant_id,
            project_id=project_id,
            window_start=window.start,
            window_end=window.end,
        )
        return scoped.human_cost_usd
    # TENANT scope: sum across the opted-in projects.
    total = Decimal("0")
    for pid in human_cost_project_ids:
        scoped = await compute_human_cost_usd(
            session,
            tenant_id=tenant_id,
            project_id=pid,
            window_start=window.start,
            window_end=window.end,
        )
        total += scoped.human_cost_usd
    return total


async def compute_budget_consumption(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    on_date: date | None = None,
    thresholds: list[int] | None = None,
) -> list[BudgetConsumption]:
    """Compute the spend-vs-budget snapshot for every configured budget.

    Returns one :class:`BudgetConsumption` for the tenant-wide budget (if the
    organization has one) plus one per project that has a budget. A scope with
    no configured budget (no amount/currency/period) is skipped. Runs on the
    caller's TENANT-SCOPED RLS session; ``on_date`` (default: today, UTC)
    selects the active period.
    """
    on_date = on_date or datetime.now(tz=UTC).date()
    if thresholds is None:
        thresholds = await get_budget_alert_thresholds(session)

    # The set of projects that fold human cost into the budget (Plan 16
    # task_16_12). Computed once: the PROJECT scope folds its own when present,
    # the TENANT scope folds the sum across the set.
    human_cost_project_ids = await _human_cost_project_ids(session, tenant_id=tenant_id)

    consumptions: list[BudgetConsumption] = []

    # --- Tenant-wide budget (organizations; RLS → only this tenant's row) ---
    org = await session.get(Organization, tenant_id)
    if org is not None and _has_budget(
        org.tenant_budget_amount, org.tenant_budget_currency, org.tenant_budget_period
    ):
        snap = await _consumption_for_scope(
            session,
            tenant_id=tenant_id,
            scope=BudgetScope.TENANT,
            project_id=None,
            project_name=None,
            amount=org.tenant_budget_amount,  # type: ignore[arg-type]
            currency=org.tenant_budget_currency,  # type: ignore[arg-type]
            period=org.tenant_budget_period,  # type: ignore[arg-type]
            start_day=org.tenant_budget_period_start_day,
            length_days=org.tenant_budget_period_length_days,
            on_date=on_date,
            thresholds=thresholds,
            human_cost_project_ids=human_cost_project_ids,
        )
        if snap is not None:
            consumptions.append(snap)

    # --- Per-project budgets (projects; tenant-scoped) ----------------------
    projects = (
        (
            await session.execute(
                select(Project).where(
                    Project.tenant_id == tenant_id,
                    Project.deleted_at.is_(None),
                    Project.budget_amount.is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for project in projects:
        if not _has_budget(project.budget_amount, project.budget_currency, project.budget_period):
            continue
        snap = await _consumption_for_scope(
            session,
            tenant_id=tenant_id,
            scope=BudgetScope.PROJECT,
            project_id=project.id,
            project_name=project.name,
            amount=project.budget_amount,  # type: ignore[arg-type]
            currency=project.budget_currency,  # type: ignore[arg-type]
            period=project.budget_period,  # type: ignore[arg-type]
            start_day=project.budget_period_start_day,
            length_days=project.budget_period_length_days,
            on_date=on_date,
            thresholds=thresholds,
            human_cost_project_ids=human_cost_project_ids,
        )
        if snap is not None:
            consumptions.append(snap)

    return consumptions


async def _human_cost_project_ids(session: AsyncSession, *, tenant_id: UUID) -> frozenset[UUID]:
    """The live projects of the tenant that opted into folding human cost.

    Tenant-scoped (RLS) + defence-in-depth ``tenant_id ==``. A project folds its
    human cost into the budget when ``budget_includes_human_cost`` is true (Plan
    16 task_16_12); the default (false) keeps the AI-only budget."""
    rows = (
        (
            await session.execute(
                select(Project.id).where(
                    Project.tenant_id == tenant_id,
                    Project.deleted_at.is_(None),
                    Project.budget_includes_human_cost.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return frozenset(rows)


def _has_budget(amount: Decimal | None, currency: str | None, period: str | None) -> bool:
    """A scope has a usable budget only when amount + currency + period are all
    set and the amount is strictly positive (a 0 cap is "no budget")."""
    return amount is not None and amount > 0 and bool(currency) and bool(period)


# =============================================================================
# Alert evaluation (tenant-scoped) — fire newly-crossed thresholds, debounced
# =============================================================================
def _build_alert_event(
    *,
    tenant_id: UUID,
    consumption: BudgetConsumption,
    threshold: int,
) -> dict[str, object]:
    """Build the JSON-safe ``budget_alert`` event payload for the notifier.

    Carries only non-sensitive budget metadata (the scope, the crossed
    threshold, the percent used, spend + cap in USD, the period). Uses the
    existing ``budget_alert`` template's ``plan_name`` / ``threshold`` /
    ``spent`` keys for back-compatibility, plus richer scope fields.
    """
    scope_label = (
        consumption.project_name or "project"
        if consumption.scope is BudgetScope.PROJECT
        else "tenant"
    )
    return {
        "event_type": BUDGET_ALERT_EVENT_TYPE,
        "tenant_id": str(tenant_id),
        "context": {
            # Back-compat keys the builtin template already renders.
            "plan_name": scope_label,
            "threshold": threshold,
            "spent": f"{consumption.spend_usd} USD",
            # Richer, scope-aware fields.
            "scope": consumption.scope.value,
            "project_id": (
                str(consumption.project_id) if consumption.project_id is not None else None
            ),
            "project_name": consumption.project_name,
            "percent_used": (
                float(consumption.percent_used) if consumption.percent_used is not None else None
            ),
            "spend_usd": float(consumption.spend_usd),
            "budget_usd": (
                float(consumption.budget_usd) if consumption.budget_usd is not None else None
            ),
            "budget_amount": float(consumption.budget_amount),
            "budget_currency": consumption.budget_currency,
            "period": consumption.period,
            "period_start": consumption.window.start.isoformat(),
            "period_end": consumption.window.end.isoformat(),
        },
    }


async def _already_fired(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    scope: BudgetScope,
    project_id: UUID | None,
    period_start: date,
) -> set[int]:
    """The thresholds already fired for this scope's current period (debounce).

    Tenant-scoped + a defence-in-depth ``tenant_id ==`` predicate. project_id
    is matched with ``IS NULL`` for the tenant scope (a NULL never equals a
    value), so a tenant-scope lookup never picks up a project row and vice
    versa."""
    stmt = select(BudgetAlertState.threshold).where(
        BudgetAlertState.tenant_id == tenant_id,
        BudgetAlertState.scope == scope.value,
        BudgetAlertState.period_start == period_start,
    )
    stmt = stmt.where(
        BudgetAlertState.project_id == project_id
        if project_id is not None
        else BudgetAlertState.project_id.is_(None)
    )
    return {int(t) for t in (await session.execute(stmt)).scalars().all()}


async def evaluate_budget_alerts(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    dispatcher: BudgetAlertDispatcher | None = None,
    now: datetime | None = None,
) -> BudgetEvaluationResult:
    """Evaluate every configured budget once and fire any newly-crossed threshold.

    Runs on the caller's TENANT-SCOPED RLS session (the org/project read, the
    spend sum, the debounce state and the alert are ALL scoped to ``tenant_id``),
    so tenant A's spend can never alert / debounce tenant B. For each scope:

      1. Compute the consumption snapshot (spend USD vs USD-converted cap).
      2. For each configured threshold the percent used crosses AND that has no
         ``budget_alert_states`` row for the current period → dispatch ONE
         ``budget_alert`` event via the Plan 10 notifier and INSERT the
         debounce row (one alert per threshold per period per scope).

    The caller owns the transaction — the debounce inserts are flushed, not
    committed (they commit atomically with the caller). Returns the per-scope
    consumption + the fired / suppressed thresholds.
    """
    now = now or datetime.now(tz=UTC)
    dispatcher = dispatcher or CeleryBudgetAlertDispatcher()

    consumptions = await compute_budget_consumption(
        session, tenant_id=tenant_id, on_date=now.date()
    )
    result = BudgetEvaluationResult(tenant_id=tenant_id, consumptions=consumptions)

    for consumption in consumptions:
        if not consumption.crossed_thresholds:
            continue
        fired_thresholds = await _already_fired(
            session,
            tenant_id=tenant_id,
            scope=consumption.scope,
            project_id=consumption.project_id,
            period_start=consumption.window.start,
        )
        for threshold in consumption.crossed_thresholds:
            firing = BudgetFiring(
                scope=consumption.scope,
                project_id=consumption.project_id,
                threshold=threshold,
                percent_used=consumption.percent_used,
                dispatched=False,
            )
            if threshold in fired_thresholds:
                result.suppressed.append(firing)
                continue

            # Record the debounce row BEFORE awaiting the dispatch so a
            # concurrent evaluation in the same period cannot double-fire (the
            # unique constraint is the hard guarantee; ON CONFLICT DO NOTHING
            # keeps a racing insert from raising).
            await session.execute(
                pg_insert(BudgetAlertState)
                .values(
                    tenant_id=tenant_id,
                    scope=consumption.scope.value,
                    project_id=consumption.project_id,
                    period_start=consumption.window.start,
                    threshold=threshold,
                )
                .on_conflict_do_nothing(constraint="uq_budget_alert_states_debounce")
            )
            await session.flush()

            dispatched = await dispatcher.dispatch(
                _build_alert_event(
                    tenant_id=tenant_id, consumption=consumption, threshold=threshold
                )
            )
            result.fired.append(
                BudgetFiring(
                    scope=consumption.scope,
                    project_id=consumption.project_id,
                    threshold=threshold,
                    percent_used=consumption.percent_used,
                    dispatched=dispatched,
                )
            )
            _log.info(
                "budget_alert.fired",
                tenant_id=str(tenant_id),
                scope=consumption.scope.value,
                project_id=str(consumption.project_id) if consumption.project_id else None,
                threshold=threshold,
                percent_used=(
                    str(consumption.percent_used) if consumption.percent_used is not None else None
                ),
                dispatched=dispatched,
            )

    return result


async def maybe_alert_budgets(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    dispatcher: BudgetAlertDispatcher | None = None,
    now: datetime | None = None,
) -> BudgetEvaluationResult:
    """Best-effort wrapper over :func:`evaluate_budget_alerts` that never raises.

    The seam a periodic sweep / an execution-finished host can call: budget
    alerting is observability layered on top, so a failure here must not break
    the host (the spend record is already persisted)."""
    try:
        return await evaluate_budget_alerts(
            session, tenant_id=tenant_id, dispatcher=dispatcher, now=now
        )
    except Exception as exc:  # pragma: no cover - defensive best-effort
        _log.warning(
            "budget_alert.evaluation_failed",
            tenant_id=str(tenant_id),
            error=str(exc),
        )
        return BudgetEvaluationResult(tenant_id=tenant_id)


# =============================================================================
# Personal-assistant summary (fills the Plan 10 tenant_budget_status stub)
# =============================================================================
def _consumption_to_dict(
    consumption: BudgetConsumption, thresholds: list[int]
) -> dict[str, object]:
    """JSON-safe view of one scope's consumption for the assistant tool."""
    return {
        "scope": consumption.scope.value,
        "project_id": (str(consumption.project_id) if consumption.project_id else None),
        "project_name": consumption.project_name,
        "budget_amount": str(consumption.budget_amount),
        "budget_currency": consumption.budget_currency,
        "budget_usd": (str(consumption.budget_usd) if consumption.budget_usd is not None else None),
        "spend_usd": str(consumption.spend_usd),
        # Segmentation (Plan 16 task_16_12): AI vs human spend that make up
        # spend_usd. human_spend_usd is 0 unless the scope folds human cost.
        "ai_spend_usd": str(consumption.ai_spend_usd),
        "human_spend_usd": str(consumption.human_spend_usd),
        "percent_used": (
            float(consumption.percent_used) if consumption.percent_used is not None else None
        ),
        "status": _status(consumption.percent_used, thresholds),
        "over_budget": consumption.is_over_budget,
        "period": consumption.period,
        "period_start": consumption.window.start.isoformat(),
        "period_end": consumption.window.end.isoformat(),
    }


async def tenant_budget_summary(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    on_date: date | None = None,
) -> dict[str, object]:
    """Build the real ``tenant_budget_status`` payload (Plan 11.1 task_11_1_05).

    Replaces the Plan 10 typed placeholder with live data: the tenant-wide
    budget consumption + each project's, all in canonical USD with the percent
    used, the active period and a coarse status. When NO budget is configured
    anywhere, ``available`` is False with a clear reason (so the assistant says
    "no budget configured" rather than fabricating numbers). Tenant-scoped
    (RLS): only this tenant's budgets / spend are ever seen.
    """
    thresholds = await get_budget_alert_thresholds(session)
    consumptions = await compute_budget_consumption(
        session, tenant_id=tenant_id, on_date=on_date, thresholds=thresholds
    )
    if not consumptions:
        return {
            "available": False,
            "reason": "no_budget_configured",
            "message": (
                "No hay ningún presupuesto configurado para este tenant ni " "para sus proyectos."
            ),
        }

    tenant_scope = next((c for c in consumptions if c.scope is BudgetScope.TENANT), None)
    project_scopes = [c for c in consumptions if c.scope is BudgetScope.PROJECT]
    return {
        "available": True,
        "currency": "USD",
        "alert_thresholds": list(thresholds),
        "tenant": (
            _consumption_to_dict(tenant_scope, thresholds) if tenant_scope is not None else None
        ),
        "projects": [_consumption_to_dict(c, thresholds) for c in project_scopes],
    }


__all__ = [
    "BUDGET_ALERT_EVENT_TYPE",
    "BudgetAlertDispatcher",
    "BudgetConsumption",
    "BudgetEvaluationResult",
    "BudgetFiring",
    "CeleryBudgetAlertDispatcher",
    "compute_budget_consumption",
    "evaluate_budget_alerts",
    "maybe_alert_budgets",
    "tenant_budget_summary",
]
