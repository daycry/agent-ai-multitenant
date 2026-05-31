"""Pydantic schemas for the CROSS-TENANT comparison (Plan 14 task_14_15).

The read shape behind the System-Admin-only cross-tenant statistics endpoint
(``GET /admin/cross-tenant-stats``). This is the ONE deliberately cross-tenant
surface in Plan 14: every other dashboard / stats / export surface is
tenant-scoped (RLS). Here a platform operator (System Admin) compares how each
tenant's agents perform side by side — success rate, cost and throughput — over
a window, using the BYPASSRLS admin session.

The comparison is strictly AGGREGATE: one row PER TENANT with counts / rates /
sums only. No per-execution rows, prompts, completions, ``steps_log``,
credentials or any other tenant secret / PII crosses tenants through this
surface — a platform operator sees that tenant X ran N times at a Y% success
rate and $Z, never *what* tenant X actually did.

Costs are CANONICAL USD. A tenant-currency display toggle is intentionally NOT
modelled: the FX / display-currency system it would depend on (exchange_rates)
has no numbered task and was not built (flagged as a scope gap in Plan 11's
changelog). USD is the only currency surfaced. Cross-tenant comparison in a
single currency is in fact the *correct* default — comparing tenants in their
own heterogeneous display currencies would not be comparable at all.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

_CONFIG = ConfigDict(from_attributes=True)


class TenantComparisonRow(BaseModel):
    """One tenant's aggregate statistics in the cross-tenant comparison.

    Aggregate only (counts / rates / sums) — never a per-execution row.
    ``success_rate`` is the fraction of the tenant's executions that ended
    ``done`` (a fraction in ``[0, 1]``; ``None`` when the tenant had no runs in
    the window). ``throughput_runs_per_day`` is the tenant's run count divided
    by the window length in days — a comparable throughput regardless of when
    the runs landed. ``mean_cost_usd`` is the mean over the runs that reported a
    cost; ``total_cost_usd`` is the window sum. Costs are canonical USD.
    """

    model_config = _CONFIG

    tenant_id: UUID
    tenant_name: str | None
    tenant_slug: str | None
    run_count: int
    succeeded_runs: int
    success_rate: Decimal | None
    mean_duration_ms: Decimal | None
    mean_cost_usd: Decimal | None
    total_cost_usd: Decimal
    total_tokens: int
    throughput_runs_per_day: Decimal


class CrossTenantStatsResponse(BaseModel):
    """The cross-tenant comparison for a System Admin (task_14_15).

    System-Admin-ONLY: a ``tenant_admin`` / ``tenant_user`` is 403. Aggregated
    over EVERY tenant's :class:`~api_server.db.domain.Execution` rows in the
    window via the BYPASSRLS admin session — the only surface in Plan 14 that
    crosses tenants. ``tenants`` carries one :class:`TenantComparisonRow` per
    tenant that ran at least once in the window, ranked by success rate
    (highest first), then run count. The roll-up totals span all tenants.

    Strictly aggregate: counts / rates / sums only, no per-tenant secret / PII.
    Costs are canonical USD (``currency`` is always ``"USD"``; tenant-currency
    display depends on the unbuilt FX system — Plan 11 scope gap).
    """

    model_config = _CONFIG

    window_days: int
    currency: str = Field(default="USD")
    tenant_count: int
    total_runs: int
    total_succeeded_runs: int
    overall_success_rate: Decimal | None
    total_cost_usd: Decimal
    tenants: list[TenantComparisonRow]


__all__ = [
    "CrossTenantStatsResponse",
    "TenantComparisonRow",
]
