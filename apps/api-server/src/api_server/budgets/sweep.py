"""One-tenant budget sweep — re-derive auto-pause + fire alerts (prod-06 task_prod06_budget_01).

The productive caller seam for :func:`refresh_budget_pause_flags` and
:func:`maybe_alert_budgets`, which previously had only tests as callers (db-1).
Two hosts call it:

  - the periodic beat ``workers.refresh_budgets`` — once per tenant per tick,
    the safety net (a period rollover that auto-clears a pause, a missed hook,
    a manual spend correction);
  - the worker's post-execution hook — right after ``finalize_execution``
    records a run's cost, so a run that tips a scope over 100% pauses the NEXT
    start immediately instead of waiting for the next beat.

The dispatch READER (``budget_pause_block``) already existed; this is the seam
that wires the WRITERS the reader depends on.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from api_server.budgets.consumption import (
    BudgetAlertDispatcher,
    BudgetEvaluationResult,
    maybe_alert_budgets,
)
from api_server.budgets.pause import BudgetPauseRefresh, refresh_budget_pause_flags


@dataclass
class TenantBudgetSweep:
    """The outcome of one tenant's budget sweep (for assertions / logs)."""

    refresh: BudgetPauseRefresh
    alerts: BudgetEvaluationResult


async def sweep_tenant_budgets(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    dispatcher: BudgetAlertDispatcher | None = None,
    now: datetime | None = None,
) -> TenantBudgetSweep:
    """Re-derive ``paused_by_budget`` for every scope of one tenant AND fire any
    threshold alerts.

    The caller owns the transaction (both steps flush, neither commits). Order
    matters: the pause flags are refreshed FIRST so a concurrent dispatch sees
    the fresh flag before the (best-effort, never-raising) alert fan-out. Runs
    on whatever session the caller provides — both the beat and the worker hook
    pass the BYPASSRLS engine, and the underlying functions carry explicit
    ``tenant_id`` predicates (same contract as ``budget_pause_block``).
    """
    refresh = await refresh_budget_pause_flags(session, tenant_id=tenant_id, now=now)
    alerts = await maybe_alert_budgets(session, tenant_id=tenant_id, dispatcher=dispatcher, now=now)
    return TenantBudgetSweep(refresh=refresh, alerts=alerts)
