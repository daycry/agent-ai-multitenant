"""Cross-tenant comparison — System Admin ONLY (Plan 14 task_14_15).

The ONE deliberately cross-tenant surface in Plan 14. Every other dashboard /
stats / export endpoint is tenant-scoped (RLS) so a tenant only ever sees its
own data; this one lets a PLATFORM OPERATOR (System Admin) compare how each
tenant's agents perform side by side — success rate, cost and throughput — over
a window.

Gating (defence in layers):

  - ``require_system_admin`` rejects a ``tenant_admin`` / ``tenant_user`` with a
    clean 403 *before* any query runs (the JWT must carry ``sys: true``).
  - ``get_admin_session`` runs the aggregation on the BYPASSRLS admin engine, so
    the per-tenant roll-up actually spans every tenant. A regular tenant session
    would be RLS-clamped to one tenant and could never produce the comparison —
    only this System-Admin path crosses tenants by design.

The comparison is strictly AGGREGATE: ``GROUP BY`` the tenant, returning counts
/ rates / sums per tenant joined to ``organizations`` for the label. No
per-execution row, prompt, completion, ``steps_log``, credential or other
tenant secret / PII crosses tenants — a platform operator learns that tenant X
ran N times at a Y% success rate and $Z, never *what* tenant X did.

Costs are CANONICAL USD. The tenant-currency display toggle the plan mentions
depends on the FX / display-currency system (exchange_rates), which has no
numbered task and was not built (Plan 11 scope gap); USD is the only currency
surfaced — and a single currency is the *correct* default for a cross-tenant
comparison, since heterogeneous display currencies would not be comparable.

Pure aggregation over existing rows — no migration.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_admin_session, require_system_admin
from api_server.db.domain import Execution
from api_server.db.models import Organization
from api_server.routers.tenant_stats import (
    _COST_QUANTUM,
    _MS_QUANTUM,
    _RATE_QUANTUM,
    _duration_ms,
    _quantize,
    _quantize_cost,
    _rate,
    _succeeded_flag,
    _window_days,
)
from api_server.schemas.cross_tenant_stats import (
    CrossTenantStatsResponse,
    TenantComparisonRow,
)

router = APIRouter(prefix="/admin/cross-tenant-stats", tags=["stats", "admin"])


# ===========================================================================
# GET /admin/cross-tenant-stats — per-tenant comparative statistics
# ===========================================================================
@router.get("", response_model=CrossTenantStatsResponse)
async def cross_tenant_stats(
    principal: AuthPrincipal = Depends(require_system_admin),
    session: AsyncSession = Depends(get_admin_session),
    window_days: int = _window_days(),
) -> CrossTenantStatsResponse:
    """Per-tenant comparative statistics over the last N days. System Admin ONLY.

    Success rate / cost / throughput per tenant, side by side. Gated to System
    Admin (``require_system_admin`` → 403 for a tenant_admin/member) and run on
    the BYPASSRLS admin session so the roll-up spans every tenant. Strictly
    aggregate (counts / rates / sums per tenant); no per-tenant secret / PII.
    Costs in canonical USD. Window in days (``1..MAX_STATS_WINDOW_DAYS``); a
    request out of range is a clean 422.
    """
    _ = principal  # gate-only; the admin session already bypasses RLS.
    since = datetime.now(tz=UTC) - timedelta(days=window_days)

    rows = await _per_tenant(session, since=since, window_days=window_days)

    # Roll-up totals across all tenants (sum the per-tenant aggregates so the
    # headline and the rows always agree — no second pass over executions).
    total_runs = sum(r.run_count for r in rows)
    total_succeeded = sum(r.succeeded_runs for r in rows)
    total_cost = sum((r.total_cost_usd for r in rows), Decimal(0))

    return CrossTenantStatsResponse(
        window_days=window_days,
        tenant_count=len(rows),
        total_runs=total_runs,
        total_succeeded_runs=total_succeeded,
        overall_success_rate=_rate(total_succeeded, total_runs),
        total_cost_usd=total_cost.quantize(_COST_QUANTUM),
        tenants=rows,
    )


async def _per_tenant(
    session: AsyncSession,
    *,
    since: datetime,
    window_days: int,
) -> list[TenantComparisonRow]:
    """Aggregate executions per tenant (success rate / cost / throughput).

    One ``GROUP BY Execution.tenant_id`` over EVERY tenant's runs in the window
    (BYPASSRLS admin session), joined to ``organizations`` for the label. Pure
    SQL — counts / rates / sums only, never a per-execution row. Ranked by
    success rate (highest first), then run count, so the comparison reads as a
    leaderboard.
    """
    dur = _duration_ms()
    success = _succeeded_flag()
    window = Decimal(window_days) if window_days > 0 else Decimal(1)

    stmt = (
        select(
            Execution.tenant_id,
            Organization.name,
            Organization.slug,
            func.count(),
            func.coalesce(func.sum(success), 0),
            func.avg(dur),
            func.avg(Execution.total_cost_usd),
            func.coalesce(func.sum(Execution.total_cost_usd), 0),
            func.coalesce(func.sum(Execution.total_tokens), 0),
        )
        .select_from(Execution)
        .outerjoin(Organization, Organization.id == Execution.tenant_id)
        .where(_window_filter(since))
        .group_by(Execution.tenant_id, Organization.name, Organization.slug)
    )
    result = (await session.execute(stmt)).all()

    comparison = [
        TenantComparisonRow(
            tenant_id=tenant_id,
            tenant_name=name,
            tenant_slug=slug,
            run_count=int(run_count),
            succeeded_runs=int(succeeded),
            success_rate=_rate(int(succeeded), int(run_count)),
            mean_duration_ms=_quantize(mean_dur, _MS_QUANTUM),
            mean_cost_usd=_quantize(mean_cost, _COST_QUANTUM),
            total_cost_usd=_quantize_cost(total_cost),
            total_tokens=int(total_tokens),
            throughput_runs_per_day=(Decimal(int(run_count)) / window).quantize(_RATE_QUANTUM),
        )
        for (
            tenant_id,
            name,
            slug,
            run_count,
            succeeded,
            mean_dur,
            mean_cost,
            total_cost,
            total_tokens,
        ) in result
    ]

    # Leaderboard order: best success rate first (None rates — impossible here
    # since every grouped tenant ran ≥ 1 — would sort last), then run count.
    comparison.sort(
        key=lambda r: (r.success_rate or Decimal(0), r.run_count),
        reverse=True,
    )
    return comparison


def _window_filter(since: datetime) -> ColumnElement[bool]:
    """Bound the comparison to runs recorded on/after ``since``.

    No ``tenant_id`` predicate — this is the deliberately cross-tenant surface
    (the BYPASSRLS admin session). The ``done`` success bucket is applied via
    the imported ``_succeeded_flag`` expression, not here.
    """
    return Execution.created_at >= since


__all__ = ["router"]
