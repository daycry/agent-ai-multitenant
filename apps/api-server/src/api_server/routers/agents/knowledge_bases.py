"""Grants agente ↔ KB (Plan 06.9).

Tres endpoints sobre `/agents/{id}/knowledge-bases` que espejan la junction
proyecto↔KB del Plan 04: mismo patrón de puerta (tenant_admin para grant/revoke,
tenant_member para la lectura) y misma regla de grant explícito (una KB solo es
"visible para el agente" cuando la fila existe).

Los agentes `global_builtin` rechazan grant/revoke con 403: la plataforma los
gestiona por seeds, y el tenant admin los forkea y grantea sobre el fork.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_tenant_session,
    require_tenant_admin,
    require_tenant_member,
)
from api_server.db.domain import Agent
from api_server.db.knowledge import AgentKnowledgeBase, KnowledgeBase
from api_server.routers._helpers import require_tenant_id
from api_server.routers._pagination import limit_query, offset_query
from api_server.routers.agents.common import _load_writable_agent_for_kb
from api_server.schemas.agents import GrantKBRequest

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get(
    "/{agent_id}/knowledge-bases",
    response_model=list[dict[str, object]],
)
async def list_agent_kbs(
    agent_id: UUID,
    limit: int = limit_query(),
    offset: int = offset_query(),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[dict[str, object]]:
    """List KBs granted to this agent (paged via `limit`/`offset`)."""
    # First: make sure the agent is visible to the caller. RLS handles
    # cross-tenant; here we only need to surface 404 on miss instead of
    # an empty list (a hidden grant would otherwise look like "no
    # grants" to the UI).
    agent_q = await session.execute(
        select(Agent).where(Agent.id == agent_id, Agent.deleted_at.is_(None))
    )
    if agent_q.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="agent not found")

    rows = await session.execute(
        select(
            AgentKnowledgeBase.kb_id,
            AgentKnowledgeBase.granted_at,
            AgentKnowledgeBase.granted_by,
            KnowledgeBase.name,
            KnowledgeBase.description,
            KnowledgeBase.embedding_model_id,
        )
        .join(KnowledgeBase, KnowledgeBase.id == AgentKnowledgeBase.kb_id)
        .where(
            AgentKnowledgeBase.agent_id == agent_id,
            KnowledgeBase.deleted_at.is_(None),
        )
        .order_by(KnowledgeBase.name, KnowledgeBase.id)
        .limit(limit)
        .offset(offset)
    )
    return [
        {
            "kb_id": str(r.kb_id),
            "name": r.name,
            "description": r.description,
            "embedding_model_id": r.embedding_model_id,
            "granted_at": r.granted_at.isoformat() if r.granted_at else None,
            "granted_by": str(r.granted_by) if r.granted_by else None,
        }
        for r in rows.all()
    ]


@router.post(
    "/{agent_id}/knowledge-bases",
    response_model=dict[str, object],
    status_code=status.HTTP_201_CREATED,
)
async def grant_kb_to_agent(
    agent_id: UUID,
    payload: GrantKBRequest,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> dict[str, object]:
    """Grant a KB to the agent. Re-granting is a no-op (idempotent)."""
    tenant_id = require_tenant_id(principal)
    kb_id = payload.kb_id

    agent = await _load_writable_agent_for_kb(session, agent_id, principal)

    # Verify the KB exists and is in the caller's tenant. RLS would
    # hide cross-tenant rows; this explicit check converts a silent
    # miss into a clean 404.
    kb_q = await session.execute(
        select(KnowledgeBase).where(KnowledgeBase.id == kb_id, KnowledgeBase.deleted_at.is_(None))
    )
    if kb_q.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="kb not found")

    # Idempotent: if the grant already exists, return 201 with the
    # existing row instead of 409. Matches the kb_projects pattern.
    existing_q = await session.execute(
        select(AgentKnowledgeBase).where(
            AgentKnowledgeBase.agent_id == agent_id,
            AgentKnowledgeBase.kb_id == kb_id,
        )
    )
    existing = existing_q.scalar_one_or_none()
    if existing is not None:
        return {
            "agent_id": str(agent.id),
            "kb_id": str(kb_id),
            "granted_at": existing.granted_at.isoformat() if existing.granted_at else None,
        }

    grant = AgentKnowledgeBase(
        agent_id=agent_id,
        kb_id=kb_id,
        tenant_id=tenant_id,
        granted_by=principal.user_id,
    )
    session.add(grant)
    await session.flush()
    return {
        "agent_id": str(agent.id),
        "kb_id": str(kb_id),
        "granted_at": grant.granted_at.isoformat() if grant.granted_at else None,
    }


@router.delete(
    "/{agent_id}/knowledge-bases/{kb_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_kb_from_agent(
    agent_id: UUID,
    kb_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> None:
    """Revoke the grant. Idempotent: missing row returns 204 anyway."""
    require_tenant_id(principal)
    await _load_writable_agent_for_kb(session, agent_id, principal)

    existing_q = await session.execute(
        select(AgentKnowledgeBase).where(
            AgentKnowledgeBase.agent_id == agent_id,
            AgentKnowledgeBase.kb_id == kb_id,
        )
    )
    existing = existing_q.scalar_one_or_none()
    if existing is not None:
        await session.delete(existing)
        await session.flush()
