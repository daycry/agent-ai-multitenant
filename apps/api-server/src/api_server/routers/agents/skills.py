"""Asignación de skills al agente (Plan 06.18 `task_06_18_13`, ADR 0050 Opción A).

Espejo del patrón de `agent_tools` / grants de KB. Las skills son declarativas: el
`PUT` reemplaza el conjunto entero del agente en una sola transacción. Una lista
vacía limpia todas las filas (→ sin inyección de `prompt_fragment`, comportamiento
previo intacto).

Reglas de scope (ADR 0050, mismas que tools/KB):
  * built-in (`is_builtin`)     -> asignable a cualquier agente.
  * custom (`is_builtin=false`) -> solo del tenant del agente; RLS oculta las de
      otro tenant, así que un lookup vacío se devuelve como 422 limpio.
  * agente `global_builtin`     -> 403 (forkear primero, plataforma-managed).

El `prompt_fragment` de las skills asignadas se inyecta en el system prompt
EFECTIVO del runtime (dispatch.py -> spec -> agent_runtime); el endpoint solo
persiste la asignación.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.db.domain import Agent, AgentSkill, Skill
from api_server.routers._helpers import require_tenant_id
from api_server.routers.agents.common import _load_writable_agent_for_skills
from api_server.schemas.agents import AgentSkillResponse, SetAgentSkillsRequest

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("/{agent_id}/skills", response_model=list[AgentSkillResponse])
async def list_agent_skills(
    agent_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[AgentSkillResponse]:
    """Lista las skills asignadas al agente vía la junction `agent_skills`.

    Un agente sin filas devuelve ``[]`` (sin inyección de prompt). 404 sobre un
    agente oculto/inexistente para no aparentar "sin asignaciones".
    """
    agent_q = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
    )
    if agent_q.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    rows = await session.execute(
        select(Skill)
        .join(AgentSkill, AgentSkill.skill_id == Skill.id)
        .where(AgentSkill.agent_id == agent_id, Skill.deleted_at.is_(None))
        .order_by(Skill.name, Skill.id)
    )
    return [
        AgentSkillResponse(
            skill_id=skill.id,
            name=skill.name,
            category=skill.category,
            description=skill.description,
            prompt_fragment=skill.prompt_fragment,
            is_builtin=skill.is_builtin,
        )
        for skill in rows.scalars().all()
    ]


@router.put("/{agent_id}/skills", response_model=list[AgentSkillResponse])
async def set_agent_skills(
    agent_id: UUID,
    payload: SetAgentSkillsRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[AgentSkillResponse]:
    """Reemplaza declarativamente las skills del agente (tenant_admin).

    El conjunto deseado se valida por scope, luego se borran las filas
    `agent_skills` existentes y se inserta el nuevo conjunto en la misma
    transacción. Devuelve las asignaciones resultantes.
    """
    require_tenant_id(principal)
    await _load_writable_agent_for_skills(session, agent_id, principal)

    requested = {entry.skill_id for entry in payload.skills}

    # Carga todas las skills pedidas en una query. RLS limita el resultado a los
    # built-ins de plataforma + las del tenant, así que una skill custom de otro
    # tenant simplemente no aparece — la atrapa el chequeo de "missing" abajo.
    skills_by_id: dict[UUID, Skill] = {}
    if requested:
        skill_rows = await session.execute(
            select(Skill).where(Skill.id.in_(requested), Skill.deleted_at.is_(None))
        )
        skills_by_id = {skill.id: skill for skill in skill_rows.scalars().all()}

    missing = [skill_id for skill_id in requested if skill_id not in skills_by_id]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "unknown or non-assignable skill_id(s): "
                + ", ".join(str(skill_id) for skill_id in missing)
            ),
        )

    # Reemplazo transaccional: borra las filas viejas, inserta las nuevas.
    await session.execute(delete(AgentSkill).where(AgentSkill.agent_id == agent_id))
    for skill_id in requested:
        session.add(AgentSkill(agent_id=agent_id, skill_id=skill_id))
    await session.flush()

    return [
        AgentSkillResponse(
            skill_id=skill.id,
            name=skill.name,
            category=skill.category,
            description=skill.description,
            prompt_fragment=skill.prompt_fragment,
            is_builtin=skill.is_builtin,
        )
        for skill in sorted(skills_by_id.values(), key=lambda s: (s.name, str(s.id)))
    ]
