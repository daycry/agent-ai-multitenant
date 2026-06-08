"""Inyección de skills en el prompt efectivo (Plan 06.18 task_06_18_13).

ADR 0050 (Opción A) cablea el MVP de Skills: una skill asignada a un agente
inyecta su ``prompt_fragment`` en el system prompt EFECTIVO del runtime. Este
módulo es el seam de *lectura* que el orquestador usa (en ``dispatch._route_ai``)
para resolver, a partir de las filas ``agent_skills``, la lista ordenada de
fragmentos a threadear en el spec.

Una sola pieza pura, libre de router/HTTP, para que el orquestador y los tests
la importen igual que ``agent_tools_enforcement.resolve_agent_tool_names``:

  * :func:`resolve_agent_skill_prompt_fragments` — el read async: la lista de
    ``Skill.prompt_fragment`` de las skills asignadas a un agente, o ``None``
    cuando el agente no tiene filas. El sentinel ``None`` es load-bearing: sin
    filas no se emite la clave ``skill_prompt_fragments`` en el spec → el prompt
    actual del runtime queda intacto (backward-compat, mismo patrón que
    ``resolve_agent_tool_names``).

El orden es determinista (por ``name``) para que el prompt resultante sea
estable entre ejecuciones y reproducible en los tests.

Tenant-safe por construcción: bajo una sesión tenant-scoped RLS oculta
agentes/skills de otros tenants, y el orquestador (BYPASSRLS) solo llama a esto
para un agente que ya resolvió dentro del tenant de la tarea.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import AgentSkill, Skill


async def resolve_agent_skill_prompt_fragments(
    session: AsyncSession, agent_id: UUID
) -> list[str] | None:
    """Los ``prompt_fragment`` de las skills asignadas a ``agent_id``, o ``None``.

    Devuelve ``None`` cuando el agente no tiene filas ``agent_skills`` — la señal
    de "sin inyección de prompt" (comportamiento previo, sin regresión). Una
    lista no vacía lleva un fragmento por skill asignada viva, ordenada por
    nombre. Las skills soft-deleted se excluyen.
    """
    rows = await session.execute(
        select(Skill.prompt_fragment)
        .join(AgentSkill, AgentSkill.skill_id == Skill.id)
        .where(AgentSkill.agent_id == agent_id, Skill.deleted_at.is_(None))
        .order_by(Skill.name, Skill.id)
    )
    fragments = [f for f in rows.scalars().all() if f]
    if not fragments:
        return None
    return fragments


__all__ = ["resolve_agent_skill_prompt_fragments"]
