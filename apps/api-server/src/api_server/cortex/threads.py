"""Capa de persistencia del hilo del córtex — owner-scoped, BYPASSRLS (F1).

Funciones puras async sobre una :class:`AsyncSession` (la del admin/BYPASSRLS,
que pasa el caller). Las tablas del córtex son **tenant-less**: no hay RLS, así
que **TODO `SELECT`/`UPDATE`/`DELETE` lleva un filtro `owner_user_id` explícito**
(defensa en profundidad; el test cross-owner de F1 es la prueba de mérito).

``tenant_id`` es un discriminante físico para la memoria del owner (Decisión D1),
NO un eje de autorización: se resuelve una vez como el tenant de la membresía
activa más antigua del owner y se persiste en ``cortex_conversations.tenant_id``.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.cortex import CortexConversation, CortexTurn
from api_server.db.models import UserOrganizationMembership


class CortexNoTenantError(Exception):
    """El owner no tiene ninguna membership activa.

    El córtex necesita al menos un tenant para el discriminante físico de su
    memoria (Decisión D1). El router lo traduce a un 409 honesto.
    """


async def resolve_cortex_tenant_id(session: AsyncSession, owner_user_id: UUID) -> UUID:
    """Tenant de la membership ACTIVA más antigua del owner (Decisión D1).

    Lee ``user_org_memberships`` filtrando ``user_id == owner_user_id``,
    ``is_active`` y vivas (``deleted_at IS NULL``), ordena por ``created_at`` y
    toma la primera. Si ninguna → :class:`CortexNoTenantError`.
    """
    stmt = (
        select(UserOrganizationMembership.tenant_id)
        .where(
            UserOrganizationMembership.user_id == owner_user_id,
            UserOrganizationMembership.is_active.is_(True),
            UserOrganizationMembership.deleted_at.is_(None),
        )
        .order_by(UserOrganizationMembership.created_at.asc())
        .limit(1)
    )
    tenant_id = (await session.execute(stmt)).scalar_one_or_none()
    if tenant_id is None:
        raise CortexNoTenantError(
            "el córtex necesita al menos un tenant para su memoria (Decisión D1)"
        )
    return tenant_id


async def create_conversation(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    tenant_id: UUID,
    model_id: str | None = None,
) -> CortexConversation:
    """Crea un hilo del córtex para ``owner_user_id`` (flush, sin commit)."""
    conv = CortexConversation(
        owner_user_id=owner_user_id,
        tenant_id=tenant_id,
        model_id=model_id,
    )
    session.add(conv)
    await session.flush()
    return conv


async def _owned_conversation(
    session: AsyncSession, *, conversation_id: UUID, owner_user_id: UUID
) -> CortexConversation | None:
    """El hilo SOLO si lo posee ``owner_user_id`` (filtro explícito, no-join)."""
    stmt = select(CortexConversation).where(
        CortexConversation.id == conversation_id,
        CortexConversation.owner_user_id == owner_user_id,
        CortexConversation.deleted_at.is_(None),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def append_turn(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    owner_user_id: UUID,
    role: str,
    content: str,
    model_id: str | None = None,
    tools_called: Iterable[str] = (),
    rounds: int = 0,
    reasoning_effort: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> CortexTurn:
    """Añade un turno al hilo — SOLO si ``owner_user_id`` lo posee.

    Verifica la pertenencia ANTES de escribir (SELECT con ``WHERE
    owner_user_id`` explícito); si el hilo no existe o pertenece a otro owner,
    lanza :class:`PermissionError` y NO escribe nada (flush, sin commit).
    """
    conv = await _owned_conversation(
        session, conversation_id=conversation_id, owner_user_id=owner_user_id
    )
    if conv is None:
        raise PermissionError(
            f"conversation {conversation_id} not found or not owned by {owner_user_id}"
        )

    turn = CortexTurn(
        conversation_id=conversation_id,
        owner_user_id=owner_user_id,
        role=role,
        content=content,
        model_id=model_id,
        tools_called=list(tools_called),
        rounds=rounds,
        reasoning_effort=reasoning_effort,
        metadata_=metadata or {},
    )
    session.add(turn)
    await session.flush()
    return turn


async def list_conversations(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    limit: int = 50,
) -> Sequence[CortexConversation]:
    """Hilos vivos del owner, más recientes primero (filtro owner explícito)."""
    stmt = (
        select(CortexConversation)
        .where(
            CortexConversation.owner_user_id == owner_user_id,
            CortexConversation.deleted_at.is_(None),
        )
        .order_by(CortexConversation.updated_at.desc())
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


async def list_turns(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    owner_user_id: UUID,
    limit: int = 100,
) -> Sequence[CortexTurn]:
    """Turnos de un hilo del owner, en orden cronológico.

    Valida la pertenencia del hilo al owner (filtro explícito) antes de leer:
    si el hilo no es del owner → :class:`PermissionError`.
    """
    conv = await _owned_conversation(
        session, conversation_id=conversation_id, owner_user_id=owner_user_id
    )
    if conv is None:
        raise PermissionError(
            f"conversation {conversation_id} not found or not owned by {owner_user_id}"
        )
    stmt = (
        select(CortexTurn)
        .where(
            CortexTurn.conversation_id == conversation_id,
            CortexTurn.owner_user_id == owner_user_id,
        )
        .order_by(CortexTurn.created_at.asc(), CortexTurn.id.asc())
        .limit(limit)
    )
    return (await session.execute(stmt)).scalars().all()


async def recent_history_for_prompt(
    session: AsyncSession,
    *,
    conversation_id: UUID,
    owner_user_id: UUID,
    max_turns: int = 20,
) -> list[dict[str, str]]:
    """Los últimos ``max_turns`` turnos como ``[{role, content}]`` cronológico.

    Toma los más recientes (cota ``max_turns``) y los devuelve en orden
    cronológico ascendente para alimentar el grafo del córtex.
    """
    conv = await _owned_conversation(
        session, conversation_id=conversation_id, owner_user_id=owner_user_id
    )
    if conv is None:
        raise PermissionError(
            f"conversation {conversation_id} not found or not owned by {owner_user_id}"
        )
    # Subselect de los N más recientes (DESC) y reordenado ASC para el prompt.
    stmt = (
        select(CortexTurn.role, CortexTurn.content)
        .where(
            CortexTurn.conversation_id == conversation_id,
            CortexTurn.owner_user_id == owner_user_id,
        )
        .order_by(CortexTurn.created_at.desc(), CortexTurn.id.desc())
        .limit(max_turns)
    )
    rows = list((await session.execute(stmt)).all())
    rows.reverse()
    # Map the domain role 'cortex' → 'assistant' so the LLM adapter sees a
    # standard chat role (it folds any unknown role into 'user', which would
    # mislabel the córtex's own past answers as if the user had written them).
    return [
        {"role": "assistant" if role == "cortex" else "user", "content": content}
        for role, content in rows
    ]


__all__ = [
    "CortexNoTenantError",
    "append_turn",
    "create_conversation",
    "list_conversations",
    "list_turns",
    "recent_history_for_prompt",
    "resolve_cortex_tenant_id",
]
