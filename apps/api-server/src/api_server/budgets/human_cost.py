"""Human cost imputation — rate * hours from work sessions (Plan 16 task_16_12).

Human tasks (``agent_type='human'``) record their work in
:class:`~api_server.db.domain.HumanWorkSession` rows (the Execution-equivalent
audit trail, task_16_03) rather than in ``executions``. This module turns those
sessions into a CANONICAL-USD human cost, scoped to a tenant / a project / a
plan over a date window:

    human_cost(session) = hours_logged * hourly_rate   (converted to USD)

where the rate + currency come from the task's assigned Human Agent's
:class:`~api_server.db.domain.HumanAgentConfig` (``hourly_rate`` /
``hourly_rate_currency``). When the agent has no rate configured we fall back to
:data:`~api_server.chat.cost.DEFAULT_HOURLY_RATE_EUR` (50 EUR/h — the CLAUDE.md
§6 placeholder). A session with no ``hours_logged`` contributes 0 (the human did
not log time — we never fabricate hours). The per-session rate is converted to
USD at the SESSION's own date (``start_at``) so the human cost is apples-to-apples
with the canonical ``executions.total_cost_usd`` the AI side already sums.

This feeds two consumers:

  * the BUDGET evaluator (``api_server.budgets.consumption``): a project that
    opted in via ``projects.budget_includes_human_cost`` (task_16_12 migration
    0074) folds its human cost into the spend the thresholds / auto-pause
    compare against the cap; AND
  * the 13.7 DASHBOARD (``routers/tenant_stats``): segments AI cost vs human
    cost over the consumption window.

Multi-tenancy (NON-NEGOTIABLE): every query is filtered on ``tenant_id`` (belt
+ braces over RLS) AND, where given, the ``project_id`` / ``plan_id`` of the
scope. The rate conversion uses the same FX catalog (``exchange_rates``) as the
AI budget; a session whose rate currency has no FX rate is degraded to
treating the rate as already-USD (``fallback_to_usd=True``) so a missing rate
never blocks the human-cost roll-up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.chat.cost import DEFAULT_HOURLY_RATE_EUR
from api_server.db.domain import Agent, HumanAgentConfig, HumanWorkSession, Task
from api_server.fx import convert_to_usd

# The currency the DEFAULT_HOURLY_RATE_EUR fallback is denominated in (EUR, per
# CLAUDE.md §6). A named constant, not an inline literal.
_DEFAULT_RATE_CURRENCY = "EUR"

# Canonical-USD quantum — mirrors executions.total_cost_usd Numeric(14, 6) so the
# human cost is summable into / comparable against the AI cost without drift.
_USD_QUANTUM = Decimal("0.000001")


@dataclass(frozen=True)
class HumanCostScope:
    """Human cost (canonical USD) over a date window for one scope.

    ``human_cost_usd`` is the sum of every in-window session's
    ``hours_logged * rate`` converted to USD. ``hours_logged`` is the raw sum of
    logged hours (for display). ``session_count`` is how many in-window sessions
    contributed (a session with NULL hours still counts as a session but adds 0
    cost).
    """

    human_cost_usd: Decimal
    hours_logged: Decimal
    session_count: int


def _q_usd(value: Decimal) -> Decimal:
    return value.quantize(_USD_QUANTUM, rounding=ROUND_HALF_UP)


async def compute_human_cost_usd(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID | None = None,
    plan_id: UUID | None = None,
    window_start: date | None = None,
    window_end: date | None = None,
) -> HumanCostScope:
    """Sum the canonical-USD human cost of in-window work sessions for a scope.

    Tenant-scoped (RLS) + a defence-in-depth ``tenant_id ==`` predicate. The
    optional ``project_id`` / ``plan_id`` narrow to a project's / plan's tasks.
    The half-open ``[window_start, window_end)`` window is matched on each
    session's ``start_at`` cast to a date (mirrors the AI side, which windows on
    the execution date); ``None`` bounds mean unbounded on that side.

    Each session's cost is ``hours_logged * rate`` where the rate + currency come
    from the task's assigned Human Agent's :class:`HumanAgentConfig`, falling
    back to :data:`DEFAULT_HOURLY_RATE_EUR` (EUR) when none is configured. The
    rate is converted to USD at the session's own ``start_at`` date
    (``fallback_to_usd=True`` so a missing FX rate degrades to already-USD rather
    than raising). A session with NULL ``hours_logged`` adds 0 cost.
    """
    # Pull the per-session rows we need to price: hours, the session date and
    # the assigned Human Agent's configured rate + currency. LEFT-join the agent
    # + its config through the task so a session whose task/agent/config is gone
    # still appears (priced at the default rate).
    stmt = (
        select(
            HumanWorkSession.id,
            HumanWorkSession.hours_logged,
            HumanWorkSession.start_at,
            HumanAgentConfig.hourly_rate,
            HumanAgentConfig.hourly_rate_currency,
        )
        .select_from(HumanWorkSession)
        .join(Task, Task.id == HumanWorkSession.task_id)
        .outerjoin(
            Agent,
            (Agent.id == Task.assigned_agent_id) & (Agent.agent_type == "human"),
        )
        .outerjoin(HumanAgentConfig, HumanAgentConfig.agent_id == Agent.id)
        .where(HumanWorkSession.tenant_id == tenant_id)
    )
    if project_id is not None:
        stmt = stmt.where(Task.project_id == project_id)
    if plan_id is not None:
        stmt = stmt.where(Task.plan_id == plan_id)
    # The window is in dates; cast the (timestamptz) start_at to a date on BOTH
    # bounds so the half-open [start, end) matches the AI side exactly (a
    # session ON window_start is in; one ON window_end belongs to the next).
    session_date = func.date(HumanWorkSession.start_at)
    if window_start is not None:
        stmt = stmt.where(session_date >= window_start)
    if window_end is not None:
        stmt = stmt.where(session_date < window_end)

    rows = (await session.execute(stmt)).all()

    total_cost = Decimal("0")
    total_hours = Decimal("0")
    session_count = 0
    for _sid, hours_logged, start_at, hourly_rate, rate_currency in rows:
        session_count += 1
        if hours_logged is None:
            continue
        hours = Decimal(str(hours_logged))
        total_hours += hours
        # Rate + currency from the agent's config; fall back to the EUR default
        # when the agent has no rate configured (or the row is gone).
        if hourly_rate is not None:
            rate = Decimal(str(hourly_rate))
            currency = rate_currency or _DEFAULT_RATE_CURRENCY
        else:
            rate = DEFAULT_HOURLY_RATE_EUR
            currency = _DEFAULT_RATE_CURRENCY
        cost_native = hours * rate
        # Convert the native-currency cost into canonical USD at the session
        # date. fallback_to_usd=True so a missing FX rate degrades gracefully.
        cost_usd = await convert_to_usd(
            session,
            cost_native,
            currency,
            start_at.date(),
            fallback_to_usd=True,
        )
        total_cost += cost_usd

    return HumanCostScope(
        human_cost_usd=_q_usd(total_cost),
        hours_logged=total_hours,
        session_count=session_count,
    )


__all__ = ["HumanCostScope", "compute_human_cost_usd"]
