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

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from redis.asyncio import Redis
from shared_llm.exceptions import AuthError, LLMError, RateLimitError

from api_server.assistant.graph import AssistantModelClient
from api_server.assistant.model_config import to_provider_model_name
from api_server.auth.deps import AuthPrincipal, get_redis, require_system_owner
from api_server.celery_client import enqueue_browse_session, enqueue_cortex_distill_affect
from api_server.cortex.affect_policy import modulate_reasoning_effort
from api_server.cortex.browse import BrowseTransitionError
from api_server.cortex.graph import run_cortex_turn
from api_server.cortex.model_config import (
    CortexModelUnavailableError,
    apply_effort_decision,
    build_cortex_model,
    clear_cortex_default_model,
    get_cortex_default_model,
    resolve_cortex_model,
    set_cortex_default_model,
)
from api_server.cortex.self_context import (
    compose_self_context_prompt,
    load_self_context,
    mark_pursuits_surfaced,
)
from api_server.cortex.self_context import (
    self_context_meta as _self_context_meta,
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
from api_server.cortex.tools import CortexToolContext, cortex_enabled_tool_names
from api_server.db.browse_repo import (
    approve_session,
    get_browse_session,
    list_pending,
    reject_session,
)
from api_server.db.llm_providers import get_llm_provider
from api_server.db.models import User
from api_server.db.platform_settings import (
    PlatformSettingForbiddenError,
    get_cortex_browser_enabled,
    get_cortex_web_enabled,
)
from api_server.db.session import get_admin_sessionmaker
from api_server.llm_providers.factory import build_llm_provider
from api_server.llm_providers.vault import LLMProviderVaultStore
from api_server.routers.assistant import (
    _build_model_options,
    _claude_sdk_available,
    _parse_selection,
    _validate_selection_or_422,
)
from api_server.routers.llm_providers import get_provider_vault_store
from api_server.schemas.assistant import (
    AssistantDefaultModelResponse,
    AssistantDefaultModelUpdateRequest,
    AssistantModelOptionsResponse,
)
from api_server.schemas.cortex import (
    CortexConversationResponse,
    CortexTurnItem,
    CortexTurnRequest,
    CortexTurnResponse,
)

router = APIRouter(prefix="/owner/cortex", tags=["cortex"])

# El catálogo del córtex está habilitado para el owner (no hay identity por tenant que
# lo recorte como en el asistente; el córtex es un singleton). Las host tools web
# (ADR 0067) son la ÚNICA excepción: están gated por el setting ``cortex.web_enabled``
# (deny-by-default) y se resuelven por turno con ``cortex_enabled_tool_names``.

# Longitud del recorte del último turno en el listado de hilos.
_PREVIEW_LEN = 160


def _redis_or_none() -> Redis | None:
    """El cliente Redis del api-server, o ``None`` si no es construible.

    El self-context lo usa solo para leer el afecto vivo (fail-open): sin Redis
    cae a la BD y, sin snapshot, al estado neutro — nunca rompe el turno."""
    try:
        return get_redis()
    except Exception:  # fail-open: el afecto es un matiz del turno
        return None


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
    return await build_cortex_default_model(vault)


async def build_cortex_default_model(
    vault: LLMProviderVaultStore | None,
) -> AssistantModelClient:
    """Construye el modelo del córtex desde el platform-default (sin gate).

    Núcleo de :func:`get_cortex_model` SIN la dependencia de gate
    (``require_system_owner``): el córtex es un singleton del owner, así que su
    modelo sale sólo de ``cortex.default_model`` y no depende del principal. Lo
    reutiliza el WS de voz del córtex, que ya gatea por su cuenta
    (``_is_db_system_owner`` DB-authoritative en el accept del socket), donde no
    hay principal de cabecera para resolver ``require_system_owner``.

    Degradación limpia (ADR 0074): un modelo ``claude_sdk`` sin el SDK en ESTE
    proceso, o un provider no construible, levanta ``CortexModelUnavailableError``
    traducida a un 503 honesto; sin nada configurado → 503 también.
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
        # Auditoría del córtex 2026-07-27 (F1.6): el flag NO se pasaba, así que
        # `native_web` era siempre False y las WebSearch/WebFetch nativas del SDK
        # —el egress RECOMENDADO por el ADR 0076 (dec. 3), con anti-SSRF gratis
        # porque el fetch lo hace Anthropic— eran código muerto: con la web
        # encendida el córtex caía siempre en el camino DEGRADADO (dec. 4), el que
        # sale del proceso confiable y necesita su propio anti-SSRF. Se lee del
        # MISMO setting que gobierna las host tools (`cortex.web_enabled`), para
        # que encender la web sea una sola decisión del owner y no dos.
        web_enabled = await get_cortex_web_enabled(admin_session)
        try:
            return build_cortex_model(
                resolved,
                provider=provider,
                claude_sdk_available=claude_ok,
                web_enabled=web_enabled,
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

        # Web del córtex (ADR 0067): gate deny-by-default. Cuando el owner lo habilita
        # desde el panel, las host tools web_search/web_fetch entran en el catálogo y el
        # ctx las permite (salida SIEMPRE por el egress-proxy + anti-SSRF).
        web_enabled = await get_cortex_web_enabled(session)
        # Navegador real (ADR 0080): kill-switch APARTE del de la web. Encendido,
        # el córtex puede PEDIR sesiones de navegación; cada una necesita despues
        # la aprobación explícita del owner (validación humana por sesión).
        browser_enabled = await get_cortex_browser_enabled(session)
        enabled_tools = cortex_enabled_tool_names(
            web_enabled=web_enabled, browser_enabled=browser_enabled
        )

        # Self-context unificado: identidad + afecto vivo + recall + temas
        # pendientes, cargados UNA vez y compuestos en UN solo prompt blindado.
        now = datetime.now(UTC)
        ctx = await load_self_context(
            session,
            _redis_or_none(),
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            query=payload.message,
            now=now,
        )

        # El afecto modula el effort (acotado ±1 paso, auditable; ADR 0075: modula,
        # nunca bloquea). Un doble de test sin provider_kind es no-op limpio.
        decision = modulate_reasoning_effort(
            getattr(model, "reasoning_effort", None),
            getattr(model, "provider_kind", None),
            ctx.affect,
        )
        model = apply_effort_decision(model, decision)

        system_prompt = compose_self_context_prompt(
            _cortex_base_prompt(web_enabled=web_enabled),
            ctx,
            remember_enabled="cortex_remember" in enabled_tools,
        )

        # The thread's recent history as chat context (excludes the just-written
        # user turn? no — it includes it, which is the latest user message).
        chat_history = await recent_history_for_prompt(
            session, conversation_id=conversation_id, owner_user_id=owner_id
        )
        tool_ctx = CortexToolContext(
            session=session,
            owner_user_id=owner_id,
            tenant_id=tenant_id,
            web_enabled=web_enabled,
            browser_enabled=browser_enabled,
        )

        try:
            result = await run_cortex_turn(
                model,
                system_prompt=system_prompt,
                enabled_tools=enabled_tools,
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

        # El effort EFECTIVO del turno (modulado por afecto cuando aplica; para un
        # doble sin metadatos la decisión es no-op y esto queda en None, como antes).
        reasoning_effort = (
            decision.effective
            if decision.effective is not None
            else getattr(model, "reasoning_effort", None)
        )
        degraded = bool(getattr(model, "degraded", False))

        cortex_turn = await append_turn(
            session,
            conversation_id=conversation_id,
            owner_user_id=owner_id,
            role="cortex",
            content=result.content,
            model_id=getattr(model, "model", None),
            tools_called=result.tools_called,
            rounds=result.rounds,
            reasoning_effort=reasoning_effort,
            metadata={
                "degraded": degraded,
                "recall_hits": len(ctx.known_facts),
                "self_context": _self_context_meta(ctx, decision),
            },
        )
        cortex_turn_id = cortex_turn.id

        # Surfacing (ADR 0078): los temas de curiosidad ofrecidos al prompt se
        # marcan EN ESTA transacción — si el LLM hubiera fallado antes, el
        # rollback los deja pendientes para el próximo encuentro.
        await mark_pursuits_surfaced(
            session,
            owner_user_id=owner_id,
            pursuit_ids=[p.pursuit_id for p in ctx.pending_learnings],
            now=now,
        )

        # ADR 0116: el consumo del córtex por fin se contabiliza (best-effort;
        # tenant_id=None — es consumo del owner de plataforma, no de un tenant).
        from api_server.llm_usage import record_llm_usage

        await record_llm_usage(
            session,
            source="cortex",
            model_client=model,
            tenant_id=None,
            user_id=owner_id,
        )

    # Córtex F2 (ADR 0075): tras COMMIT del turno, dispara el distilador afectivo
    # fuera del hot-path (fire-and-forget). El appraisal es ASÍNCRONO: el dial PAD
    # se actualiza ~1-2s después; NUNCA bloquea ni rompe la respuesta (un fallo del
    # broker se traga dentro de enqueue_cortex_distill_affect).
    await enqueue_cortex_distill_affect(cortex_turn_id)

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
# Modelo del córtex (config del owner — sin SQL, espejo del default del asistente)
# ---------------------------------------------------------------------------
# El córtex es un singleton del owner: su modelo sale SOLO del platform-default
# ``cortex.default_model`` (sin override por tenant). Estos endpoints dan al
# System Owner un selector en el panel (igual que el modelo del asistente) en vez
# de tener que tocar ``platform_settings`` a mano. Reutilizan el builder de
# opciones y la validación del asistente (catálogo cerrado, ADR 0021) para NO
# duplicar el catálogo. Todos van gated por ``require_system_owner`` (config del
# owner, no del tenant) y abren la sesión BYPASSRLS manualmente porque
# ``platform_settings``/``llm_providers`` son globales (sin RLS, ADR 0028) y la
# dependencia ``get_admin_session`` exige System Admin (un eje distinto al owner).
@router.get("/model-options", response_model=AssistantModelOptionsResponse)
async def get_model_options(
    _principal: AuthPrincipal = Depends(require_system_owner),
) -> AssistantModelOptionsResponse:
    """Proveedores activos + sus modelos elegibles — la MISMA fuente que usa el
    asistente para sus desplegables (catálogo + modelos sincronizados, sin red ni
    secretos). El selector del córtex la consume tal cual."""
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        return await _build_model_options(admin_session)


@router.get("/model", response_model=AssistantDefaultModelResponse)
async def get_model(
    _principal: AuthPrincipal = Depends(require_system_owner),
) -> AssistantDefaultModelResponse:
    """La selección de modelo del córtex (o sin configurar). ``is_valid`` marca
    una selección obsoleta (proveedor desactivado / modelo retirado) para que el
    owner pueda corregirla — mismo contrato que el default del asistente."""
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        selection = await get_cortex_default_model(admin_session)
        if selection is None:
            return AssistantDefaultModelResponse()
        provider = await get_llm_provider(admin_session, selection.provider_id)
        resolved = await resolve_cortex_model(admin_session)
        return AssistantDefaultModelResponse(
            provider_id=str(selection.provider_id),
            model_id=selection.model_id,
            is_valid=resolved is not None,
            provider_display_name=(provider.display_name if provider else None),
            reasoning_effort=selection.reasoning_effort,
        )


@router.put("/model", response_model=AssistantDefaultModelResponse)
async def put_model(
    payload: AssistantDefaultModelUpdateRequest,
    principal: AuthPrincipal = Depends(require_system_owner),
) -> AssistantDefaultModelResponse:
    """Fija o limpia el modelo del córtex (System Owner).

    Cuerpo con ``provider_id``+``model_id`` (y opcional ``reasoning_effort``) para
    fijar, o ambos ``None`` para limpiar. Valida la selección como el asistente
    (proveedor activo + modelo elegible del catálogo cerrado ADR 0021, y un
    ``reasoning_effort`` válido para el kind, ADR 0070); rechaza con 422 una
    selección fuera de catálogo. Todo en UNA transacción BYPASSRLS.

    ``set_cortex_default_model`` (→ ``set_platform_setting``) re-verifica que el
    actor es System Admin: el owner del despliegue lo es (es el primer usuario,
    ADR 0074); un owner que NO fuese admin recibiría un 403 honesto en vez de un
    escritura silenciosa."""
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session, admin_session.begin():
        actor = await admin_session.get(User, principal.user_id)
        if actor is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="actor user not found"
            )
        try:
            if payload.is_clear:
                await clear_cortex_default_model(admin_session, actor=actor)
                return AssistantDefaultModelResponse()
            selection = _parse_selection(
                payload.provider_id, payload.model_id, payload.reasoning_effort
            )
            await _validate_selection_or_422(admin_session, selection)
            await set_cortex_default_model(admin_session, selection, actor=actor)
        except PlatformSettingForbiddenError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        provider = await get_llm_provider(admin_session, selection.provider_id)
        return AssistantDefaultModelResponse(
            provider_id=str(selection.provider_id),
            model_id=selection.model_id,
            is_valid=True,
            provider_display_name=(provider.display_name if provider else None),
            reasoning_effort=selection.reasoning_effort,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Sesiones de navegador: el inbox de aprobación del owner (ADR 0080)
# ---------------------------------------------------------------------------
class BrowseSessionItem(BaseModel):
    """Una sesión que el córtex quiere navegar. El owner ve el guion EXACTO —
    a qué URLs va, qué clica y qué teclea — porque eso es lo que autoriza."""

    id: str
    status: str
    goal: str
    steps: list[dict[str, Any]]
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: datetime | None = None


class BrowseDecisionRequest(BaseModel):
    reason: str = Field(default="", max_length=500)


def _browse_item(row: Any) -> BrowseSessionItem:
    return BrowseSessionItem(
        id=str(row.id),
        status=row.status,
        goal=row.goal,
        steps=list(row.steps or []),
        result=row.result,
        error=row.error,
        created_at=row.created_at,
    )


@router.get("/browse-sessions", response_model=list[BrowseSessionItem])
async def list_browse_sessions(
    principal: AuthPrincipal = Depends(require_system_owner),
) -> list[BrowseSessionItem]:
    """Lo que el córtex ha pedido navegar y espera decisión humana."""
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        rows = await list_pending(admin_session, owner_user_id=principal.user_id)
        return [_browse_item(row) for row in rows]


@router.post("/browse-sessions/{session_id}/approve", response_model=BrowseSessionItem)
async def approve_browse_session(
    session_id: UUID,
    principal: AuthPrincipal = Depends(require_system_owner),
) -> BrowseSessionItem:
    """El owner aprueba ESTA sesión: solo ahora se lanza el navegador.

    La aprobación es por sesión (nunca un permiso permanente) y se registra con
    quién y cuándo. Si el kill-switch de plataforma está apagado no hay nada que
    aprobar — y el worker lo vuelve a comprobar antes de abrir Chromium."""
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        if not await get_cortex_browser_enabled(admin_session):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="el navegador del córtex está deshabilitado (cortex.browser_enabled)",
            )
        owner_id = principal.user_id
        row = await get_browse_session(admin_session, session_id, owner_user_id=owner_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        try:
            await approve_session(admin_session, row, decided_by=owner_id)
        except BrowseTransitionError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        await admin_session.commit()
        item = _browse_item(row)

    if not await enqueue_browse_session(session_id):
        # La fila queda en `approved`: re-aprobar la relanza. Se lo decimos al
        # owner en vez de dejarle creer que su navegación está en marcha.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="sesión aprobada pero no se pudo encolar (broker caído): reintenta",
        )
    return item


@router.post("/browse-sessions/{session_id}/reject", response_model=BrowseSessionItem)
async def reject_browse_session(
    session_id: UUID,
    payload: BrowseDecisionRequest,
    principal: AuthPrincipal = Depends(require_system_owner),
) -> BrowseSessionItem:
    """El owner dice que no. Terminal: esa sesión no se navega nunca."""
    sessionmaker = get_admin_sessionmaker()
    async with sessionmaker() as admin_session:
        owner_id = principal.user_id
        row = await get_browse_session(admin_session, session_id, owner_user_id=owner_id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")
        try:
            await reject_session(admin_session, row, decided_by=owner_id, reason=payload.reason)
        except BrowseTransitionError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        await admin_session.commit()
        return _browse_item(row)


def _cortex_base_prompt(*, web_enabled: bool = False) -> str:
    """El system prompt base del córtex (copy honesto — F1 no simula afecto).

    El recall y la pista de escritura se añaden encima con
    :func:`augment_cortex_prompt` (mismo blindaje anti-inyección del asistente).

    ``web_enabled``: la affordance de la web se ANUNCIA explícitamente — el
    modelo no puede usar lo que no sabe que tiene (sus priors buscan las tools
    nativas «WebSearch/WebFetch», que aquí no existen: las del córtex son las
    host tools ``web_search``/``web_fetch`` vía egress-proxy, ADR 0067)."""
    base = (
        "Eres el córtex del System Owner: un asistente de deliberación con memoria "
        "persistente entre conversaciones. Razonas en profundidad, recuerdas lo que "
        "el owner te cuenta y lo usas para ayudarle mejor en futuros turnos. Responde "
        "con honestidad y precisión, en el idioma del owner (español o inglés). No "
        "afirmes tener emociones ni consciencia."
    )
    if web_enabled:
        base += (
            " SÍ tienes acceso a Internet mediante tus tools web_search (buscar) y "
            "web_fetch (leer una URL concreta), con salida por un proxy seguro. NUNCA "
            "digas que no tienes acceso a Internet ni permiso para buscar: LO TIENES. "
            "Siempre que te pregunten por información ACTUAL o externa (el tiempo, "
            "noticias, precios, datos recientes, cualquier cosa que no sepas con "
            "certeza), LLAMA a web_search ANTES de responder y basa tu respuesta en "
            "los resultados, citando la fuente (no confundas estas tools con "
            "«WebSearch/WebFetch», que no existen aquí). Solo di que no lo sabes si "
            "la búsqueda no devuelve nada útil."
        )
    return base


def _preview(content: str) -> str:
    """Recorta el contenido de un turno para el listado de hilos."""
    text = " ".join(content.split())
    return text if len(text) <= _PREVIEW_LEN else text[: _PREVIEW_LEN - 1] + "…"


__all__ = ["build_cortex_default_model", "get_cortex_model", "router"]
