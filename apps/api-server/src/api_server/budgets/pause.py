"""Budget auto-pause + manual override (Plan 11.1 task_11_1_06).

The enforcement side of tenant/project budgets. The consumption evaluator
(``api_server.budgets.consumption``) already computes, per scope, the
CANONICAL-USD spend over the active period vs the (USD-converted) cap. This
module turns "100% reached" into a **pause flag** the execution-start path
honours, and provides the **manual override** that clears it:

  1. **Auto-pause** — :func:`refresh_budget_pause_flags` recomputes every
     configured budget and sets ``paused_by_budget`` (on ``organizations`` for
     the tenant scope, on ``projects`` for each project scope) to true iff the
     scope is at/over 100% of its budget for the active period, false
     otherwise. Because the flag is RE-DERIVED from the current period's
     consumption, a NEW period auto-clears it (the fresh window starts under
     100%) with no extra bookkeeping. This NEVER touches running executions —
     it only flips a flag the START path reads.

  2. **The START guard** — :func:`budget_pause_block` reads the tenant + (for a
     project run) the project pause flags and returns a typed
     :class:`BudgetPauseBlock` reason when a NEW execution start must be
     refused, or ``None`` to allow. The orchestrator's execution-start path
     calls it BEFORE it moves a task to ``in_progress`` / enqueues the worker
     run; a block means the run is simply not enqueued (a clear, typed reason
     logged), while any already-running execution keeps going untouched.

  3. **Manual override** — :func:`clear_budget_pause` clears the pause for a
     scope (System Admin / Tenant Admin) and writes an ``audit_log`` entry. It
     can record an optional temporary-allowance date in the audit row (the
     scope stays un-paused until the next auto-pause refresh sees it back over
     100% — i.e. the override grants headroom for the rest of the period).

Multi-tenancy (NON-NEGOTIABLE): the pause flags + consumption are
tenant/project-scoped. :func:`refresh_budget_pause_flags` and
:func:`clear_budget_pause` run on the caller's TENANT-SCOPED RLS session (the
org row, the projects, the consumption sum are all filtered to ``tenant_id``),
so tenant A's spend can NEVER pause tenant B. :func:`budget_pause_block` is
called by the orchestrator on a BYPASSRLS session, so it carries an explicit
``tenant_id ==`` predicate (defence in depth) and never relies on RLS.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.audit import write_audit_log
from api_server.budgets.consumption import BudgetConsumption, compute_budget_consumption
from api_server.db.budget_alert_state import BudgetScope
from api_server.db.domain import Project
from api_server.db.models import Organization

_log = structlog.get_logger("api_server.budgets.pause")

# The audit_log action a manual budget-pause override writes. A named
# constant, not an inline literal scattered across the endpoint + tests.
BUDGET_PAUSE_OVERRIDE_ACTION = "budget_pause_override"

# The percent-used mark at which a scope auto-pauses. The plan binds this to
# 100% (the alert thresholds always include the 100% pause arm — see
# platform_settings.validate_budget_alert_thresholds). Kept as a named Decimal
# so the comparison is never a bare magic literal.
_PAUSE_PERCENT = Decimal(100)


# =============================================================================
# The typed START-guard reason
# =============================================================================
@dataclass(frozen=True)
class BudgetPauseBlock:
    """Why a NEW execution start was refused by the budget auto-pause.

    The dispatcher logs this verbatim and never enqueues the run. ``scope``
    is which budget tripped (the tenant-wide one or a specific project); for a
    project block ``project_id`` is set. ``reason`` is a stable machine code
    the caller / UI can branch on.
    """

    scope: BudgetScope
    tenant_id: UUID
    project_id: UUID | None
    reason: str = "budget_paused"

    def as_log_fields(self) -> dict[str, str]:
        """Flat, JSON-safe fields for a structured log line."""
        return {
            "reason": self.reason,
            "scope": self.scope.value,
            "tenant_id": str(self.tenant_id),
            "project_id": str(self.project_id) if self.project_id is not None else "",
        }


# =============================================================================
# Auto-pause: re-derive the pause flags from current-period consumption
# =============================================================================
def _is_paused(consumption: BudgetConsumption) -> bool:
    """True when the scope is at/over 100% of its (USD) budget this period.

    A scope whose cap is unconvertible (``percent_used is None``) is NOT
    paused — we never block on a number we cannot honestly compute.
    """
    return consumption.percent_used is not None and consumption.percent_used >= _PAUSE_PERCENT


@dataclass
class BudgetPauseRefresh:
    """The outcome of one auto-pause refresh pass (for assertions / logs)."""

    tenant_id: UUID
    # Scopes whose flag was newly SET to paused this pass.
    newly_paused: list[BudgetConsumption]
    # Scopes whose flag was newly CLEARED (e.g. a new period dropped them
    # back under 100%, or spend was corrected).
    newly_cleared: list[BudgetConsumption]


async def refresh_budget_pause_flags(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    now: datetime | None = None,
) -> BudgetPauseRefresh:
    """Re-derive ``paused_by_budget`` for every configured budget of a tenant.

    Runs on the caller's TENANT-SCOPED RLS session. For each scope (the
    tenant-wide budget on ``organizations`` + each project's on ``projects``)
    the flag is set to ``True`` iff the scope is at/over 100% of its
    USD-converted cap for the ACTIVE period, ``False`` otherwise — so a NEW
    period (under 100% again) auto-clears the pause with no extra state.

    Active executions are untouched: this only flips a flag the START path
    reads. The caller owns the transaction (this flushes, never commits).
    Returns the scopes newly paused / newly cleared.
    """
    now = now or datetime.now(tz=UTC)
    consumptions = await compute_budget_consumption(
        session, tenant_id=tenant_id, on_date=now.date()
    )

    newly_paused: list[BudgetConsumption] = []
    newly_cleared: list[BudgetConsumption] = []

    # Cache the org row once (it is read for the tenant scope).
    org: Organization | None = None
    for consumption in consumptions:
        should_pause = _is_paused(consumption)
        if consumption.scope is BudgetScope.TENANT:
            if org is None:
                org = await session.get(Organization, tenant_id)
            if org is None:  # pragma: no cover - defensive (RLS-filtered)
                continue
            changed = org.tenant_paused_by_budget != should_pause
            if changed:
                org.tenant_paused_by_budget = should_pause
                (newly_paused if should_pause else newly_cleared).append(consumption)
        else:
            project = await _load_project_for_update(
                session, tenant_id=tenant_id, project_id=consumption.project_id
            )
            if project is None:  # pragma: no cover - defensive (RLS-filtered)
                continue
            changed = project.paused_by_budget != should_pause
            if changed:
                project.paused_by_budget = should_pause
                (newly_paused if should_pause else newly_cleared).append(consumption)

    if newly_paused or newly_cleared:
        await session.flush()
        for c in newly_paused:
            _log.info(
                "budget_pause.set",
                tenant_id=str(tenant_id),
                scope=c.scope.value,
                project_id=str(c.project_id) if c.project_id else None,
                percent_used=str(c.percent_used) if c.percent_used is not None else None,
            )
        for c in newly_cleared:
            _log.info(
                "budget_pause.auto_cleared",
                tenant_id=str(tenant_id),
                scope=c.scope.value,
                project_id=str(c.project_id) if c.project_id else None,
            )

    return BudgetPauseRefresh(
        tenant_id=tenant_id, newly_paused=newly_paused, newly_cleared=newly_cleared
    )


async def _load_project_for_update(
    session: AsyncSession, *, tenant_id: UUID, project_id: UUID | None
) -> Project | None:
    """Load a live project within the tenant (defence-in-depth predicate)."""
    if project_id is None:  # pragma: no cover - tenant scope never reaches here
        return None
    return (
        await session.execute(
            select(Project).where(
                Project.id == project_id,
                Project.tenant_id == tenant_id,
                Project.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


# =============================================================================
# The START guard: refuse a NEW execution when the scope is paused
# =============================================================================
async def budget_pause_block(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    project_id: UUID | None,
) -> BudgetPauseBlock | None:
    """Return a typed block reason if a NEW execution start must be refused.

    Reads the tenant-wide pause flag (``organizations.tenant_paused_by_budget``)
    and, for a project run, the project's ``paused_by_budget``. Returns the
    FIRST tripped scope (project takes precedence — it is the more specific
    budget) or ``None`` to allow the start. Carries an explicit ``tenant_id``
    predicate so it is safe on the orchestrator's BYPASSRLS session (it never
    relies on RLS). Read-only: it flips no flags and touches no running runs.
    """
    # Project scope first (the more specific budget). A project run blocked by
    # its own budget is reported as a PROJECT block even if the tenant is also
    # paused, so the operator sees the proximate cause.
    if project_id is not None:
        project = (
            await session.execute(
                select(Project.paused_by_budget).where(
                    Project.id == project_id,
                    Project.tenant_id == tenant_id,
                    Project.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if project:
            return BudgetPauseBlock(
                scope=BudgetScope.PROJECT, tenant_id=tenant_id, project_id=project_id
            )

    tenant_paused = (
        await session.execute(
            select(Organization.tenant_paused_by_budget).where(Organization.id == tenant_id)
        )
    ).scalar_one_or_none()
    if tenant_paused:
        return BudgetPauseBlock(scope=BudgetScope.TENANT, tenant_id=tenant_id, project_id=None)

    return None


# =============================================================================
# Manual override: clear the pause + write audit_log
# =============================================================================
async def clear_budget_pause(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    scope: BudgetScope,
    project_id: UUID | None,
    actor_user_id: UUID | None,
    is_system_admin: bool = False,
    reason: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    temporary_allowance_until: date | None = None,
) -> bool:
    """Manually clear the budget pause for one scope, writing an audit entry.

    A System Admin / Tenant Admin override that flips ``paused_by_budget`` back
    to false for the tenant-wide budget (``scope=TENANT``) or one project
    (``scope=PROJECT`` + ``project_id``), so NEW executions may start again.
    Writes a ``budget_pause_override`` ``audit_log`` row capturing who, what
    scope, the optional human reason, and any temporary-allowance date. The
    caller owns the transaction (this flushes, never commits).

    Returns True iff a paused flag was actually cleared (idempotent: clearing
    an already-unpaused scope is a no-op that still audits the attempt, but
    returns False). Tenant-scoped (RLS) — only the caller tenant's org /
    projects are reachable; ``tenant_id`` is also an explicit predicate.
    """
    cleared = False
    target_project: Project | None = None
    if scope is BudgetScope.PROJECT:
        if project_id is None:
            raise ValueError("clear_budget_pause(PROJECT) requires a project_id")
        target_project = await _load_project_for_update(
            session, tenant_id=tenant_id, project_id=project_id
        )
        if target_project is not None and target_project.paused_by_budget:
            target_project.paused_by_budget = False
            cleared = True
    else:
        org = await session.get(Organization, tenant_id)
        if org is not None and org.tenant_paused_by_budget:
            org.tenant_paused_by_budget = False
            cleared = True

    changes: dict[str, Any] = {
        "scope": scope.value,
        "project_id": str(project_id) if project_id is not None else None,
        "cleared": cleared,
        "is_system_admin": is_system_admin,
        "reason": reason,
        "temporary_allowance_until": (
            temporary_allowance_until.isoformat() if temporary_allowance_until is not None else None
        ),
    }
    await write_audit_log(
        session,
        action=BUDGET_PAUSE_OVERRIDE_ACTION,
        actor_user_id=actor_user_id,
        tenant_id=tenant_id,
        resource_type="project" if scope is BudgetScope.PROJECT else "organization",
        resource_id=project_id if scope is BudgetScope.PROJECT else tenant_id,
        changes=changes,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    await session.flush()
    _log.info(
        "budget_pause.override",
        tenant_id=str(tenant_id),
        scope=scope.value,
        project_id=str(project_id) if project_id else None,
        cleared=cleared,
        actor_user_id=str(actor_user_id) if actor_user_id else None,
    )
    return cleared


__all__ = [
    "BUDGET_PAUSE_OVERRIDE_ACTION",
    "BudgetPauseBlock",
    "BudgetPauseRefresh",
    "budget_pause_block",
    "clear_budget_pause",
    "refresh_budget_pause_flags",
]
