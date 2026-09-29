"""La pestaña Equipo de un proyecto y sus huecos de rol (`task_ui_12`, plan ui-reestructuracion).

**Hueco de rol** = rol que pide alguna tarea de un plan ACTIVO del proyecto
(``plans.specification["tasks"][i]["role"]``, ADR 0091) y que ningún agente del equipo
asignado cubre (``team_role_agents``, la misma función con la que el planificador reparte
las tareas). Es la única definición: el aviso ``role_without_agent`` del Resumen
(`dashboard.project`) y la pestaña Equipo llaman a :func:`role_gaps`.

Un rol que no es un ``PlanningRole`` (un typo del LLM) no cuenta: el dominio ya lo descarta al
materializar el plan (``sync_to_kanban._resolve_assignment``), así que no hay agente que lo
pueda cubrir y avisar de él sería un aviso imposible de resolver.

Todas las consultas llevan ``tenant_id`` además de la RLS de la sesión (principio 1).
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.chat.planning_graph import PlanningRole
from api_server.chat.responder import team_role_agents
from api_server.db.domain import Project

#: Mismos estados que los «planes activos» del Resumen (`dashboard.project`).
ACTIVE_PLAN_STATUSES = ("approved", "in_progress", "blocked")

#: Agentes del tenant que se ofrecen por hueco (la ficha del agente es el destino).
CANDIDATES_LIMIT = 5


async def _requested_roles(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> dict[str, list[str]]:
    """``{rol: [títulos de los planes activos que lo piden]}`` (sólo roles del dominio)."""
    rows = await session.execute(
        sa_text(
            "SELECT title, specification FROM plans"
            " WHERE tenant_id = :tid AND project_id = :pid AND deleted_at IS NULL"
            " AND status = ANY(:active) ORDER BY updated_at DESC"
        ),
        {"tid": tenant_id, "pid": project_id, "active": list(ACTIVE_PLAN_STATUSES)},
    )
    valid = {r.value for r in PlanningRole}
    requested: dict[str, list[str]] = {}
    for title, spec in rows:
        tasks = spec.get("tasks") if isinstance(spec, dict) else None
        for task in tasks if isinstance(tasks, list) else []:
            role = str(task.get("role") or "").strip() if isinstance(task, dict) else ""
            if role in valid and title not in requested.setdefault(role, []):
                requested[role].append(title)
    return requested


async def load_project(session: AsyncSession, tenant_id: UUID, project_id: UUID) -> Project | None:
    return (
        await session.execute(
            select(Project).where(
                Project.id == project_id,
                Project.tenant_id == tenant_id,
                Project.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()


async def role_gaps(
    session: AsyncSession, tenant_id: UUID, project: Project
) -> list[dict[str, Any]]:
    """Los huecos de rol del proyecto: ``[{"role", "plans"}]``, ordenados por rol."""
    requested = await _requested_roles(session, tenant_id, project.id)
    if not requested:
        return []
    covered = {role.value for role in await team_role_agents(session, project)}
    return [
        {"role": role, "plans": requested[role]}
        for role in sorted(requested)
        if role not in covered
    ]


async def _members(session: AsyncSession, tenant_id: UUID, team_id: UUID) -> list[dict[str, Any]]:
    rows = await session.execute(
        sa_text(
            "SELECT a.id, a.name, a.role, a.agent_type, tm.is_team_leader"
            " FROM team_members tm JOIN agents a ON a.id = tm.agent_id"
            " WHERE tm.team_id = :team AND tm.tenant_id = :tid"
            # Mismo criterio de visibilidad que `/teams/{id}` (RLS `agents_global_builtin_read`):
            # los del tenant y los built-in de plataforma, nunca los de otro tenant.
            " AND (a.tenant_id = :tid OR a.scope = 'global_builtin')"
            " AND a.deleted_at IS NULL"
            " ORDER BY a.role, tm.assignment_priority, a.name"
        ),
        {"team": team_id, "tid": tenant_id},
    )
    return [
        {
            "agent_id": str(r.id),
            "name": r.name,
            "role": r.role,
            "agent_type": r.agent_type,
            "is_team_leader": bool(r.is_team_leader),
            "url_path": f"/admin/agents/{r.id}",
        }
        for r in rows
    ]


async def _candidates(
    session: AsyncSession, tenant_id: UUID, role: str, team_id: UUID | None
) -> list[dict[str, str]]:
    """Agentes globales del tenant de ese rol que aún no están en el equipo del proyecto
    (los de alcance de proyecto no se reasignan a otro equipo)."""
    rows = await session.execute(
        sa_text(
            "SELECT a.id, a.name FROM agents a"
            " WHERE a.tenant_id = :tid AND a.role = :role AND a.deleted_at IS NULL"
            " AND a.scope IN ('global_builtin', 'global_tenant_template')"
            " AND NOT EXISTS (SELECT 1 FROM team_members tm"
            "   WHERE tm.agent_id = a.id AND tm.team_id = :team AND tm.tenant_id = :tid)"
            " ORDER BY a.name LIMIT :limit"
        ),
        {"tid": tenant_id, "role": role, "team": team_id, "limit": CANDIDATES_LIMIT},
    )
    return [
        {"agent_id": str(r.id), "name": r.name, "url_path": f"/admin/agents/{r.id}"} for r in rows
    ]


async def build_project_team(
    session: AsyncSession, tenant_id: UUID, project_id: UUID
) -> dict[str, Any] | None:
    """La pestaña Equipo, o ``None`` si el proyecto no existe en este tenant."""
    project = await load_project(session, tenant_id, project_id)
    if project is None:
        return None
    team: dict[str, Any] | None = None
    members: list[dict[str, Any]] = []
    if project.team_id is not None:
        name = (
            await session.execute(
                sa_text("SELECT name FROM teams WHERE id = :team AND tenant_id = :tid"),
                {"team": project.team_id, "tid": tenant_id},
            )
        ).scalar_one_or_none()
        if name is not None:
            team = {"id": str(project.team_id), "name": name}
            members = await _members(session, tenant_id, project.team_id)
    gaps = await role_gaps(session, tenant_id, project)
    for gap in gaps:
        gap["candidates"] = await _candidates(session, tenant_id, gap["role"], project.team_id)
    return {"team": team, "members": members, "role_gaps": gaps}


__all__ = ["ACTIVE_PLAN_STATUSES", "build_project_team", "load_project", "role_gaps"]
