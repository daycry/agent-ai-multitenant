"""`GET /agents/{id}/prompt-versions` — el historial del prompt (`task_gov_02`).

La mitad de lectura de la tarea: sin ella habría una tabla que se llena y que
nadie mira, que es el patrón que
`docs/03-guides/verificar-antes-de-implementar.md` §5 identifica como el modo de
fallo dominante de esta base (mecanismo entregado, cero consumidores).

El diff se calcula al SERVIR y no se persiste: es una función pura de dos filas
(`agent_prompt_diff`), y guardarlo obligaría a reescribir filas de una tabla
append-only el día que se mejore el renderizado.

**La ruta no colisiona con `/agents/{agent_id}`** —tiene dos segmentos— así que
este módulo puede montarse después de `crud` sin el cuidado que sí exige
`provider-options` (ver el docstring del paquete).
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.agent_persona import effective_prompt_hash
from api_server.agent_prompt_diff import prompt_version_diff
from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_member,
)
from api_server.db.agent_prompt_version_repo import list_prompt_versions
from api_server.db.domain import Agent
from api_server.schemas.agents import (
    AgentPromptVersionEntry,
    AgentPromptVersionsResponse,
)

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("/{agent_id}/prompt-versions", response_model=AgentPromptVersionsResponse)
async def list_agent_prompt_versions(
    agent_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> AgentPromptVersionsResponse:
    """El historial del prompt de ``agent_id``, más reciente primero y con diff.

    404 cuando el agente no existe o no es visible: sin esa comprobación, pedir el
    historial de un agente de otro tenant devolvería 200 con lista vacía, que
    confirma que el id existe. La RLS ya impide leer sus filas; esto impide además
    distinguir «no existe» de «no es tuyo».
    """
    agent = (
        await session.execute(select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None)))
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    rows = await list_prompt_versions(session, agent_id)
    entries: list[AgentPromptVersionEntry] = []
    for index, row in enumerate(rows):
        # La lista viene de más reciente a más antigua, así que «la anterior en el
        # tiempo» es la SIGUIENTE de la lista. La última no tiene anterior.
        previous = rows[index + 1] if index + 1 < len(rows) else None
        entries.append(
            AgentPromptVersionEntry(
                id=row.id,
                agent_id=row.agent_id,
                version=row.version,
                system_prompt=row.system_prompt,
                persona=dict(row.persona or {}),
                prompt_hash=row.prompt_hash,
                changed_by=row.changed_by,
                parent_version_id=row.parent_version_id,
                created_at=row.created_at,
                diff=prompt_version_diff(
                    newer=(row.system_prompt, row.persona),
                    older=(
                        (previous.system_prompt, previous.persona) if previous is not None else None
                    ),
                ),
            )
        )
    return AgentPromptVersionsResponse(
        agent_id=agent_id,
        current_prompt_hash=effective_prompt_hash(agent),
        versions=entries,
    )
