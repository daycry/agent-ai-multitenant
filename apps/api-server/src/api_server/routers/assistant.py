"""`/assistant` endpoints — conversational personal assistant (Plan 10 task_10_14).

REST shape:

  POST /assistant/chat        ask the assistant a question
  GET  /assistant/identity    read the tenant-level assistant identity
  PUT  /assistant/identity    update the tenant-level assistant identity

ACCESS (binding constraints — see docs/roadmap/10-asistente-personal.md):

  * Tenant-Admin-only. ``require_tenant_admin`` already 403s a
    ``tenant_user`` / member.
  * Toggle-gated. ``Organization.personal_assistant_enabled`` DEFAULTS to
    false; when off, even a Tenant Admin is denied (403 "disabled"). The
    ``require_assistant_access`` dependency enforces both.

Cross-project read tools run through the request's RLS-bound session
(``get_tenant_session``), so a tool can never see another tenant's data
and never more than the admin's RLS scope permits.

The LLM is injected via ``get_assistant_model`` so tests override it with
a ``ScriptedAssistantModel`` — no real provider is contacted (the chat-test
pattern). The default factory raises a clear 503 until a provider is wired,
rather than fabricating answers.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from shared_llm.exceptions import AuthError, LLMError, RateLimitError
from shared_llm.reasoning import reasoning_call_kwargs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.assistant.config import get_assistant_identity, set_assistant_identity
from api_server.assistant.graph import AssistantModelClient, run_assistant_turn
from api_server.assistant.llm import LLMAssistantModel
from api_server.assistant.memory import augment_system_prompt, recall_user_memories
from api_server.assistant.model_config import (
    AssistantModelSelection,
    ResolvedAssistantModel,
    clear_platform_default_model,
    clear_tenant_model_override,
    get_platform_default_model,
    get_tenant_model_override,
    is_valid_selection,
    list_available_models_for_provider,
    resolve_assistant_model,
    set_platform_default_model,
    set_tenant_model_override,
    to_provider_model_name,
)
from api_server.assistant.tools import AssistantToolContext
from api_server.auth.deps import (
    AuthPrincipal,
    get_admin_session,
    get_tenant_session,
    require_system_admin,
    require_tenant_admin,
)
from api_server.db.assistant_chat import AssistantConversation, AssistantTurn
from api_server.db.llm_providers import (
    REASONING_OPTIONS_BY_KIND,
    get_llm_provider,
    list_llm_providers,
)
from api_server.db.models import Organization, User
from api_server.db.session import get_admin_sessionmaker
from api_server.llm_providers.factory import build_llm_provider
from api_server.llm_providers.vault import LLMProviderVaultStore
from api_server.llm_usage import record_llm_usage
from api_server.routers._helpers import require_tenant_id
from api_server.routers.llm_providers import get_provider_vault_store
from api_server.schemas.assistant import (
    AssistantChatRequest,
    AssistantChatResponse,
    AssistantConversationItem,
    AssistantDefaultModelResponse,
    AssistantDefaultModelUpdateRequest,
    AssistantIdentityResponse,
    AssistantIdentityUpdateRequest,
    AssistantModelOption,
    AssistantModelOptionsResponse,
    AssistantModelResponse,
    AssistantModelUpdateRequest,
    AssistantTurnItem,
    to_identity_response,
)

router = APIRouter(prefix="/assistant", tags=["assistant"])


# ---------------------------------------------------------------------------
# Access gate: Tenant Admin AND personal_assistant_enabled
# ---------------------------------------------------------------------------
async def require_assistant_access(
    principal: AuthPrincipal = Depends(require_tenant_admin),
    session: AsyncSession = Depends(get_tenant_session),
) -> AuthPrincipal:
    """Gate every assistant endpoint.

    ``require_tenant_admin`` already 403s a non-admin member. On top of
    that we require the per-tenant toggle to be ON: a Tenant Admin of a
    tenant with ``personal_assistant_enabled = false`` (the default) gets
    a 403 telling them the feature is disabled.

    A System Admin acting WITHOUT a tenant context (no ``tid``) has no
    tenant whose toggle to check, so we 400 — they must pick a tenant
    first (the same rule every tenant-scoped write follows).
    """
    tenant_id = require_tenant_id(principal)
    enabled = await session.scalar(
        select(Organization.personal_assistant_enabled).where(Organization.id == tenant_id)
    )
    if not enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="personal assistant is disabled for this tenant",
        )
    return principal


def _claude_sdk_available() -> bool:
    """True si el Claude Agent SDK está instalado (build WITH_CLAUDE, ADR 0064).
    El asistente corre EN el api-server; sin el SDK, un modelo claude_sdk daría un
    500 (ImportError) — lo convertimos en 503 limpio aguas arriba."""
    import importlib.util

    return importlib.util.find_spec("claude_agent_sdk") is not None


# ---------------------------------------------------------------------------
# Model injection seam (overridden in tests with a ScriptedAssistantModel)
# ---------------------------------------------------------------------------
async def get_assistant_model(
    principal: AuthPrincipal = Depends(require_assistant_access),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> AssistantModelClient:
    """Resolve + build the LLM-backed assistant model for the tenant (ADR 0053).

    Resolves the effective ``(provider_id, model_id)`` with inheritance
    (tenant override → platform default), then builds the concrete provider.
    ``llm_providers`` is platform-global with NO RLS (ADR 0028) and the caller
    is a Tenant Admin, so we open a BYPASSRLS admin session *internally* — it
    is used only to construct the provider server-side; nothing about the
    provider config is ever returned to the tenant. A 503 is raised (rather
    than fabricating an answer) when nothing is configured or the provider's
    optional SDK / credential is unavailable.

    Tests override this dependency with a ``ScriptedAssistantModel`` (the
    established chat-test pattern), so the integration suite never contacts a
    real provider.
    """
    tenant_id = require_tenant_id(principal)
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        resolved = await resolve_assistant_model(admin_session, tenant_id)
        if resolved is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="no LLM model configured for the personal assistant",
            )
        # El asistente corre EN el api-server: si usa claude_sdk pero la imagen no
        # trae el Claude Agent SDK (build sin WITH_CLAUDE, ADR 0064), fallamos
        # limpio con 503 en vez de un 500 (ImportError en `_build_options`). Los
        # agentes de equipo NO se ven afectados: corren en agent-runtime (WITH_CLAUDE).
        if resolved.provider_kind == "claude_sdk" and not _claude_sdk_available():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "el modelo del asistente usa Claude (claude_sdk) pero este "
                    "api-server no incluye el Claude Agent SDK (build con "
                    "WITH_CLAUDE=1). Elige otro proveedor para el asistente o "
                    "redespliega el api-server con el SDK."
                ),
            )
        # The catalog id can be LiteLLM-keyed (e.g. ``ollama/llama3.1``); the
        # provider API wants the bare model name.
        api_model = to_provider_model_name(resolved.provider_kind, resolved.model_id)
        provider = await build_llm_provider(
            admin_session,
            provider_id=resolved.provider_id,
            model=api_model,
            vault=vault,
        )
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="the configured LLM provider is unavailable",
        )
    # ADR 0070: traduce el reasoning_effort resuelto al kwarg nativo del proveedor.
    extra = reasoning_call_kwargs(resolved.provider_kind, resolved.reasoning_effort)
    return LLMAssistantModel(provider=provider, model=api_model, extra_call_kwargs=extra)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# A1 — hilos persistentes del asistente (espejo tenant-scoped del córtex).
# ---------------------------------------------------------------------------
# Turnos recientes que entran al prompt (pares user/assistant). El resto del
# hilo queda en BD para la UI; el prompt no crece sin límite.
_HISTORY_TURNS_LIMIT = 20
_TITLE_MAX = 60


async def _resolve_conversation(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
    conversation_id: UUID | None,
    first_message: str,
) -> AssistantConversation:
    """El hilo del usuario (SOLO suyo — 404 si es de otro) o uno nuevo."""
    if conversation_id is not None:
        conv = (
            await session.execute(
                select(AssistantConversation).where(
                    AssistantConversation.id == conversation_id,
                    AssistantConversation.tenant_id == tenant_id,
                    AssistantConversation.user_id == user_id,
                    AssistantConversation.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if conv is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found"
            )
        return conv
    title = first_message.strip()[:_TITLE_MAX] or None
    conv = AssistantConversation(tenant_id=tenant_id, user_id=user_id, title=title)
    session.add(conv)
    await session.flush()
    return conv


async def _conversation_history(
    session: AsyncSession, *, conversation_id: UUID
) -> list[dict[str, str]]:
    """Los últimos turnos del hilo como {role, content} (cronológicos)."""
    rows = list(
        (
            await session.execute(
                select(AssistantTurn.role, AssistantTurn.content)
                .where(
                    AssistantTurn.conversation_id == conversation_id,
                    AssistantTurn.deleted_at.is_(None),
                )
                .order_by(AssistantTurn.created_at.desc())
                .limit(_HISTORY_TURNS_LIMIT)
            )
        ).all()
    )
    return [{"role": str(r[0]), "content": str(r[1])} for r in reversed(rows)]


async def _persist_turns(
    session: AsyncSession,
    *,
    conversation: AssistantConversation,
    user_id: UUID,
    user_message: str,
    answer: str,
    tools_called: list[str],
    rounds: int,
) -> None:
    """Persiste el par user/assistant y refresca el updated_at del hilo."""
    session.add(
        AssistantTurn(
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
            user_id=user_id,
            role="user",
            content=user_message,
        )
    )
    session.add(
        AssistantTurn(
            tenant_id=conversation.tenant_id,
            conversation_id=conversation.id,
            user_id=user_id,
            role="assistant",
            content=answer,
            tools_called=tools_called,
            rounds=rounds,
        )
    )
    conversation.updated_at = datetime.now(UTC)
    await session.flush()


@router.post("/chat/stream")
async def assistant_chat_stream(
    payload: AssistantChatRequest,
    principal: AuthPrincipal = Depends(require_assistant_access),
    session: AsyncSession = Depends(get_tenant_session),
    model: AssistantModelClient = Depends(get_assistant_model),
) -> StreamingResponse:
    """El mismo turno que /chat pero con PROGRESO en vivo (SSE) — A2 fase 1.

    Antes el usuario miraba «Pensando…» hasta la respuesta completa (hasta 6
    rondas de tools, cada una un complete() entero). Frames:
      * ``progress`` {rounds, tools_called} por paso del grafo;
      * ``answer``  {answer, tools_called, rounds, conversation_id} al final;
      * ``error``   {detail} si el proveedor falla (el stream cierra limpio).
    El streaming token-a-token queda para ADR 0073 F2 (decide_stream)."""
    import asyncio as _asyncio
    import json as _json

    tenant_id = require_tenant_id(principal)
    identity = await get_assistant_identity(session, tenant_id)
    tool_ctx = AssistantToolContext(session=session, tenant_id=tenant_id, user_id=principal.user_id)
    enabled_tools = identity.effective_tools()
    known_facts = await recall_user_memories(
        session, tenant_id=tenant_id, user_id=principal.user_id
    )
    system_prompt = augment_system_prompt(
        identity.system_prompt(),
        known_facts=known_facts,
        remember_enabled="remember_about_me" in enabled_tools,
    )
    conversation = await _resolve_conversation(
        session,
        tenant_id=tenant_id,
        user_id=principal.user_id,
        conversation_id=payload.conversation_id,
        first_message=payload.message,
    )
    history = await _conversation_history(session, conversation_id=conversation.id)

    queue: _asyncio.Queue[tuple[str, dict[str, Any]]] = _asyncio.Queue()

    async def _on_progress(frame: dict[str, Any]) -> None:
        await queue.put(("progress", frame))

    async def _run() -> None:
        try:
            result = await run_assistant_turn(
                model,
                system_prompt=system_prompt,
                enabled_tools=enabled_tools,
                tool_ctx=tool_ctx,
                chat_history=[*history, {"role": "user", "content": payload.message}],
                on_progress=_on_progress,
            )
            await _persist_turns(
                session,
                conversation=conversation,
                user_id=principal.user_id,
                user_message=payload.message,
                answer=result.content,
                tools_called=list(result.tools_called),
                rounds=result.rounds,
            )
            # ADR 0116: contabilidad del consumo (best-effort).
            await record_llm_usage(
                session,
                source="assistant",
                model_client=model,
                tenant_id=tenant_id,
                user_id=principal.user_id,
            )
            await session.commit()
            await queue.put(
                (
                    "answer",
                    {
                        "answer": result.content,
                        "tools_called": list(result.tools_called),
                        "rounds": result.rounds,
                        "conversation_id": str(conversation.id),
                    },
                )
            )
        except (AuthError, RateLimitError, LLMError) as exc:
            await queue.put(("error", {"detail": f"el proveedor LLM falló: {exc}"}))
        except Exception as exc:  # el stream cierra limpio, nunca cuelga
            await queue.put(("error", {"detail": str(exc)}))
        finally:
            await queue.put(("end", {}))

    async def _events() -> Any:
        task = _asyncio.create_task(_run())
        try:
            while True:
                kind, data = await queue.get()
                if kind == "end":
                    break
                yield f"event: {kind}\ndata: {_json.dumps(data)}\n\n"
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(
        _events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/conversations", response_model=list[AssistantConversationItem])
async def list_assistant_conversations(
    principal: AuthPrincipal = Depends(require_assistant_access),
    session: AsyncSession = Depends(get_tenant_session),
    limit: int = Query(default=30, ge=1, le=100),
) -> list[AssistantConversationItem]:
    """Los hilos del usuario, más reciente primero (A1)."""
    tenant_id = require_tenant_id(principal)
    rows = list(
        (
            await session.execute(
                select(AssistantConversation)
                .where(
                    AssistantConversation.tenant_id == tenant_id,
                    AssistantConversation.user_id == principal.user_id,
                    AssistantConversation.deleted_at.is_(None),
                )
                .order_by(AssistantConversation.updated_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        AssistantConversationItem(id=r.id, title=r.title, updated_at=r.updated_at) for r in rows
    ]


@router.get("/conversations/{conversation_id}/turns", response_model=list[AssistantTurnItem])
async def list_assistant_turns(
    conversation_id: UUID,
    principal: AuthPrincipal = Depends(require_assistant_access),
    session: AsyncSession = Depends(get_tenant_session),
    limit: int = Query(default=200, ge=1, le=500),
) -> list[AssistantTurnItem]:
    """Los turnos de un hilo del usuario (cronológicos; 404 si no es suyo)."""
    tenant_id = require_tenant_id(principal)
    conv = await _resolve_conversation(
        session,
        tenant_id=tenant_id,
        user_id=principal.user_id,
        conversation_id=conversation_id,
        first_message="",
    )
    rows = list(
        (
            await session.execute(
                select(AssistantTurn)
                .where(
                    AssistantTurn.conversation_id == conv.id,
                    AssistantTurn.deleted_at.is_(None),
                )
                .order_by(AssistantTurn.created_at.asc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return [
        AssistantTurnItem(
            role=r.role,
            content=r.content,
            tools_called=[str(t) for t in (r.tools_called or [])],
            rounds=r.rounds,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post("/chat", response_model=AssistantChatResponse)
async def assistant_chat(
    payload: AssistantChatRequest,
    principal: AuthPrincipal = Depends(require_assistant_access),
    session: AsyncSession = Depends(get_tenant_session),
    model: AssistantModelClient = Depends(get_assistant_model),
) -> AssistantChatResponse:
    tenant_id = require_tenant_id(principal)
    identity = await get_assistant_identity(session, tenant_id)
    tool_ctx = AssistantToolContext(
        session=session,
        tenant_id=tenant_id,
        user_id=principal.user_id,
    )
    enabled_tools = identity.effective_tools()
    # Surface what we already know about this user and fold it into the system
    # prompt so the assistant "knows" them without a tool call (ADR 0054).
    known_facts = await recall_user_memories(
        session, tenant_id=tenant_id, user_id=principal.user_id
    )
    system_prompt = augment_system_prompt(
        identity.system_prompt(),
        known_facts=known_facts,
        remember_enabled="remember_about_me" in enabled_tools,
    )
    # A1 (investigación 2026-07-11): hilo persistente — resolver/crear la
    # conversación del usuario y alimentar el prompt con su historial reciente
    # (human_10_04: «mantiene contexto entre mensajes»; antes cada POST enviaba
    # SOLO el mensaje actual y una recarga lo perdía todo).
    conversation = await _resolve_conversation(
        session,
        tenant_id=tenant_id,
        user_id=principal.user_id,
        conversation_id=payload.conversation_id,
        first_message=payload.message,
    )
    history = await _conversation_history(session, conversation_id=conversation.id)
    try:
        result = await run_assistant_turn(
            model,
            system_prompt=system_prompt,
            enabled_tools=enabled_tools,
            tool_ctx=tool_ctx,
            chat_history=[*history, {"role": "user", "content": payload.message}],
        )
    except AuthError as exc:
        # Bad/expired provider credential — most often a misconfigured provider
        # (e.g. an Ollama Cloud endpoint with no API key). A handled 502 flows
        # back through the CORS middleware (an UNHANDLED 500 would not, and the
        # browser would see an opaque "Failed to fetch" instead of this hint).
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
            detail=f"el proveedor LLM del asistente falló: {exc}",
        ) from exc
    await _persist_turns(
        session,
        conversation=conversation,
        user_id=principal.user_id,
        user_message=payload.message,
        answer=result.content,
        tools_called=list(result.tools_called),
        rounds=result.rounds,
    )
    # ADR 0116: el consumo del asistente por fin se contabiliza (best-effort).
    await record_llm_usage(
        session,
        source="assistant",
        model_client=model,
        tenant_id=tenant_id,
        user_id=principal.user_id,
    )
    return AssistantChatResponse(
        answer=result.content,
        tools_called=list(result.tools_called),
        rounds=result.rounds,
        conversation_id=conversation.id,
    )


@router.get("/identity", response_model=AssistantIdentityResponse)
async def get_identity(
    principal: AuthPrincipal = Depends(require_assistant_access),
    session: AsyncSession = Depends(get_tenant_session),
) -> AssistantIdentityResponse:
    tenant_id = require_tenant_id(principal)
    identity = await get_assistant_identity(session, tenant_id)
    return to_identity_response(identity)


@router.put("/identity", response_model=AssistantIdentityResponse)
async def put_identity(
    payload: AssistantIdentityUpdateRequest,
    principal: AuthPrincipal = Depends(require_assistant_access),
    session: AsyncSession = Depends(get_tenant_session),
) -> AssistantIdentityResponse:
    tenant_id = require_tenant_id(principal)
    stored = await set_assistant_identity(
        session,
        tenant_id,
        payload.to_identity(),
        updated_by_user_id=principal.user_id,
    )
    return to_identity_response(stored)


# ===========================================================================
# Model selection (ADR 0053)
# ===========================================================================
def _parse_selection(
    provider_id: str | None, model_id: str | None, reasoning_effort: str | None = None
) -> AssistantModelSelection:
    """Coerce a request's provider_id/model_id into a selection (422 on a
    malformed UUID). Callers pass non-None values (the schema enforces
    both-or-neither and the clear path is handled before this)."""
    try:
        parsed = UUID(str(provider_id))
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="provider_id must be a valid UUID",
        ) from exc
    # "off"/vacío no se persiste (= sin razonamiento).
    reasoning = reasoning_effort if reasoning_effort and reasoning_effort != "off" else None
    return AssistantModelSelection(
        provider_id=parsed, model_id=str(model_id), reasoning_effort=reasoning
    )


async def _validate_selection_or_422(
    admin_session: AsyncSession, selection: AssistantModelSelection
) -> None:
    """422 unless the selection names an ACTIVE provider and a SELECTABLE
    model (price catalogue plus the provider's synced models), y el
    ``reasoning_effort`` (si lo hay) es válido para el kind del proveedor
    (ADR 0070)."""
    if not await is_valid_selection(admin_session, selection):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "invalid selection: provider_id must be an active provider and "
                "model_id must be one it offers (catalogue or synced — sync the "
                "provider's models first)"
            ),
        )
    if selection.reasoning_effort:
        provider = await get_llm_provider(admin_session, selection.provider_id)
        allowed = REASONING_OPTIONS_BY_KIND.get(provider.kind, ()) if provider else ()
        if selection.reasoning_effort not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    f"reasoning_effort {selection.reasoning_effort!r} is not valid for "
                    f"this provider (ADR 0070); allowed: {list(allowed)}"
                ),
            )


def _model_response(
    resolved: ResolvedAssistantModel | None, *, has_tenant_override: bool
) -> AssistantModelResponse:
    if resolved is None:
        return AssistantModelResponse(has_tenant_override=has_tenant_override)
    return AssistantModelResponse(
        provider_id=str(resolved.provider_id),
        model_id=resolved.model_id,
        source=resolved.source,
        provider_kind=resolved.provider_kind,
        provider_display_name=resolved.provider_display_name,
        has_tenant_override=has_tenant_override,
        reasoning_effort=resolved.reasoning_effort,
    )


@router.get("/model", response_model=AssistantModelResponse)
async def get_model(
    principal: AuthPrincipal = Depends(require_assistant_access),
) -> AssistantModelResponse:
    """The effective model for the tenant's assistant (resolved with
    inheritance) plus whether a tenant override is set."""
    tenant_id = require_tenant_id(principal)
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        resolved = await resolve_assistant_model(admin_session, tenant_id)
        override = await get_tenant_model_override(admin_session, tenant_id)
    return _model_response(resolved, has_tenant_override=override is not None)


@router.put("/model", response_model=AssistantModelResponse)
async def put_model(
    payload: AssistantModelUpdateRequest,
    principal: AuthPrincipal = Depends(require_assistant_access),
) -> AssistantModelResponse:
    """Set or clear the tenant model override.

    The whole operation runs on ONE BYPASSRLS admin transaction so the
    response reflects the write (a tenant session's uncommitted change would
    be invisible to the separate admin session the resolver needs for
    ``llm_providers``). Authorization is already enforced by
    ``require_assistant_access`` (Tenant Admin + toggle ON); the tenant_id is
    taken from the principal, never the body, and the write filters tenant_id
    explicitly.
    """
    tenant_id = require_tenant_id(principal)
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session, admin_session.begin():
        if payload.is_clear:
            await clear_tenant_model_override(admin_session, tenant_id)
        else:
            selection = _parse_selection(
                payload.provider_id, payload.model_id, payload.reasoning_effort
            )
            await _validate_selection_or_422(admin_session, selection)
            await set_tenant_model_override(
                admin_session, tenant_id, selection, updated_by_user_id=principal.user_id
            )
        resolved = await resolve_assistant_model(admin_session, tenant_id)
        override = await get_tenant_model_override(admin_session, tenant_id)
    return _model_response(resolved, has_tenant_override=override is not None)


async def _build_model_options(admin_session: AsyncSession) -> AssistantModelOptionsResponse:
    """Active providers + their selectable models (price catalogue plus the
    provider's synced models — both from the DB, no network). The shared
    dropdown source for the tenant and platform-default surfaces. No secrets
    are exposed (only kind + display_name + model ids)."""
    providers = await list_llm_providers(admin_session, active_only=True)
    options = [
        AssistantModelOption(
            provider_id=str(provider.id),
            kind=provider.kind,
            slug=provider.slug,
            display_name=provider.display_name,
            models=await list_available_models_for_provider(admin_session, provider),
        )
        for provider in providers
    ]
    # ADR 0070: opciones de razonamiento por kind, solo para los kinds activos.
    active_kinds = {p.kind for p in providers}
    reasoning_by_kind = {
        kind: list(REASONING_OPTIONS_BY_KIND[kind])
        for kind in active_kinds
        if kind in REASONING_OPTIONS_BY_KIND
    }
    return AssistantModelOptionsResponse(providers=options, reasoning_by_kind=reasoning_by_kind)


@router.get(
    "/model/options",
    response_model=AssistantModelOptionsResponse,
    dependencies=[Depends(require_assistant_access)],
)
async def get_model_options() -> AssistantModelOptionsResponse:
    """Active providers and the models selectable on each — the dropdown
    source for the tenant UI. Gated to Tenant Admins (toggle ON); no secrets
    are exposed."""
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        return await _build_model_options(admin_session)


# ---------------------------------------------------------------------------
# Platform default (System-Admin surface)
# ---------------------------------------------------------------------------
@router.get("/default-model/options", response_model=AssistantModelOptionsResponse)
async def get_default_model_options(
    admin_session: AsyncSession = Depends(get_admin_session),
) -> AssistantModelOptionsResponse:
    """Provider/model dropdown source for the System-Admin platform-default
    control. Runs on the admin session (System Admin only) so it needs no
    tenant context — unlike the tenant ``/model/options``."""
    return await _build_model_options(admin_session)


@router.get("/default-model", response_model=AssistantDefaultModelResponse)
async def get_default_model(
    admin_session: AsyncSession = Depends(get_admin_session),
) -> AssistantDefaultModelResponse:
    """The platform default model (or unset). ``is_valid`` flags a stale
    default (disabled provider / retired model) so the operator can fix it."""
    default = await get_platform_default_model(admin_session)
    if default is None:
        return AssistantDefaultModelResponse()
    provider = await get_llm_provider(admin_session, default.provider_id)
    return AssistantDefaultModelResponse(
        provider_id=str(default.provider_id),
        model_id=default.model_id,
        is_valid=await is_valid_selection(admin_session, default),
        provider_display_name=(provider.display_name if provider else None),
        reasoning_effort=default.reasoning_effort,
    )


@router.put("/default-model", response_model=AssistantDefaultModelResponse)
async def put_default_model(
    payload: AssistantDefaultModelUpdateRequest,
    principal: AuthPrincipal = Depends(require_system_admin),
    admin_session: AsyncSession = Depends(get_admin_session),
) -> AssistantDefaultModelResponse:
    """Set or clear the platform default model (System Admin only)."""
    actor = await admin_session.get(User, principal.user_id)
    if actor is None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="actor user not found")
    if payload.is_clear:
        await clear_platform_default_model(admin_session, actor=actor)
        return AssistantDefaultModelResponse()
    selection = _parse_selection(payload.provider_id, payload.model_id, payload.reasoning_effort)
    await _validate_selection_or_422(admin_session, selection)
    await set_platform_default_model(admin_session, selection, actor=actor)
    provider = await get_llm_provider(admin_session, selection.provider_id)
    return AssistantDefaultModelResponse(
        provider_id=str(selection.provider_id),
        model_id=selection.model_id,
        is_valid=True,
        provider_display_name=(provider.display_name if provider else None),
        reasoning_effort=selection.reasoning_effort,
    )


__all__ = ["get_assistant_model", "require_assistant_access", "router"]
