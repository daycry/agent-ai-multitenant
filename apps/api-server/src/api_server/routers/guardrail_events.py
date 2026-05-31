"""Tenant guardrail observability — events + dashboard endpoints (Plan 11 task_11_20).

The read side of the Plan 11 guardrail-events substrate. A tenant sees the
guardrails that fired on ITS OWN work — never another tenant's — enforced
at the DB by the ``guardrail_events`` tenant-isolation RLS policy (migration
0052) on top of the endpoint RBAC here.

Two endpoints, both tenant-scoped (``get_tenant_session`` → RLS) and gated
to ``tenant_admin`` (the dashboard is an admin surface; the frontend mirrors
this with ``<RoleGuard min="tenant_admin">``):

  - ``GET /guardrails/events``     paginated, filtered list of raw events
                                   (``limit`` / ``offset`` ge/le + optional
                                   ``type`` / ``severity`` / ``since`` /
                                   ``until`` filters).
  - ``GET /guardrails/dashboard``  aggregated activity over a window:
                                   counts by type, by severity, a per-day
                                   time series, and the most-recent events.

Masking invariant: every event's ``detail`` / ``detail_payload`` carries a
MASKED summary only — the raw secret / PII that tripped the guardrail was
never persisted (the recorder service masked it). So nothing sensitive can
leak through these read endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

from fastapi import APIRouter, Depends, Query
from sqlalchemy import ColumnElement, Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
)
from api_server.db.guardrail_event import (
    GuardrailEvent,
    GuardrailEventSeverity,
    GuardrailHookPoint,
)
from api_server.routers._helpers import require_tenant_id
from api_server.routers._pagination import apply_pagination, limit_query, offset_query
from api_server.schemas.guardrail_events import (
    GuardrailDashboardResponse,
    GuardrailDayCount,
    GuardrailEventResponse,
    GuardrailSeverityCount,
    GuardrailTypeCount,
    to_event_response,
)

router = APIRouter(prefix="/guardrails", tags=["guardrails"])

# Default + max window (in days) the dashboard aggregates over. A named
# constant, not a magic number — a platform invariant of the dashboard
# contract (a request above the max is a clean 422, not a silent clamp).
DEFAULT_DASHBOARD_WINDOW_DAYS = 30
MAX_DASHBOARD_WINDOW_DAYS = 365
# How many recent events the dashboard returns inline for the activity table.
DASHBOARD_RECENT_LIMIT = 20


def _type_filter() -> str | None:
    """Optional ``?type=`` filter on the guardrail type (free-form)."""
    return cast(
        str | None,
        Query(default=None, max_length=64, description="Filter by guardrail type."),
    )


def _window_days() -> int:
    return cast(
        int,
        Query(
            default=DEFAULT_DASHBOARD_WINDOW_DAYS,
            ge=1,
            le=MAX_DASHBOARD_WINDOW_DAYS,
            description=f"Dashboard aggregation window in days (1..{MAX_DASHBOARD_WINDOW_DAYS}).",
        ),
    )


def _apply_filters(
    stmt: Select[tuple[GuardrailEvent]],
    *,
    tenant_id: object,
    guardrail_type: str | None,
    severity: GuardrailEventSeverity | None,
    hook_point: GuardrailHookPoint | None,
    since: datetime | None,
    until: datetime | None,
) -> Select[tuple[GuardrailEvent]]:
    """Apply the tenant scope + optional filters to a SELECT.

    The ``tenant_id`` equality is defence in depth on top of RLS — the
    RLS policy already restricts the rows, but pinning the predicate keeps
    the query plan tight and the intent explicit.
    """
    filters: list[ColumnElement[bool]] = [GuardrailEvent.tenant_id == tenant_id]
    if guardrail_type is not None:
        filters.append(GuardrailEvent.guardrail_type == guardrail_type)
    if severity is not None:
        filters.append(GuardrailEvent.severity == severity.value)
    if hook_point is not None:
        filters.append(GuardrailEvent.hook_point == hook_point.value)
    if since is not None:
        filters.append(GuardrailEvent.created_at >= since)
    if until is not None:
        filters.append(GuardrailEvent.created_at < until)
    return stmt.where(*filters)


# ===========================================================================
# GET /guardrails/events — paginated, filtered list
# ===========================================================================
@router.get("/events", response_model=list[GuardrailEventResponse])
async def list_guardrail_events(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    limit: int = limit_query(),
    offset: int = offset_query(),
    guardrail_type: str | None = _type_filter(),
    severity: GuardrailEventSeverity | None = Query(
        default=None, description="Filter by severity (info/low/medium/high/critical)."
    ),
    hook_point: GuardrailHookPoint | None = Query(
        default=None, description="Filter by hook point (pre_llm/post_llm/pre_tool/post_tool)."
    ),
    since: datetime | None = Query(
        default=None, description="Only events created at/after this UTC time."
    ),
    until: datetime | None = Query(
        default=None, description="Only events created strictly before this UTC time."
    ),
) -> list[GuardrailEventResponse]:
    """List this tenant's guardrail events, newest-first, paginated + filtered.

    Tenant-scoped (RLS) — only the caller tenant's events are visible. The
    masked ``detail`` / ``detail_payload`` never carry the raw value.
    """
    tenant_id = require_tenant_id(principal)
    stmt = _apply_filters(
        select(GuardrailEvent),
        tenant_id=tenant_id,
        guardrail_type=guardrail_type,
        severity=severity,
        hook_point=hook_point,
        since=since,
        until=until,
    ).order_by(GuardrailEvent.created_at.desc(), GuardrailEvent.id.desc())
    stmt = apply_pagination(stmt, limit=limit, offset=offset)
    rows = (await session.execute(stmt)).scalars().all()
    return [to_event_response(r) for r in rows]


# ===========================================================================
# GET /guardrails/dashboard — aggregated activity
# ===========================================================================
@router.get("/dashboard", response_model=GuardrailDashboardResponse)
async def guardrail_dashboard(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
    window_days: int = _window_days(),
) -> GuardrailDashboardResponse:
    """Aggregated guardrail activity for this tenant over the last N days.

    Counts grouped by type and by severity (for the dashboard charts), a
    per-UTC-day time series (the trend chart), and the latest events (the
    recent-activity table). All tenant-scoped (RLS).
    """
    tenant_id = require_tenant_id(principal)
    # Window lower bound. Both `since` and `created_at` are TIMESTAMPTZ, so a
    # plain UTC-now minus the window compares cleanly as a bound parameter
    # (no dialect-specific interval arithmetic needed).
    since = datetime.now(tz=UTC) - timedelta(days=window_days)

    base = (GuardrailEvent.tenant_id == tenant_id) & (GuardrailEvent.created_at >= since)

    total = (
        await session.execute(select(func.count()).select_from(GuardrailEvent).where(base))
    ).scalar_one()

    by_type_rows = (
        await session.execute(
            select(GuardrailEvent.guardrail_type, func.count())
            .where(base)
            .group_by(GuardrailEvent.guardrail_type)
            .order_by(func.count().desc(), GuardrailEvent.guardrail_type)
        )
    ).all()

    by_severity_rows = (
        await session.execute(
            select(GuardrailEvent.severity, func.count())
            .where(base)
            .group_by(GuardrailEvent.severity)
            .order_by(func.count().desc(), GuardrailEvent.severity)
        )
    ).all()

    day_col = func.to_char(func.date_trunc("day", GuardrailEvent.created_at), "YYYY-MM-DD")
    by_day_rows = (
        await session.execute(
            select(day_col, func.count()).where(base).group_by(day_col).order_by(day_col)
        )
    ).all()

    recent_rows = (
        (
            await session.execute(
                select(GuardrailEvent)
                .where(base)
                .order_by(GuardrailEvent.created_at.desc(), GuardrailEvent.id.desc())
                .limit(DASHBOARD_RECENT_LIMIT)
            )
        )
        .scalars()
        .all()
    )

    return GuardrailDashboardResponse(
        total=int(total),
        window_days=window_days,
        by_type=[GuardrailTypeCount(guardrail_type=t, count=int(c)) for t, c in by_type_rows],
        by_severity=[GuardrailSeverityCount(severity=s, count=int(c)) for s, c in by_severity_rows],
        by_day=[GuardrailDayCount(day=d, count=int(c)) for d, c in by_day_rows],
        recent=[to_event_response(r) for r in recent_rows],
    )


__all__ = ["router"]
