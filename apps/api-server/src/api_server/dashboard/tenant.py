"""El dashboard del tenant en una respuesta (`task_ui_02`, plan ui-reestructuracion).

Seis secciones, en el orden que el operador fijó el 2026-09-09:

1. **pending** — lo que espera a un humano. Sale de `collect_human_queue`, la MISMA
   función que alimenta `/human-queue`: dos consultas para lo mismo acabarían dando
   dos cifras distintas.
2. **projects** — trabajo en curso por proyecto: sólo los proyectos con algo en
   marcha (plan activo o run corriendo).
3. **spend** — dinero contra presupuesto, de `tenant_budget_summary` (Plan 11.1).
4. **issues** — la salud **sólo cuando duele**: una lista vacía es la respuesta buena.
5. **activity** — los runs más recientes de los agentes.
6. **mcp** — los proyectos con servidores MCP y cuántos avisos de egress tienen.

Todas las consultas llevan el predicado ``tenant_id`` además de la RLS de la sesión
(defensa en profundidad, principio rector 1). Los textos para el humano los pone el
panel: aquí sólo viajan códigos y cifras.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.budgets.consumption import tenant_budget_summary
from api_server.db.platform_settings import get_platform_setting
from api_server.egress.mcp_allowlist import SETTING_KEY as MCP_ALLOWLIST_KEY
from api_server.egress.mcp_discovery import egress_warnings_for_servers
from api_server.routers.human_queue import collect_human_queue

#: Cuántos pendientes se enseñan con nombre; el resto va sólo en el contador.
PENDING_PREVIEW = 3
#: Cuántos runs recientes lleva la sección de actividad.
ACTIVITY_LIMIT = 10
#: Estados de plan que cuentan como «en curso».
_ACTIVE_PLAN_STATUSES = ("approved", "in_progress", "blocked")


async def _pending(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    queue = await collect_human_queue(session, str(tenant_id))
    return {
        "total": len(queue),
        "by_kind": dict(Counter(item.kind for item in queue)),
        "oldest": [item.model_dump(mode="json") for item in queue[:PENDING_PREVIEW]],
    }


async def _projects(session: AsyncSession, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa_text(
            "SELECT pr.id, pr.name,"
            " (SELECT count(*) FROM plans p WHERE p.project_id = pr.id"
            "   AND p.tenant_id = :tid AND p.status = ANY(:active)) AS active_plans,"
            " (SELECT count(*) FROM tasks t JOIN plans p ON p.id = t.plan_id"
            "   WHERE p.project_id = pr.id AND t.tenant_id = :tid"
            "   AND p.status = ANY(:active)) AS tasks_total,"
            " (SELECT count(*) FROM tasks t JOIN plans p ON p.id = t.plan_id"
            "   WHERE p.project_id = pr.id AND t.tenant_id = :tid"
            "   AND p.status = ANY(:active) AND t.status = 'done') AS tasks_done,"
            " (SELECT count(*) FROM executions e JOIN tasks t ON t.id = e.task_id"
            "   WHERE t.project_id = pr.id AND e.tenant_id = :tid"
            "   AND e.status = 'running') AS running"
            " FROM projects pr"
            " WHERE pr.tenant_id = :tid AND pr.deleted_at IS NULL"
            " ORDER BY pr.name"
        ),
        {"tid": tenant_id, "active": list(_ACTIVE_PLAN_STATUSES)},
    )
    return [
        {
            "project_id": str(row.id),
            "project_name": row.name,
            "active_plans": int(row.active_plans),
            "tasks_done": int(row.tasks_done),
            "tasks_total": int(row.tasks_total),
            "running": int(row.running),
            "url_path": f"/admin/projects/{row.id}",
        }
        for row in rows
        if row.active_plans or row.running
    ]


async def _activity(session: AsyncSession, tenant_id: UUID) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa_text(
            "SELECT e.id, e.status, t.title AS task_title, pr.name AS project_name,"
            " a.name AS agent_name, coalesce(e.updated_at, e.started_at, e.created_at) AS at"
            " FROM executions e"
            " JOIN tasks t ON t.id = e.task_id"
            " LEFT JOIN projects pr ON pr.id = t.project_id"
            " LEFT JOIN agents a ON a.id = e.agent_id"
            " WHERE e.tenant_id = :tid"
            " ORDER BY coalesce(e.updated_at, e.started_at, e.created_at) DESC"
            " LIMIT :limit"
        ),
        {"tid": tenant_id, "limit": ACTIVITY_LIMIT},
    )
    return [
        {
            "execution_id": str(row.id),
            "status": row.status,
            "task_title": row.task_title,
            "project_name": row.project_name,
            "agent_name": row.agent_name,
            "at": row.at.isoformat() if row.at is not None else None,
            "url_path": f"/admin/executions/{row.id}",
        }
        for row in rows
    ]


async def _mcp_and_issues(
    session: AsyncSession, tenant_id: UUID
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Los proyectos con MCP y los avisos que duelen: egress bloqueado y pausas."""
    allowed = await get_platform_setting(session, MCP_ALLOWLIST_KEY, default=[])
    allowed_hosts = allowed if isinstance(allowed, list) else []
    rows = await session.execute(
        sa_text(
            "SELECT id, name, mcp_servers, paused_by_budget FROM projects"
            " WHERE tenant_id = :tid AND deleted_at IS NULL ORDER BY name"
        ),
        {"tid": tenant_id},
    )
    mcp: list[dict[str, Any]] = []
    issues: list[dict[str, Any]] = []
    for row in rows:
        servers = row.mcp_servers if isinstance(row.mcp_servers, list) else []
        if servers:
            warnings = egress_warnings_for_servers(servers, allowed_hosts=allowed_hosts)
            mcp.append(
                {
                    "project_id": str(row.id),
                    "project_name": row.name,
                    "servers": len(servers),
                    "warnings": len(warnings),
                }
            )
            if warnings:
                issues.append(
                    {
                        "kind": "mcp_egress_blocked",
                        "project_id": str(row.id),
                        "project_name": row.name,
                        "count": len(warnings),
                        "url_path": f"/admin/projects/{row.id}/mcp-servers",
                    }
                )
        if row.paused_by_budget:
            issues.append(
                {
                    "kind": "budget_paused_project",
                    "project_id": str(row.id),
                    "project_name": row.name,
                    "count": 1,
                    "url_path": f"/admin/projects/{row.id}",
                }
            )
    tenant_paused = (
        await session.execute(
            sa_text("SELECT tenant_paused_by_budget FROM organizations WHERE id = :tid"),
            {"tid": tenant_id},
        )
    ).scalar_one_or_none()
    if tenant_paused:
        issues.insert(
            0,
            {
                "kind": "budget_paused_tenant",
                "project_id": None,
                "project_name": None,
                "count": 1,
                "url_path": "/admin/tenant-stats",
            },
        )
    return mcp, issues


async def build_tenant_dashboard(session: AsyncSession, tenant_id: UUID) -> dict[str, Any]:
    """Las seis secciones del dashboard del tenant, en su orden."""
    mcp, issues = await _mcp_and_issues(session, tenant_id)
    return {
        "pending": await _pending(session, tenant_id),
        "projects": await _projects(session, tenant_id),
        "spend": await tenant_budget_summary(session, tenant_id=tenant_id),
        "issues": issues,
        "activity": await _activity(session, tenant_id),
        "mcp": mcp,
        "generated_at": datetime.now(tz=UTC).isoformat(),
    }


__all__ = ["ACTIVITY_LIMIT", "PENDING_PREVIEW", "build_tenant_dashboard"]
