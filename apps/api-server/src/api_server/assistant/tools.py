"""Cross-project READ tools for the personal assistant (Plan 10 task_10_14).

Each tool answers one slice of the tenant's global state. The binding
constraints (see the plan) make tenant isolation + RBAC non-negotiable:

  * Every tool runs through the **caller's tenant-scoped session** — the
    same RLS-bound ``AsyncSession`` the request uses (``get_tenant_session``).
    PostgreSQL RLS therefore filters every query to the asking admin's
    tenant. A tool can NEVER return another tenant's rows: the database
    refuses to return them, regardless of any id the model might pass.
  * Tools are READ-ONLY: they only ``SELECT``. The assistant cannot mutate
    state through them.

``tenant_budget_status`` is a typed placeholder: the budget engine lands
in Plan 11 (§28.7). Rather than fabricate numbers, it returns a structured
``available: false`` result the UI/LLM can render as "not available yet".
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Plan, Project, Task

# Tasks still "in the team's hands" — terminal statuses excluded so the
# recent-activity / status views focus on outstanding work.
_ACTIVE_TASK_STATUSES: frozenset[str] = frozenset(
    {
        "backlog",
        "ready",
        "in_progress",
        "in_review",
        "blocked",
        "awaiting_human_approval",
    }
)


@dataclass(frozen=True)
class AssistantToolContext:
    """Everything a tool needs to answer. The ``session`` is the request's
    RLS-bound session, so isolation is enforced by the database, not by
    the tool code."""

    session: AsyncSession
    tenant_id: UUID
    # The asking admin — reserved for finer-grained RBAC scoping (e.g.
    # project-membership filters) without changing the tool signature.
    user_id: UUID


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------
async def _tenant_projects_status(ctx: AssistantToolContext, **_: Any) -> dict[str, Any]:
    """Consolidated count + per-project status of the tenant's projects."""
    result = await ctx.session.execute(
        select(Project.id, Project.name, Project.status)
        .where(Project.deleted_at.is_(None))
        .order_by(Project.created_at)
    )
    rows = result.all()
    by_status: dict[str, int] = {}
    projects: list[dict[str, Any]] = []
    for pid, name, status_ in rows:
        by_status[status_] = by_status.get(status_, 0) + 1
        projects.append({"id": str(pid), "name": name, "status": status_})
    return {"total": len(projects), "by_status": by_status, "projects": projects}


async def _tenant_plans_summary(ctx: AssistantToolContext, **_: Any) -> dict[str, Any]:
    """Plans across all the tenant's projects, grouped by status, with a
    short list of titles per status."""
    result = await ctx.session.execute(
        select(Plan.id, Plan.title, Plan.status, Plan.project_id)
        .where(Plan.deleted_at.is_(None))
        .order_by(Plan.created_at)
    )
    rows = result.all()
    by_status: dict[str, int] = {}
    plans: list[dict[str, Any]] = []
    for pid, title, status_, project_id in rows:
        by_status[status_] = by_status.get(status_, 0) + 1
        plans.append(
            {
                "id": str(pid),
                "title": title,
                "status": status_,
                "project_id": str(project_id),
            }
        )
    pending = [p for p in plans if p["status"] in ("pending_approval", "pending_second_approval")]
    return {
        "total": len(plans),
        "by_status": by_status,
        "pending_approval": pending,
        "plans": plans,
    }


async def _tenant_recent_activity(
    ctx: AssistantToolContext, *, limit: int = 20, **_: Any
) -> dict[str, Any]:
    """The most recently updated non-terminal tasks across the tenant —
    a quick "what is the team working on right now" view."""
    capped = max(1, min(int(limit), 100))
    result = await ctx.session.execute(
        select(Task.id, Task.title, Task.status, Task.project_id, Task.plan_id, Task.updated_at)
        .where(Task.status.in_(_ACTIVE_TASK_STATUSES))
        .order_by(Task.updated_at.desc())
        .limit(capped)
    )
    rows = result.all()
    items = [
        {
            "id": str(tid),
            "title": title,
            "status": status_,
            "project_id": str(project_id),
            "plan_id": (str(plan_id) if plan_id is not None else None),
            "updated_at": updated_at.isoformat() if updated_at is not None else None,
        }
        for tid, title, status_, project_id, plan_id, updated_at in rows
    ]
    # Also surface the active-task count so the assistant can answer
    # "how many open tasks do I have?" without re-querying.
    total_active = await ctx.session.scalar(
        select(func.count()).select_from(Task).where(Task.status.in_(_ACTIVE_TASK_STATUSES))
    )
    return {"active_task_count": int(total_active or 0), "recent": items}


