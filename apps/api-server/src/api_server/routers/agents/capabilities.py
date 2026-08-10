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
)
from api_server.db.domain import Agent, Project, Team

router = APIRouter(prefix="/agents", tags=["agents"])


async def _resolve_model_origin(session: AsyncSession, agent: Agent) -> str:
    """Nivel que fija el modelo EFECTIVO del agente en la cadena de herencia
    (Ola D / ADR 0065): carga el proyecto del agente y su equipo para resolver
    ``agent → team → project → platform``."""
    from api_server.db.platform_settings import resolve_model_config_origin

    project_cfg: dict[str, Any] = {}
    team_cfg: dict[str, Any] = {}
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
    return resolve_model_config_origin(dict(agent.model_config or {}), team_cfg, project_cfg)


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
    ser.model_origin = await _resolve_model_origin(session, agent)

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
