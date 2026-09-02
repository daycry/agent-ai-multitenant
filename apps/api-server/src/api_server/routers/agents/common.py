"""Piezas que comparten los sub-módulos de `/agents`. **Sin rutas.**

Aquí vive lo que más de un módulo del paquete necesita, y una pieza que necesita
alguien de fuera: `_clone_agent_capabilities` la usa también `routers/teams.py`
al adoptar un equipo (clona SABER/HACER/SER del agente origen al fork).

Los nombres conservan el guion bajo del monolito a propósito: `routers/teams.py`
importa `_clone_agent_capabilities` por ese nombre exacto y el paquete lo
reexporta, así que renombrarlo aquí sería un cambio de API encubierto.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import AuthPrincipal
from api_server.db.domain import Agent, AgentScope, AgentSkill, AgentTool, Team, TeamMember
from api_server.db.knowledge import AgentKnowledgeBase
from api_server.routers._helpers import get_writable_or_404
from api_server.schemas.agents import AgentCapabilitiesDiff


async def _teams_by_agent(
    session: AsyncSession, agent_ids: list[UUID]
) -> dict[UUID, list[tuple[UUID, str]]]:
    """Pertenencias (team_id, nombre) por agente, en UNA query (ADR 0071). Team es
    tenant-scoped (RLS), así que el join filtra al tenant del request."""
    if not agent_ids:
        return {}
    rows = await session.execute(
        select(TeamMember.agent_id, Team.id, Team.name)
        .join(Team, Team.id == TeamMember.team_id)
        .where(TeamMember.agent_id.in_(agent_ids), Team.deleted_at.is_(None))
        .order_by(Team.name)
    )
    out: dict[UUID, list[tuple[UUID, str]]] = {}
    for agent_id, team_id, team_name in rows.all():
        out.setdefault(agent_id, []).append((team_id, team_name))
    return out


async def _merge_agent_capabilities(
    session: AsyncSession,
    *,
    source_id: UUID,
    fork_id: UUID,
    tenant_id: UUID,
    kinds: set[str],
) -> None:
    """El fork ABSORBE las capacidades actuales del origen (`task_cv_33`):
    reemplaza sus `agent_tools` y/o `agent_skills` por las del origen. Las KBs
    no entran (ADR 0026: las grantea el tenant). Las filas nuevas llevan el
    tenant del fork, nunca el del origen."""
    if "tools" in kinds:
        await session.execute(delete(AgentTool).where(AgentTool.agent_id == fork_id))
        tool_rows = await session.execute(
            select(AgentTool.tool_id, AgentTool.config_override).where(
                AgentTool.agent_id == source_id
            )
        )
        for tool_id, config_override in tool_rows.all():
            session.add(
                AgentTool(
                    agent_id=fork_id,
                    tool_id=tool_id,
                    tenant_id=tenant_id,
                    config_override=dict(config_override) if config_override is not None else None,
                )
            )
    if "skills" in kinds:
        await session.execute(delete(AgentSkill).where(AgentSkill.agent_id == fork_id))
        skill_rows = await session.execute(
            select(AgentSkill.skill_id, AgentSkill.proficiency).where(
                AgentSkill.agent_id == source_id
            )
        )
        for skill_id, proficiency in skill_rows.all():
            session.add(
                AgentSkill(
                    agent_id=fork_id,
                    skill_id=skill_id,
                    tenant_id=tenant_id,
                    proficiency=proficiency,
                )
            )
    await session.flush()


async def _clone_agent_capabilities(
    session: AsyncSession,
    *,
    source_id: UUID,
    fork_id: UUID,
    tenant_id: UUID,
    granted_by: UUID | None,
) -> None:
    """Clona KBs/tools/skills del agente origen al fork (Plan 06.17 task_06_17_12).

    Idempotencia no aplica: el fork es una fila recién creada sin junctions
    previas. Solo se copian las filas que RLS hace visibles al que forkea, de
    modo que el aislamiento multi-tenant queda garantizado por la sesión.
    """
    # SABER — KBs de rol. Re-`tenant_id`amos al del que forkea (la fila origen
    # solo es visible si ya es de ese tenant, pero lo fijamos explícitamente
    # para no depender de la denormalización del origen).
    kb_rows = await session.execute(
        select(AgentKnowledgeBase.kb_id).where(AgentKnowledgeBase.agent_id == source_id)
    )
    for (kb_id,) in kb_rows.all():
        session.add(
            AgentKnowledgeBase(
                agent_id=fork_id,
                kb_id=kb_id,
                tenant_id=tenant_id,
                granted_by=granted_by,
            )
        )

    # HACER — tools asignadas, preservando el config_override por agente.
    tool_rows = await session.execute(
        select(AgentTool.tool_id, AgentTool.config_override).where(AgentTool.agent_id == source_id)
    )
    for tool_id, config_override in tool_rows.all():
        session.add(
            AgentTool(
                agent_id=fork_id,
                tool_id=tool_id,
                # Copia superficial del JSON para que editar el override del
                # fork no mute el del origen vía referencias compartidas.
                config_override=dict(config_override) if config_override is not None else None,
            )
        )

    # SER — skills asignadas (ADR 0050), preservando la proficiency.
    skill_rows = await session.execute(
        select(AgentSkill.skill_id, AgentSkill.proficiency).where(AgentSkill.agent_id == source_id)
    )
    for skill_id, proficiency in skill_rows.all():
        session.add(
            AgentSkill(
                agent_id=fork_id,
                skill_id=skill_id,
                proficiency=proficiency,
            )
        )

    await session.flush()


async def _agent_capability_ids(
    session: AsyncSession,
    agent_id: UUID,
) -> AgentCapabilitiesDiff:
    """Sets de KBs/tools/skills asignados a un agente (Plan 06.17 task_06_17_12).

    Sólo se ven las filas que RLS hace visibles al llamante: para un built-in de
    plataforma, sus KBs (RLS por tenant) no aparecen, lo que es coherente con
    que el fork tampoco las hereda (ADR 0026).
    """
    kb_rows = await session.execute(
        select(AgentKnowledgeBase.kb_id).where(AgentKnowledgeBase.agent_id == agent_id)
    )
    tool_rows = await session.execute(
        select(AgentTool.tool_id).where(AgentTool.agent_id == agent_id)
    )
    skill_rows = await session.execute(
        select(AgentSkill.skill_id).where(AgentSkill.agent_id == agent_id)
    )
    return AgentCapabilitiesDiff(
        kb_ids=sorted(str(r[0]) for r in kb_rows.all()),
        tool_ids=sorted(str(r[0]) for r in tool_rows.all()),
        skill_ids=sorted(str(r[0]) for r in skill_rows.all()),
    )


async def _load_writable_agent_or_403(
    session: AsyncSession,
    agent_id: UUID,
    principal: AuthPrincipal,
    *,
    builtin_detail: str,
) -> Agent:
    """Carga un agente escribible y rechaza los `global_builtin` con 403.

    `get_writable_or_404` ya filtra por tenant vía RLS y devuelve 404 si no lo
    ve. Lo que se añade aquí es la comprobación de scope: los built-in son de la
    plataforma y están vetados a los tenant_admin — se forkea primero y se
    asigna sobre el fork.

    El mensaje lo pone cada llamante porque son tres textos DISTINTOS y visibles
    en la API ("KBs", "tools", "skills"); unificarlos sería cambiar la respuesta.
    """
    agent = await get_writable_or_404(
        session, Agent, agent_id, principal, not_found_detail="agent not found"
    )
    if agent.scope == AgentScope.GLOBAL_BUILTIN.value:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=builtin_detail)
    return agent


async def _load_writable_agent_for_kb(
    session: AsyncSession,
    agent_id: UUID,
    principal: AuthPrincipal,
) -> Agent:
    """Carga un agente para grant/revoke de KBs; rechaza `global_builtin` (403)."""
    return await _load_writable_agent_or_403(
        session,
        agent_id,
        principal,
        builtin_detail=(
            "cannot grant/revoke KBs on a global_builtin agent; fork it first and grant on the fork"
        ),
    )


async def _load_writable_agent_for_tools(
    session: AsyncSession,
    agent_id: UUID,
    principal: AuthPrincipal,
) -> Agent:
    """Carga un agente para asignar tools; rechaza `global_builtin` (403)."""
    return await _load_writable_agent_or_403(
        session,
        agent_id,
        principal,
        builtin_detail=(
            "cannot assign tools to a global_builtin agent; fork it first and assign on the fork"
        ),
    )


async def _load_writable_agent_for_skills(
    session: AsyncSession,
    agent_id: UUID,
    principal: AuthPrincipal,
) -> Agent:
    """Carga un agente para asignar skills; rechaza `global_builtin` (403)."""
    return await _load_writable_agent_or_403(
        session,
        agent_id,
        principal,
        builtin_detail=(
            "cannot assign skills to a global_builtin agent; fork it first and assign on the fork"
        ),
    )
