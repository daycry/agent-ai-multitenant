"""El Hub de Capacidad por agente (Plan 06.17 `task_06_17_08`).

SABER (KBs visibles por nivel rol/stack/plataforma), RECORDAR (memoria por scope
+ el `memory_scope` del agente), SER (modelo configurado) y HACER (set efectivo de
tools). La sección HACER **delega** en la pieza pura `compute_effective_tools` del
Plan 06.18 — NO recalcula la intersección: ésa es la frontera con 06.18.
Read-only y tenant-scoped: RLS oculta agentes cross-tenant, así que un agente
oculto o inexistente devuelve 404.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal, get_tenant_session, require_tenant_member
from api_server.capabilities import (
    CapabilitiesResponse,
    CapabilityRecordar,
    CapabilitySaber,
    CapabilityWarning,
    agent_global_warning,
    build_ser,
    hacer_for_agent,
    kbs_for_agent_role,
    kbs_for_project,
    memory_counts,
    merge_kbs,
    private_memory_warning,
    shared_lineage_warning,
)
from api_server.db.domain import Agent, Project, Team
from api_server.db.platform_settings import resolve_model_config_origin

router = APIRouter(prefix="/agents", tags=["agents"])

_log = structlog.get_logger("api_server.capabilities")


async def _inheritance_levels(
    session: AsyncSession, agent: Agent
) -> tuple[Project | None, dict[str, Any], dict[str, Any]]:
    """El proyecto del agente y los ``model_config`` de proyecto y equipo.

    Los dos consumidores de esta vista —el ORIGEN del modelo efectivo (Ola D /
    ADR 0065) y el aviso de linaje compartido (`task_gov_07`)— necesitan
    exactamente los mismos dos niveles de la cadena ``agent → team → project →
    platform``. Se leen una vez: duplicar la carga fue lo que en su día hizo que
    reviewer e implementador derivaran la cadena por caminos distintos
    (hallazgo H2 del refactor 2026-07-07).
    """
    project_cfg: dict[str, Any] = {}
    team_cfg: dict[str, Any] = {}
    project: Project | None = None
    if agent.project_id is not None:
        project = (
            await session.execute(select(Project).where(Project.id == agent.project_id))
        ).scalar_one_or_none()
        if project is not None:
            project_cfg = dict(project.model_config or {})
            if project.team_id is not None:
                team = (
                    await session.execute(select(Team).where(Team.id == project.team_id))
                ).scalar_one_or_none()
                if team is not None:
                    team_cfg = dict(team.model_config or {})
    return project, project_cfg, team_cfg


async def _effective_provider(
    session: AsyncSession,
    agent: Agent,
    *,
    project_cfg: dict[str, Any],
    team_cfg: dict[str, Any],
) -> str | None:
    """El ``provider`` EFECTIVO del agente tras la cadena de herencia.

    Resuelve por la MISMA función que el dispatch (``resolve_model_config_chain``),
    incluido el default de plataforma: comparar los `model_config` crudos daría
    «no comparten linaje» para dos agentes que en realidad heredan los dos el
    mismo default — el caso más común de todos.
    """
    from api_server.db.platform_settings import (
        get_default_model_config,
        resolve_model_config_chain,
    )

    effective = resolve_model_config_chain(
        dict(agent.model_config or {}),
        team_cfg,
        project_cfg,
        await get_default_model_config(session),
    )
    provider = effective.get("provider")
    return str(provider) if isinstance(provider, str) and provider.strip() else None


async def _reviewer_of(session: AsyncSession, project: Project | None) -> Agent | None:
    """El agente que REVISA el trabajo de este proyecto, o ``None``.

    Misma fuente que usa el planner al materializar tareas
    (``sync_to_kanban._resolve_assignment``): el agente de rol ``reviewer`` del
    equipo del proyecto. Se resuelve por ahí y no por `tasks.reviewer_agent_id`
    porque el Hub es una vista **por agente**, no por tarea, y preguntarle a una
    tarea concreta ataría el aviso al azar de qué tarea se mirase.
    """
    if project is None or project.team_id is None:
        return None
    from api_server.chat.planning_graph import PlanningRole
    from api_server.chat.responder import team_role_agents

    role_agents = await team_role_agents(session, project)
    reviewer_id = role_agents.get(PlanningRole.REVIEWER)
    if reviewer_id is None:
        return None
    return (
        await session.execute(select(Agent).where(Agent.id == reviewer_id))
    ).scalar_one_or_none()


async def _shared_lineage_warning_for(
    session: AsyncSession,
    agent: Agent,
    *,
    project: Project | None,
    project_cfg: dict[str, Any],
    team_cfg: dict[str, Any],
) -> list[CapabilityWarning]:
    """El aviso de linaje compartido de ESTE agente, o ``[]`` (`task_gov_07`).

    Best-effort de verdad, no de comentario: el Hub es la pantalla desde la que
    se configura un agente, y un equipo a medio montar (revisor borrado, rol sin
    agente, `model_config` corrupto) no puede dejar sin Hub a quien está
    justamente arreglándolo. Un aviso informativo que se pierde es un coste
    aceptable; un 500 en la pantalla de configuración, no.
    """
    reviewer = await _reviewer_of(session, project)
    if reviewer is None or reviewer.id == agent.id:
        return []
    try:
        _, reviewer_project_cfg, reviewer_team_cfg = await _inheritance_levels(session, reviewer)
        return shared_lineage_warning(
            agent_provider=await _effective_provider(
                session, agent, project_cfg=project_cfg, team_cfg=team_cfg
            ),
            reviewer_provider=await _effective_provider(
                session, reviewer, project_cfg=reviewer_project_cfg, team_cfg=reviewer_team_cfg
            ),
            reviewer_name=reviewer.name,
        )
    except Exception:
        _log.warning(
            "capabilities.shared_lineage_failed",
            agent_id=str(agent.id),
            reviewer_id=str(reviewer.id),
        )
        return []


@router.get("/{agent_id}/capabilities", response_model=CapabilitiesResponse)
async def get_agent_capabilities(
    agent_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> CapabilitiesResponse:
    """Devuelve el set efectivo REAL de capacidad del agente + avisos honestos.

    SABER es la UNIÓN de las KBs de rol (``agent_knowledge_bases``) y, si el
    agente está atado a un proyecto, las KBs del stack (``kb_projects``). HACER
    compone con ``compute_effective_tools`` (06.18). Avisos honestos: agente
    global sin contexto de proyecto (ADR 0054), modelo no configurado (ADR 0055)
    y ``memory_scope=private`` silencioso.
    """
    agent_q = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
    )
    agent = agent_q.scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    warnings: list[CapabilityWarning] = []

    # SABER: rol mas stack (si hay proyecto). El nivel rol gana si una KB aparece
    # por ambas vias (orden de merge_kbs).
    role_kbs = await kbs_for_agent_role(session, agent_id=agent_id)
    project_kbs = (
        await kbs_for_project(session, project_id=agent.project_id)
        if agent.project_id is not None
        else []
    )
    saber = CapabilitySaber(knowledge_bases=merge_kbs(role_kbs, project_kbs))

    # RECORDAR: el memory_scope del agente + conteo por scope de su proyecto.
    recordar = CapabilityRecordar(
        memory_scope=agent.memory_scope,
        memory=await memory_counts(session, project_id=agent.project_id),
    )
    warnings += private_memory_warning(agent.memory_scope)

    # SER: persona/modelo (ADR 0055).
    ser, ser_warnings = build_ser(agent)
    warnings += ser_warnings
    # Ola D / ADR 0065: nivel que fija el modelo EFECTIVO en la cadena de herencia.
    project, project_cfg, team_cfg = await _inheritance_levels(session, agent)
    ser.model_origin = resolve_model_config_origin(
        dict(agent.model_config or {}), team_cfg, project_cfg
    )

    # `task_gov_07`: ¿este agente y quien revisa su trabajo resuelven modelos del
    # mismo linaje? El aviso no bloquea ni cambia nada — hace visible una
    # decisión que hoy no se ve por ningún sitio.
    warnings += await _shared_lineage_warning_for(
        session, agent, project=project, project_cfg=project_cfg, team_cfg=team_cfg
    )

    # HACER: delega en compute_effective_tools (06.18).
    hacer, hacer_warnings = await hacer_for_agent(session, agent=agent)
    warnings += hacer_warnings

    # Aviso honesto del agente global (ADR 0054).
    warnings += agent_global_warning(agent)

    return CapabilitiesResponse(
        entity_type="agent",
        entity_id=agent.id,
        saber=saber,
        recordar=recordar,
        ser=ser,
        hacer=hacer,
        warnings=warnings,
    )
