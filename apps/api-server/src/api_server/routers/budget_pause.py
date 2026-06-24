"""`/budgets/pause` — budget auto-pause status + manual override (Plan 11.1 task_11_1_06).

When a tenant or project reaches 100% of its budget for the active period the
consumption evaluator sets ``paused_by_budget`` and the orchestrator refuses to
START new executions for that scope (active runs are never killed). This router
lets a Tenant Admin (or a System Admin acting for the tenant) inspect the pause
state and MANUALLY OVERRIDE it — clearing the pause so new runs may start again
(e.g. after raising the budget, or to grant a temporary allowance for the rest
of the period). Every override writes a ``budget_pause_override`` ``audit_log``
row capturing who, what scope, the reason and any temporary-allowance date.

RBAC + tenancy (CLAUDE.md principle 1):

  - Both endpoints are gated to ``tenant_admin`` (``require_tenant_admin`` — a
    System Admin always passes; a plain member is a clean 403) and run on the
    tenant-scoped RLS session (``get_tenant_session``).
  - The override clears the flag on ``organizations`` (tenant scope) or
    ``projects`` (project scope), both of which RLS restricts to the caller's
    tenant; a ``tenant_id`` predicate is defence in depth. A Tenant Admin can
    NEVER clear another tenant's pause.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_client_ip,
    get_tenant_session,
    require_tenant_admin,
)
from api_server.budgets.pause import clear_budget_pause
from api_server.db.budget_alert_state import BudgetScope
from api_server.db.domain import Project
from api_server.db.models import Organization
from api_server.routers._helpers import require_tenant_id

router = APIRouter(prefix="/budgets/pause", tags=["budgets"])

# A free-form override reason is bounded so a client cannot bloat the audit row.
_REASON_MAX_LEN = 500


class BudgetPauseStatusResponse(BaseModel):
    """The current auto-pause state for a tenant + its projects."""

    tenant_paused: bool
    paused_project_ids: list[UUID]


class BudgetPauseOverrideRequest(BaseModel):
    """Manually clear the budget pause for one scope.

    ``scope`` is ``tenant`` (clear the tenant-wide pause) or ``project`` (clear
    one project's pause — ``project_id`` then required). ``reason`` is an
    optional human note recorded in the audit row. ``temporary_allowance_until``
    is an optional date recorded in the audit row to document a grace window.
    """

    scope: BudgetScope
    project_id: UUID | None = None
    reason: str | None = Field(default=None, max_length=_REASON_MAX_LEN)
    temporary_allowance_until: date | None = None


class BudgetPauseOverrideResponse(BaseModel):
    """The outcome of a manual override."""

    scope: BudgetScope
    project_id: UUID | None
    cleared: bool


# ===========================================================================
# GET /budgets/pause — current pause state (tenant_admin)
# ===========================================================================
@router.get("", response_model=BudgetPauseStatusResponse)
async def get_pause_status(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> BudgetPauseStatusResponse:
    """Report whether the tenant + any of its projects are budget-paused.

    Tenant-scoped (RLS): only the caller tenant's org + projects are visible.
    """
    tenant_id = require_tenant_id(principal)
    tenant_paused = (
        await session.execute(
            select(Organization.tenant_paused_by_budget).where(Organization.id == tenant_id)
        )
    ).scalar_one_or_none()
    paused_projects = (
        (
            await session.execute(
                select(Project.id).where(
                    Project.tenant_id == tenant_id,
                    Project.deleted_at.is_(None),
                    Project.paused_by_budget.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return BudgetPauseStatusResponse(
        tenant_paused=bool(tenant_paused),
        paused_project_ids=list(paused_projects),
    )


# ===========================================================================
# POST /budgets/pause/override — clear the pause (tenant_admin)
# ===========================================================================
@router.post(
    "/override",
    response_model=BudgetPauseOverrideResponse,
    status_code=status.HTTP_200_OK,
)
async def override_pause(
    payload: BudgetPauseOverrideRequest,
    request: Request,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> BudgetPauseOverrideResponse:
    """Manually clear the budget pause for a scope, writing an audit entry.

    Clears ``paused_by_budget`` (tenant or one project) so NEW executions may
    start again; a ``budget_pause_override`` ``audit_log`` row records the
    actor, scope, reason and any temporary-allowance date. A 422 if
    ``scope=project`` without a ``project_id``. Tenant-scoped (RLS).
    """
    tenant_id = require_tenant_id(principal)
    if payload.scope is BudgetScope.PROJECT and payload.project_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="project_id is required for scope=project",
        )
    project_id = payload.project_id if payload.scope is BudgetScope.PROJECT else None
    cleared = await clear_budget_pause(
        session,
        tenant_id=tenant_id,
        scope=payload.scope,
        project_id=project_id,
        actor_user_id=principal.user_id,
        is_system_admin=principal.is_system_admin,
        reason=payload.reason,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent"),
        temporary_allowance_until=payload.temporary_allowance_until,
    )
    return BudgetPauseOverrideResponse(scope=payload.scope, project_id=project_id, cleared=cleared)


__all__ = ["router"]
