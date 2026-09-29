"""El Resumen de un proyecto en una respuesta (`task_ui_10`, plan ui-reestructuracion).

Es el dashboard del tenant (`dashboard.tenant`) acotado a UN proyecto, más los **avisos**
del proyecto. Reutiliza lo que ya existe en vez de duplicarlo: el egress sale de
`egress_warnings_for_servers` (la misma función que la ficha y el dashboard), las tools
importadas de `count_imported_tools` (estado derivado, ADR 0166 D4) y el gasto de
`tenant_budget_summary`.

Cada aviso lleva sólo un código, cifras y la ruta de la pestaña donde se arregla; los
textos los pone el panel. Y sólo hay avisos de lo que el dominio **guarda**:

* ``mcp_not_connected`` — servidor OAuth sin token en Vault (mismo criterio que
  ``GET .../oauth/status``).
* ``mcp_no_tools`` — servidor declarado sin ninguna tool importada.
* ``mcp_egress_blocked`` — host externo fuera de la allowlist de la plataforma.
* ``repo_not_synced`` — el proyecto tiene remoto git y el último sondeo del worker
  (``repository_config.last_git_sync``) no existe o no acabó en ``ok``.
* ``budget_paused_project`` — pausa por presupuesto.

**No hay aviso de «rol sin agente»**: el dominio no declara qué roles necesita un proyecto
(ni el equipo ni las tareas guardan roles requeridos), y decidirlo aquí sería inventar una
regla de producto (criterio de cierre 6 del plan).

Todas las consultas llevan ``tenant_id`` además de la RLS de la sesión (principio 1).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from shared_mcp.catalog import uses_oauth
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.platform_settings import get_platform_setting
from api_server.egress.mcp_allowlist import SETTING_KEY as MCP_ALLOWLIST_KEY
from api_server.egress.mcp_discovery import egress_warnings_for_servers
from api_server.mcp.import_tools import count_imported_tools

#: Planes activos que se enseñan con nombre, y runs recientes de la actividad.
PLANS_LIMIT = 10
ACTIVITY_LIMIT = 10
_ACTIVE_PLAN_STATUSES = ("approved", "in_progress", "blocked")

#: ¿Tiene este servidor OAuth de este proyecto un token en Vault?
OAuthConnected = Callable[[str], Awaitable[bool]]


async def _plans(session: AsyncSession, tenant_id: UUID, project_id: UUID) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa_text(
            "SELECT p.id, p.title, p.status, count(t.id) AS total,"
            " count(t.id) FILTER (WHERE t.status = 'done') AS done"
            " FROM plans p LEFT JOIN tasks t ON t.plan_id = p.id AND t.tenant_id = :tid"
            " WHERE p.tenant_id = :tid AND p.project_id = :pid AND p.deleted_at IS NULL"
            " AND p.status = ANY(:active)"
            " GROUP BY p.id ORDER BY p.updated_at DESC LIMIT :limit"
        ),
        {
            "tid": tenant_id,
            "pid": project_id,
            "active": list(_ACTIVE_PLAN_STATUSES),
            "limit": PLANS_LIMIT,
        },
    )
    return [
        {
            "plan_id": str(r.id),
            "title": r.title,
            "status": r.status,
            "tasks_done": int(r.done),
            "tasks_total": int(r.total),
            "url_path": f"/admin/projects/{project_id}/plans/{r.id}",
        }
        for r in rows
    ]


async def _runs(session: AsyncSession, tenant_id: UUID, project_id: UUID) -> dict[str, Any]:
    params = {"tid": tenant_id, "pid": project_id}
    running = (
        await session.execute(
            sa_text(
                "SELECT count(*) FROM executions e JOIN tasks t ON t.id = e.task_id"
                " WHERE e.tenant_id = :tid AND t.tenant_id = :tid AND t.project_id = :pid"
                " AND e.status = 'running'"
            ),
            params,
        )
    ).scalar_one()
    failure = (
        await session.execute(
            sa_text(
                "SELECT e.id, t.title AS task_title FROM executions e"
                " JOIN tasks t ON t.id = e.task_id"
                " WHERE e.tenant_id = :tid AND t.tenant_id = :tid AND t.project_id = :pid"
                " AND e.status IN ('failed', 'aborted')"
                " ORDER BY e.created_at DESC LIMIT 1"
            ),
            params,
        )
    ).first()
    return {
        "running": int(running),
        "last_failure": (
            {
                "task_title": failure.task_title,
                "execution_id": str(failure.id),
                "url_path": f"/admin/executions/{failure.id}",
            }
            if failure is not None
            else None
        ),
        "url_path": f"/admin/projects/{project_id}/tasks",
    }


async def _activity(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa_text(
            "SELECT e.id, e.status, t.title AS task_title, a.name AS agent_name,"
            " e.created_at AS at"
            " FROM executions e JOIN tasks t ON t.id = e.task_id"
            " LEFT JOIN agents a ON a.id = e.agent_id"
            " WHERE e.tenant_id = :tid AND t.tenant_id = :tid AND t.project_id = :pid"
            " ORDER BY e.created_at DESC LIMIT :limit"
        ),
        {"tid": tenant_id, "pid": project_id, "limit": ACTIVITY_LIMIT},
    )
    return [
        {
            "execution_id": str(r.id),
            "status": r.status,
            "task_title": r.task_title,
            "agent_name": r.agent_name,
            "at": r.at.isoformat() if r.at is not None else None,
            "url_path": f"/admin/executions/{r.id}",
        }
        for r in rows
    ]


def _server_names(servers: list[Any]) -> list[str]:
    return [str(s["name"]) for s in servers if isinstance(s, dict) and s.get("name")]


async def _warnings(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    project: Any,
    oauth_connected: OAuthConnected,
) -> list[dict[str, Any]]:
    base = f"/admin/projects/{project_id}"
    warnings: list[dict[str, Any]] = []
    servers = project.mcp_servers if isinstance(project.mcp_servers, list) else []
    if servers:
        names = _server_names(servers)
        unconnected = [
            str(s["name"])
            for s in servers
            if isinstance(s, dict)
            and s.get("name")
            and uses_oauth(s.get("url") if isinstance(s.get("url"), str) else None)
            and not await oauth_connected(str(s["name"]))
        ]
        if unconnected:
            warnings.append(
                {
                    "kind": "mcp_not_connected",
                    "count": len(unconnected),
                    "names": unconnected,
                    "url_path": f"{base}/mcp-servers",
                }
            )
        imported = await count_imported_tools(session, tenant_id=tenant_id, server_names=names)
        empty = [n for n in names if not imported.get(n)]
        if empty:
            warnings.append(
                {
                    "kind": "mcp_no_tools",
                    "count": len(empty),
                    "names": empty,
                    "url_path": f"{base}/mcp-servers",
                }
            )
        allowed = await get_platform_setting(session, MCP_ALLOWLIST_KEY, default=[])
        egress = egress_warnings_for_servers(
            servers, allowed_hosts=allowed if isinstance(allowed, list) else []
        )
        if egress:
            warnings.append(
                {
                    "kind": "mcp_egress_blocked",
                    "count": len(egress),
                    "names": [],
                    "url_path": f"{base}/mcp-servers",
                }
            )
    git = project.git_config if isinstance(project.git_config, dict) else {}
    if git.get("remote_url"):
        repo_cfg = project.repository_config if isinstance(project.repository_config, dict) else {}
        last = repo_cfg.get("last_git_sync")
        if not (isinstance(last, dict) and last.get("status") == "ok"):
            warnings.append({"kind": "repo_not_synced", "count": 1, "names": [], "url_path": base})
    if project.paused_by_budget:
        warnings.append(
            {"kind": "budget_paused_project", "count": 1, "names": [], "url_path": base}
        )
    return warnings


def _project_spend(spend: dict[str, Any], project_id: UUID) -> dict[str, Any]:
    """La fila de gasto de ESTE proyecto dentro del resumen del tenant."""
    for row in spend.get("projects") or []:
        if row.get("project_id") == str(project_id):
            return {"available": True, **row}
    return {"available": False, "reason": "no_budget_configured"}


async def build_project_overview(
    session: AsyncSession,
    tenant_id: UUID,
    project_id: UUID,
    *,
    spend: dict[str, Any],
    oauth_connected: OAuthConnected,
) -> dict[str, Any] | None:
    """El Resumen del proyecto, o ``None`` si no existe en este tenant.

    ``spend`` es el resumen de gasto del tenant (el router lo cachea aparte)."""
    project = (
        await session.execute(
            sa_text(
                "SELECT id, name, status, team_id, mcp_servers, git_config, repository_config,"
                " paused_by_budget FROM projects"
                " WHERE id = :pid AND tenant_id = :tid AND deleted_at IS NULL"
            ),
            {"pid": project_id, "tid": tenant_id},
        )
    ).first()
    if project is None:
        return None
    return {
        "project": {
            "id": str(project.id),
            "name": project.name,
            "status": project.status,
            "team_id": str(project.team_id) if project.team_id is not None else None,
        },
        "plans": await _plans(session, tenant_id, project_id),
        "runs": await _runs(session, tenant_id, project_id),
        "spend": _project_spend(spend, project_id),
        "warnings": await _warnings(session, tenant_id, project_id, project, oauth_connected),
        "activity": await _activity(session, tenant_id, project_id),
        "generated_at": datetime.now(tz=UTC).isoformat(),
    }


__all__ = ["ACTIVITY_LIMIT", "PLANS_LIMIT", "build_project_overview"]
