"""`/runs` — member-facing read access to this tenant's agent runs (executions).

The Work-menu Runs view (and the Kanban card's run-history panel) list a tenant's
executions, newest first. Unlike `GET /tenant-stats/runs` (``tenant_admin`` — the
analytics/export explorer), this surface is open to ANY tenant member: it reuses
the SAME query (:func:`query_execution_runs`) so the row shape and the
tenant-isolation guarantees are identical — only the required role differs.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_member
from api_server.routers._helpers import require_tenant_id
from api_server.routers.tenant_stats import query_execution_runs
from api_server.schemas.tenant_stats import ExecutionRunRow

router = APIRouter(tags=["runs"])

# Mirror the explorer's bounds (tenant_stats) so the two surfaces behave the same.
_DEFAULT_WINDOW_DAYS = 90
_MAX_WINDOW_DAYS = 730


@router.get("/runs", response_model=list[ExecutionRunRow])
async def list_runs(
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    window_days: int = Query(default=_DEFAULT_WINDOW_DAYS, ge=1, le=_MAX_WINDOW_DAYS),
    agent_id: UUID | None = Query(default=None, description="Narrow to one agent."),
    role: str | None = Query(default=None, max_length=32, description="Narrow to one agent role."),
    plan_id: UUID | None = Query(default=None, description="Narrow to one plan."),
    task_id: UUID | None = Query(
        default=None, description="Narrow to one task — the Kanban run-history panel."
    ),
    verdict: str | None = Query(
        default=None, max_length=32, description="Narrow to one execution verdict/status."
    ),
    model: str | None = Query(default=None, max_length=120, description="Narrow to one model."),
    min_cost: Decimal | None = Query(
        default=None, ge=0, description="Minimum total cost USD threshold."
    ),
    display_currency: str | None = Query(
        default=None, max_length=3, description="Override the tenant's display currency (ISO-4217)."
    ),
) -> list[ExecutionRunRow]:
    """This tenant's runs, newest first, paginated + filterable. ANY tenant member.

    Same row shape and tenant isolation as ``GET /tenant-stats/runs`` (RLS-bound
    session + defence-in-depth ``tenant_id`` predicate); only the required role
    differs (member vs ``tenant_admin``). Use ``?task_id=`` for the Kanban card's
    run-history panel. Never leaks prompts / completions / credentials / steps_log.
    """
    tenant_id = require_tenant_id(principal)
    return await query_execution_runs(
        session,
        tenant_id=tenant_id,
        limit=limit,
        offset=offset,
        window_days=window_days,
        agent_id=agent_id,
        role=role,
        plan_id=plan_id,
        task_id=task_id,
        verdict=verdict,
        model=model,
        min_cost=min_cost,
        display_currency=display_currency,
    )