async def _tenant_budget_status(_ctx: AssistantToolContext, **_: Any) -> dict[str, Any]:
    """Typed placeholder — the budget engine lands in Plan 11 (§28.7).

    Returns a structured "not available yet" result rather than fake
    numbers, so the LLM / UI can say so honestly.
    """
    return {
        "available": False,
        "reason": "budget_engine_not_implemented",
        "message": (
            "El motor de presupuesto se implementa en el Plan 11; "
            "todavía no hay cifras de presupuesto disponibles."
        ),
    }


# ---------------------------------------------------------------------------
# Registry + JSON schemas (the shape an LLM tool-calling API expects)
# ---------------------------------------------------------------------------
ToolImpl = Callable[..., Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class ToolEntry:
    """One entry in the assistant tool catalogue: the async implementation
    plus the JSON schema the LLM tool-calling API consumes."""

    impl: ToolImpl
    schema: dict[str, Any]


ASSISTANT_TOOLS: dict[str, ToolEntry] = {
    "tenant_projects_status": ToolEntry(
        impl=_tenant_projects_status,
        schema={
            "name": "tenant_projects_status",
            "description": (
                "Estado consolidado de todos los proyectos del tenant "
                "(conteo total y por estado)."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    ),
    "tenant_plans_summary": ToolEntry(
        impl=_tenant_plans_summary,
        schema={
            "name": "tenant_plans_summary",
            "description": (
                "Resumen de los planes del tenant cross-proyecto, agrupados "
                "por estado, incluyendo los pendientes de aprobación."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    ),
    "tenant_recent_activity": ToolEntry(
        impl=_tenant_recent_activity,
        schema={
            "name": "tenant_recent_activity",
            "description": (
                "Tareas no terminales más recientes del tenant cross-proyecto "
                "(qué está haciendo el equipo ahora) y el total de tareas abiertas."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Máximo de tareas a devolver (1-100).",
                        "minimum": 1,
                        "maximum": 100,
                    }
                },
            },
        },
    ),
    "tenant_budget_status": ToolEntry(
        impl=_tenant_budget_status,
        schema={
            "name": "tenant_budget_status",
            "description": (
                "Estado de presupuesto del tenant. NOTA: el motor de "
                "presupuesto se implementa en el Plan 11; de momento "
                "devuelve un marcador 'no disponible'."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    ),
}


class UnknownToolError(KeyError):
    """The tool name is not in the assistant catalogue."""


async def run_assistant_tool(
    name: str,
    ctx: AssistantToolContext,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch a tool by name. Raises :class:`UnknownToolError` for an
    unknown name (the graph filters to enabled tools, so this only fires
    on a programming error or a hostile model)."""
    entry = ASSISTANT_TOOLS.get(name)
    if entry is None:
        raise UnknownToolError(f"unknown assistant tool {name!r}")
    return await entry.impl(ctx, **(arguments or {}))


def tool_schemas(enabled: tuple[str, ...]) -> list[dict[str, Any]]:
    """Return the JSON schemas for the enabled tools, in catalogue order.
    Fed to the LLM tool-calling API."""
    return [ASSISTANT_TOOLS[name].schema for name in enabled if name in ASSISTANT_TOOLS]


__all__ = [
    "ASSISTANT_TOOLS",
    "AssistantToolContext",
    "ToolEntry",
    "UnknownToolError",
    "run_assistant_tool",
    "tool_schemas",
]
