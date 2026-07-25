"""`/conversations` and `/projects/{project_id}/conversations` endpoints
(Plan 03 task_03_03).

REST shape:

  POST   /projects/{project_id}/conversations   create conversation
  GET    /projects/{project_id}/conversations   list project conversations
  GET    /conversations/{id}                    one conversation
  PUT    /conversations/{id}                    update title / current_mode
  DELETE /conversations/{id}                    soft-delete

  POST   /conversations/{id}/messages           post a message
  GET    /conversations/{id}/messages           list messages (paginated)

Mode change side-effect: a PUT that flips ``current_mode`` posts an
automatic ``system`` message ("modo cambiado: planning -> discussion")
so the chat feed keeps a visible audit trail of every mode switch. The
WebSocket `/ws/conversation/{id}` tails the per-conversation Redis
stream so connected browsers see new messages live.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.asyncio import Redis
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.auth.deps import (
    AuthPrincipal,
    get_redis,
    get_tenant_session,
    require_tenant_member,
    schedule_after_commit,
)
from api_server.chat.modes import list_chat_modes
from api_server.chat.responder import schedule_reply
from api_server.db.conversation import (
    ChatMode,
    Conversation,
    Message,
    MessageAuthorKind,
)
from api_server.db.domain import Project
from api_server.events import (
    EVENT_CONVERSATION_MODE_CHANGED,
    EVENT_MESSAGE_CREATED,
    delete_conversation_stream,
    publish_conversation_event,
)
from api_server.llm_providers.vault import LLMProviderVaultStore
from api_server.routers._helpers import (
    apply_partial_update,
    get_writable_or_404,
    require_project_active,
    require_tenant_id,
    soft_delete,
)
from api_server.routers.llm_providers import get_provider_vault_store
from api_server.schemas.conversations import (
    ChatModeResponse,
    ConversationCreateRequest,
    ConversationResponse,
    ConversationUpdateRequest,
    MessageCreateRequest,
    MessageResponse,
    to_conversation_response,
    to_message_response,
)

# Two routers because the prefix differs (project-scoped vs flat).
project_conversations_router = APIRouter(
    prefix="/projects/{project_id}/conversations", tags=["conversations"]
)
conversations_router = APIRouter(prefix="/conversations", tags=["conversations"])
# Catalogo de modos de chat para la UI (Plan 06.17 task_06_17_11): un GET
# read-only que la seccion Persona consume para componer el "prompt efectivo"
# (rol + modo) sin hardcodear los prompts de modo en el frontend.
chat_modes_router = APIRouter(prefix="/chat-modes", tags=["conversations"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _verify_project_visible(session: AsyncSession, project_id: UUID) -> Project:
    """RLS hides cross-tenant projects; this turns a silent 0-row SELECT
    into an explicit 404 instead of a downstream FK error."""
    result = await session.execute(
        select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="project not found")
    return project


async def _load_conversation(session: AsyncSession, conversation_id: UUID) -> Conversation:
    """Load a non-deleted conversation; RLS does the tenant filtering."""
    result = await session.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.deleted_at.is_(None),
        )
    )
    conv = result.scalar_one_or_none()
    if conv is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return conv


async def _publish_message_event(redis: Redis, message: Message) -> None:
    """Best-effort publish to the per-conversation Redis stream."""
    await publish_conversation_event(
        redis,
        str(message.conversation_id),
        event_type=EVENT_MESSAGE_CREATED,
        payload={
            "message_id": str(message.id),
            "author_kind": message.author_kind,
            "author_user_id": (
                str(message.author_user_id) if message.author_user_id is not None else None
            ),
            "author_agent_id": (
                str(message.author_agent_id) if message.author_agent_id is not None else None
            ),
            "content": message.content,
            "mode": message.mode,
            "attachments": message.attachments,
            "is_summary": message.is_summary,
        },
    )


# ===========================================================================
# Conversation endpoints
# ===========================================================================
@project_conversations_router.post(
    "", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED
)
async def create_conversation(
    project_id: UUID,
    payload: ConversationCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> ConversationResponse:
    tenant_id = require_tenant_id(principal)
    await _verify_project_visible(session, project_id)

    conv = Conversation(
        tenant_id=tenant_id,
        project_id=project_id,
        title=payload.title,
        current_mode=payload.current_mode.value,
        custom_mode_name=payload.custom_mode_name,
        created_by=principal.user_id,
    )
    session.add(conv)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc.orig)) from exc
    await session.refresh(conv)
    return to_conversation_response(conv)


@project_conversations_router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    project_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[ConversationResponse]:
    await _verify_project_visible(session, project_id)
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.project_id == project_id,
            Conversation.deleted_at.is_(None),
        )
        .order_by(Conversation.created_at)
    )
    return [to_conversation_response(c) for c in result.scalars().all()]


@conversations_router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID,
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> ConversationResponse:
    conv = await _load_conversation(session, conversation_id)
    return to_conversation_response(conv)


@conversations_router.put("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: UUID,
    payload: ConversationUpdateRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    redis: Redis = Depends(get_redis),
) -> ConversationResponse:
    require_tenant_id(principal)
    conv = await get_writable_or_404(
        session,
        Conversation,
        conversation_id,
        principal,
        not_found_detail="conversation not found",
    )

    old_mode = conv.current_mode
    new_mode = payload.current_mode.value if payload.current_mode is not None else None

    apply_partial_update(conv, payload, enum_fields=("current_mode",))

    # If current_mode actually flipped, drop a system message banner and
    # broadcast a mode-changed event onto the conversation stream so live
    # clients can render the change.
    if new_mode is not None and new_mode != old_mode:
        system_msg = Message(
            tenant_id=conv.tenant_id,
            conversation_id=conv.id,
            author_kind=MessageAuthorKind.SYSTEM.value,
            content=f"Modo cambiado: {old_mode} -> {new_mode}",
            mode=new_mode,
            attachments=[],
        )
        session.add(system_msg)
        await session.flush()
        await session.refresh(system_msg)
        await _publish_message_event(redis, system_msg)
        await publish_conversation_event(
            redis,
            str(conv.id),
            event_type=EVENT_CONVERSATION_MODE_CHANGED,
            payload={
                "conversation_id": str(conv.id),
                "old_mode": old_mode,
                "new_mode": new_mode,
            },
        )

    await session.flush()
    await session.refresh(conv)
    return to_conversation_response(conv)


@conversations_router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    conversation_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    redis: Redis = Depends(get_redis),
) -> None:
    require_tenant_id(principal)
    conv = await get_writable_or_404(
        session,
        Conversation,
        conversation_id,
        principal,
        not_found_detail="conversation not found",
    )
    # Hard-delete the messages so deleting a chat actually removes its data from
    # the DB (not just hiding a soft-deleted conversation with its messages left
    # behind as orphan rows). The conversation row itself stays soft-deleted as a
    # lightweight audit marker (it drops out of every listing via deleted_at).
    await session.execute(delete(Message).where(Message.conversation_id == conv.id))
    await soft_delete(session, conv)
    # Drop the live stream too — no orphan events left behind in Redis.
    await delete_conversation_stream(redis, str(conv.id))


@conversations_router.delete("/{conversation_id}/messages", status_code=status.HTTP_204_NO_CONTENT)
async def clear_messages(
    conversation_id: UUID,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    redis: Redis = Depends(get_redis),
) -> None:
    """Clear ALL messages of a conversation, keeping the conversation itself.

    Lets the operator empty an accumulated chat so it doesn't pile up and the team
    starts the next turn with FRESH context (the responder loads the conversation's
    recent messages as history). Messages have no soft-delete, so this hard-deletes
    them. RLS-scoped: ``get_writable_or_404`` rejects a conversation the caller can't
    see (cross-tenant → 404), and the delete is bounded to that conversation.
    """
    require_tenant_id(principal)
    conv = await get_writable_or_404(
        session,
        Conversation,
        conversation_id,
        principal,
        not_found_detail="conversation not found",
    )
    await session.execute(delete(Message).where(Message.conversation_id == conv.id))
    # Clear the live stream too so cleared messages can't reappear as Redis ghosts.
    await delete_conversation_stream(redis, str(conv.id))


# ===========================================================================
# Message endpoints
# ===========================================================================
@conversations_router.post(
    "/{conversation_id}/messages",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
)
async def post_message(
    conversation_id: UUID,
    payload: MessageCreateRequest,
    principal: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
    redis: Redis = Depends(get_redis),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> MessageResponse:
    tenant_id = require_tenant_id(principal)

    # This REST surface is the HUMAN one (require_tenant_member). Agent/system messages
    # are authored server-side by the responder (chat/responder.py → _persist_and_publish)
    # and by the mode-change notice, never through here. A user posting author_kind!='user'
    # is impersonating an agent — and could forge the finish_planning attachment that
    # materialises an attacker-controlled plan (plans.py:_draft_from_conversation). Reject it.
    if payload.author_kind != MessageAuthorKind.USER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="solo se pueden publicar mensajes con author_kind='user' por esta vía",
        )

    conv = await _load_conversation(session, conversation_id)

    # P1-01: el chat del equipo se detiene con el proyecto — cada mensaje de
    # usuario dispara una respuesta LLM (coste) y puede materializar un plan.
    project = (
        await session.execute(select(Project).where(Project.id == conv.project_id))
    ).scalar_one_or_none()
    if project is not None:
        require_project_active(project)

    # Resolve author_user_id from the principal when the caller is a
    # human user and didn't pass it explicitly.
    author_user_id: UUID | None
    if payload.author_kind == MessageAuthorKind.USER and payload.author_user_id is None:
        if principal.user_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="cannot resolve author_user_id from principal",
            )
        author_user_id = principal.user_id
    else:
        author_user_id = payload.author_user_id

    message = Message(
        tenant_id=tenant_id,
        conversation_id=conv.id,
        author_kind=payload.author_kind.value,
        author_user_id=author_user_id,
        author_agent_id=payload.author_agent_id,
        content=payload.content,
        mode=conv.current_mode,
        attachments=payload.attachments,
        related_plan_id=payload.related_plan_id,
        is_summary=payload.is_summary,
    )
    session.add(message)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc.orig)) from exc
    await session.refresh(message)
    await _publish_message_event(redis, message)
    # The team replies to a USER message (Plan 04 wiring): planning → multi-agent
    # planning sub-graph; discussion/execution → a single team reply. Only USER
    # messages trigger a reply, so the team never answers itself.
    #
    # Scheduled via ``schedule_after_commit`` (NOT BackgroundTasks): in FastAPI the
    # yield-dependency commit runs AFTER background tasks, so a BackgroundTask would
    # read the not-yet-committed message and respond to stale/empty history. The
    # after-commit factory spawns a detached task, so the POST does not block on the
    # LLM call. Capture plain values now — the ORM ``conv`` is expired post-commit.
    if payload.author_kind == MessageAuthorKind.USER:
        schedule_after_commit(
            session,
            schedule_reply(
                conversation_id=conv.id,
                tenant_id=tenant_id,
                mode=conv.current_mode,
                vault=vault,
                redis=redis,
            ),
        )
    return to_message_response(message)


@conversations_router.get("/{conversation_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: UUID,
    limit: int = Query(default=50, ge=1, le=500),
    after: UUID | None = Query(
        default=None,
        description=(
            "Return only messages whose UUID is strictly greater than this"
            " value. UUID v7 is timestamp-sortable so this paginates"
            " chronologically without extra columns."
        ),
    ),
    before: UUID | None = Query(
        default=None,
        description=(
            "Return the messages immediately BEFORE this UUID (older ones), newest"
            " of them last. Backward pagination for a chat that scrolls up; ignored"
            " when `after` is set."
        ),
    ),
    _: AuthPrincipal = Depends(require_tenant_member),
    session: AsyncSession = Depends(get_tenant_session),
) -> list[MessageResponse]:
    """La ventana del chat. Sin cursor devuelve los mensajes MÁS RECIENTES.

    A-01: esto ordenaba ASC con `limit`, así que devolvía los N PRIMEROS. Pasada
    la ventana el feed se quedaba congelado en el arranque de la conversación, el
    botón «Generar Plan» —que mira el último mensaje `agent`— desaparecía para
    siempre, y el poll de respaldo evaluaba un mensaje viejo. Un chat quiere su
    cola, no su cabecera.

    Tres modos, todos devolviendo orden cronológico ascendente:
      * sin cursor  → los `limit` más recientes,
      * `after`     → los `limit` siguientes (hacia delante; el que ya existía),
      * `before`    → los `limit` anteriores (hacia atrás, para el scroll up).
    """
    # Verify the conversation exists (also enforces RLS): otherwise a
    # tenant-B caller asking for tenant-A's id would get [] rather than 404.
    await _load_conversation(session, conversation_id)

    stmt = select(Message).where(Message.conversation_id == conversation_id)
    if after is not None:
        # Hacia delante desde el cursor: la cabecera de ese tramo ya es la que se
        # quiere, así que el orden de la consulta ya es el final.
        rows = (
            await session.execute(stmt.where(Message.id > after).order_by(Message.id).limit(limit))
        ).scalars()
        return [to_message_response(m) for m in rows]
    # Sin cursor (los más recientes) y `before` (los anteriores a uno dado) se
    # resuelven igual: tomar por la COLA con DESC y revertir para devolver
    # cronológico. Sin el DESC la cláusula `limit` recorta por el lado equivocado.
    if before is not None:
        stmt = stmt.where(Message.id < before)
    rows_desc = (await session.execute(stmt.order_by(Message.id.desc()).limit(limit))).scalars()
    return [to_message_response(m) for m in reversed(list(rows_desc))]


# ===========================================================================
# Chat-mode catalog (Plan 06.17 task_06_17_11)
# ===========================================================================
@chat_modes_router.get("", response_model=list[ChatModeResponse])
async def list_chat_mode_catalog(
    _: AuthPrincipal = Depends(require_tenant_member),
) -> list[ChatModeResponse]:
    """Catalogo de modos de chat para la seccion Persona del agente.

    Devuelve los tres modos built-in (con su ``system_prompt`` real, fuente
    unica para componer el "prompt efectivo" rol+modo) y el modo ``custom``
    marcado ``available=False`` ("No disponible aun"). No toca la base de datos:
    el catalogo es estatico (``api_server.chat.modes``); requiere autenticacion
    de miembro del tenant como el resto de la superficie de chat.
    """
    return [
        ChatModeResponse(
            name=m.name,
            label_es=m.label_es,
            label_en=m.label_en,
            system_prompt=m.system_prompt,
            available=m.available,
        )
        for m in list_chat_modes()
    ]


# Keep `ChatMode` reachable from this module so external callers can
# import all chat-router public surface from one place.
__all__ = [
    "ChatMode",
    "chat_modes_router",
    "conversations_router",
    "project_conversations_router",
]
