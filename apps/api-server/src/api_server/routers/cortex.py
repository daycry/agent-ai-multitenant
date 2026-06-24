"""Endpoints ``/owner/cortex/*`` — córtex conversacional del System Owner (F1, ADR 0074).

REST:

  POST /owner/cortex/turns          un turno del córtex (abre hilo si no hay id)
  GET  /owner/cortex/turns          los turnos de un hilo del owner (cronológico)
  GET  /owner/cortex/conversations  los hilos del owner (más reciente primero)

ACCESO: todos ``Depends(require_system_owner)`` — DB-authoritative (re-lee
``users.is_system_owner`` por request; el claim ``own`` del JWT es sólo una pista,
ADR 0074). Un ``tenant_admin`` que NO sea owner recibe 403 aunque forje el claim.

AISLAMIENTO (excepción consciente al Principio 1): las tablas del córtex son
tenant-less sobre BYPASSRLS — NO hay RLS. La sesión se abre con
``get_admin_sessionmaker()`` y TODO acceso a hilos/turnos pasa por
:mod:`api_server.cortex.threads`, que filtra ``owner_user_id`` explícito en cada
``SELECT``/``UPDATE``. El ``tenant_id`` (Decisión D1) es sólo el discriminante
físico que la memoria (``memory_entries``) necesita, NO un eje de autorización.

El modelo LLM se inyecta vía ``get_cortex_model`` (espejo exacto de
``get_assistant_model``), que los tests sobreescriben con un
``ScriptedAssistantModel`` — ningún proveedor real se contacta. El factory por
defecto resuelve ``cortex.default_model`` y construye el provider; un 503 honesto
(no un 500) si no hay nada configurado o el SDK/credencial no está disponible.

Cableado del turno (Tarea 10): resolver tenant (D1) → persistir turno ``user`` →
recall híbrido (``cortex_recall``) → ``augment_cortex_prompt`` → ``run_cortex_turn``
con ``chat_history=recent_history_for_prompt`` → persistir turno ``cortex``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from shared_llm.exceptions import AuthError, LLMError, RateLimitError

from api_server.assistant.graph import AssistantModelClient
from api_server.assistant.model_config import to_provider_model_name
from api_server.auth.deps import AuthPrincipal, require_system_owner
from api_server.cortex.graph import run_cortex_turn
from api_server.cortex.memory import CORTEX_RECALL_LIMIT, augment_cortex_prompt, cortex_recall
from api_server.cortex.model_config import (
    CortexModelUnavailableError,
    build_cortex_model,
    resolve_cortex_model,
)
from api_server.cortex.threads import (
    CortexNoTenantError,
    append_turn,
    create_conversation,
    list_conversations,
    list_turns,
    recent_history_for_prompt,
    resolve_cortex_tenant_id,
)
from api_server.cortex.tools import CORTEX_TOOLS, CortexToolContext
from api_server.db.session import get_admin_sessionmaker
from api_server.llm_providers.factory import build_llm_provider
from api_server.llm_providers.vault import LLMProviderVaultStore
from api_server.routers.assistant import _claude_sdk_available
from api_server.routers.llm_providers import get_provider_vault_store
from api_server.schemas.cortex import (
    CortexConversationResponse,
    CortexTurnItem,
    CortexTurnRequest,
    CortexTurnResponse,
)

router = APIRouter(prefix="/owner/cortex", tags=["cortex"])

# Todas las tools del córtex están habilitadas para el owner (no hay identity por
# tenant que las recorte como en el asistente; el córtex es un singleton).
_CORTEX_ENABLED_TOOLS: tuple[str, ...] = tuple(CORTEX_TOOLS.keys())

# Longitud del recorte del último turno en el listado de hilos.
_PREVIEW_LEN = 160


# ---------------------------------------------------------------------------
# Model injection seam (overridden in tests with a ScriptedAssistantModel)
# ---------------------------------------------------------------------------
async def get_cortex_model(
    _principal: AuthPrincipal = Depends(require_system_owner),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> AssistantModelClient:
    """Resuelve + construye el modelo LLM del córtex (ADR 0074).

    Espejo exacto de ``get_assistant_model`` pero SIN override por tenant: el
    córtex es un singleton del owner, así que su modelo sale sólo del
    platform-default ``cortex.default_model``. ``llm_providers`` es global sin RLS
    (ADR 0028), así que se abre una sesión BYPASSRLS *internamente* sólo para
    construir el provider server-side; nada de la config del provider se devuelve.

    Degradación limpia (ADR 0074): un modelo ``claude_sdk`` sin el Claude Agent SDK
    instalado en ESTE proceso, o un provider no construible, levanta
    :class:`CortexModelUnavailableError`, que el endpoint traduce a un **503 honesto**
    (nunca un 500). Si no hay nada configurado → 503 también.

    Los tests sobreescriben esta dependencia con un ``ScriptedAssistantModel``.
    """
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        resolved = await resolve_cortex_model(admin_session)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="no hay modelo LLM configurado para el córtex (cortex.default_model)",
            )
        claude_ok = _claude_sdk_available()
        provider = None
        # Sólo construimos el provider cuando el SDK (si hace falta) está presente;
        # si es claude_sdk sin SDK, build_cortex_model levanta la excepción → 503.
        if not (resolved.provider_kind == "claude_sdk" and not claude_ok):
            api_model = to_provider_model_name(resolved.provider_kind, resolved.model_id)
            provider = await build_llm_provider(
                admin_session,
                provider_id=resolved.provider_id,
                model=api_model,
                vault=vault,
            )
        try:
            return build_cortex_model(
                resolved,
                provider=provider,
                claude_sdk_available=claude_ok,
            )
        except CortexModelUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@router.post("/turns", response_model=CortexTurnResponse)
async def post_turn(
    payload: CortexTurnRequest,
    principal: AuthPrincipal = Depends(require_system_owner),
    model: AssistantModelClient = Depends(get_cortex_model),
) -> CortexTurnResponse:
    """Un turno del córtex: persiste el mensaje del owner, delibera con recall +
    augment + grafo, persiste la respuesta y la devuelve.

    Todo corre en UNA transacción admin/BYPASSRLS con filtro ``owner_user_id``
    explícito (no hay RLS). El ``tenant_id`` se resuelve una vez (Decisión D1):
    sin ninguna membership → 409 honesto."""
    owner_id = principal.user_id
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session, session.begin():
        try:
            tenant_id = await resolve_cortex_tenant_id(session, owner_id)
        except CortexNoTenantError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc

        # Open or reuse the thread (ownership re-checked on every write/read).
        if payload.conversation_id is None:
            conv = await create_conversation(
                session, owner_user_id=owner_id, tenant_id=tenant_id, model_id=None
            )
            conversation_id = conv.id
        else:
            conversation_id = payload.conversation_id

        # Persist the user turn (append_turn 403-equivalents a foreign owner).
        try:
            await append_turn(
                session,
                conversation_id=conversation_id,
                owner_user_id=owner_id,
                role="user",
                content=payload.message,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="conversation not found",
            ) from exc

        # Recall híbrido del owner (Tarea 4) + augment del system prompt (Tarea 10).
        known_facts = await cortex_recall(
            session,
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            query=payload.message,
            limit=CORTEX_RECALL_LIMIT,
        )
        system_prompt = augment_cortex_prompt(
            _cortex_base_prompt(),
            known_facts=known_facts,
            remember_enabled="cortex_remember" in _CORTEX_ENABLED_TOOLS,
        )

        # The thread's recent history as chat context (excludes the just-written
        # user turn? no — it includes it, which is the latest user message).
        chat_history = await recent_history_for_prompt(
            session, conversation_id=conversation_id, owner_user_id=owner_id
        )
        tool_ctx = CortexToolContext(session=session, owner_user_id=owner_id, tenant_id=tenant_id)

        try:
            result = await run_cortex_turn(
                model,
                system_prompt=system_prompt,
                enabled_tools=_CORTEX_ENABLED_TOOLS,
                tool_ctx=tool_ctx,
                chat_history=chat_history,
            )
        except AuthError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=(
                    "el proveedor LLM rechazó las credenciales (auth); revisa la "
                    f"credencial del proveedor o elige otro modelo. Detalle: {exc}"
                ),
            ) from exc
        except RateLimitError as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"el proveedor LLM está limitando las peticiones: {exc}",
            ) from exc
        except LLMError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"el proveedor LLM del córtex falló: {exc}",
            ) from exc

        # The effort/degraded the resolved model carried (None on a scripted test
        # double). Honest: F1 has no auto-fallback, so degraded is False unless the
        # model object explicitly says otherwise.
        reasoning_effort = getattr(model, "reasoning_effort", None)
        degraded = bool(getattr(model, "degraded", False))

        await append_turn(
            session,
            conversation_id=conversation_id,
            owner_user_id=owner_id,
            role="cortex",
            content=result.content,
            model_id=getattr(model, "model", None),
            tools_called=result.tools_called,
            rounds=result.rounds,
            reasoning_effort=reasoning_effort,
            metadata={"degraded": degraded, "recall_hits": len(known_facts)},
        )

    return CortexTurnResponse(
        conversation_id=conversation_id,
        answer=result.content,
        tools_called=list(result.tools_called),
        rounds=result.rounds,
        reasoning_effort=reasoning_effort,
        degraded=degraded,
    )


@router.get("/turns", response_model=list[CortexTurnItem])
async def get_turns(
    conversation_id: UUID = Query(...),
    limit: int = Query(default=100, ge=1, le=500),
    principal: AuthPrincipal = Depends(require_system_owner),
) -> list[CortexTurnItem]:
    """Los turnos de un hilo del owner, en orden cronológico (filtro owner explícito)."""
    owner_id = principal.user_id
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        try:
            turns = await list_turns(
                session,
                conversation_id=conversation_id,
                owner_user_id=owner_id,
                limit=limit,
            )
        except PermissionError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="conversation not found",
            ) from exc
        return [
            CortexTurnItem(
                id=t.id,
                role=t.role,
                content=t.content,
                created_at=t.created_at,
                model_id=t.model_id,
            )
            for t in turns
        ]


@router.get("/conversations", response_model=list[CortexConversationResponse])
async def get_conversations(
    limit: int = Query(default=50, ge=1, le=200),
    principal: AuthPrincipal = Depends(require_system_owner),
) -> list[CortexConversationResponse]:
    """Los hilos vivos del owner, más reciente primero, con ``last_turn_preview``."""
    owner_id = principal.user_id
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as session:
        convs = await list_conversations(session, owner_user_id=owner_id, limit=limit)
        out: list[CortexConversationResponse] = []
        for conv in convs:
            # Last turn of the thread for the UI selector preview (owner-scoped).
            turns = await list_turns(
                session, conversation_id=conv.id, owner_user_id=owner_id, limit=500
            )
            preview = _preview(turns[-1].content) if turns else None
            out.append(
                CortexConversationResponse(
                    id=conv.id,
                    title=conv.title,
                    model_id=conv.model_id,
                    created_at=conv.created_at,
                    updated_at=conv.updated_at,
                    last_turn_preview=preview,
                )
            )
        return out


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _cortex_base_prompt() -> str:
    """El system prompt base del córtex (copy honesto — F1 no simula afecto).

    El recall y la pista de escritura se añaden encima con
    :func:`augment_cortex_prompt` (mismo blindaje anti-inyección del asistente)."""
    return (
        "Eres el córtex del System Owner: un asistente de deliberación con memoria "
        "persistente entre conversaciones. Razonas en profundidad, recuerdas lo que "
        "el owner te cuenta y lo usas para ayudarle mejor en futuros turnos. Responde "
        "con honestidad y precisión, en el idioma del owner (español o inglés). No "
        "afirmes tener emociones ni consciencia."
    )


def _preview(content: str) -> str:
    """Recorta el contenido de un turno para el listado de hilos."""
    text = " ".join(content.split())
    return text if len(text) <= _PREVIEW_LEN else text[: _PREVIEW_LEN - 1] + "…"


__all__ = ["get_cortex_model", "router"]
