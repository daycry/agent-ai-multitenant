"""Córtex F5 — adaptador de turno de voz del córtex (ADR 0073 voz + 0075 afecto).

Tres costuras que el WS ``/ws/owner/cortex/voice`` (``routers/cortex_voice.py``)
compone, manteniendo el transporte IDÉNTICO al asistente y cambiando sólo el
cerebro y el frame afectivo:

  * :func:`run_cortex_voice_turn` — corre UN turno del córtex para ``user_text``
    REUTILIZANDO el MISMO pipeline que ``POST /owner/cortex/turns`` (resolver
    tenant D1 → abrir/reusar hilo → persistir turno ``user`` → identidad + recall
    + augment → ``run_cortex_turn`` → persistir turno ``cortex`` → disparar el
    distilador afectivo fire-and-forget). Todo en UNA transacción admin/BYPASSRLS
    con filtro ``owner_user_id`` explícito (las tablas del córtex son tenant-less;
    no hay RLS — la prueba de mérito es el test cross-owner del WS).

  * :func:`load_current_affect` — lee el afecto VIGENTE del owner: caché Redis
    ``cortex:affect:{owner}`` con decay lazy (rápido) → BD ``load_affect_state``
    (fuente de verdad) → baseline neutro. **Fail-open**: cualquier fallo (Redis
    caído, BD sin snapshot, error) cae al baseline neutro y NUNCA lanza, para que
    la voz hable aunque el dial afectivo no esté disponible.

  * :func:`affect_frame` — builder PURO ``AffectState -> {type:'affect', valence,
    arousal, dominance, mood_label, drives}`` que el avatar del front mapea a
    color/expresión/sway (ADR 0075).

> Honestidad (ADR 0075 §6): el afecto es un modelo computacional determinista,
> NO sentimientos reales; el front rotula el frame como tal en la UI (copy honesto).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api_server.assistant.graph import AssistantModelClient, AssistantTurnResult
from api_server.cortex.affect_cache import read_affect_state
from api_server.cortex.affect_policy import modulate_reasoning_effort
from api_server.cortex.affect_store import load_affect_state
from api_server.cortex.affective import AffectState, Language, neutral_affect_state
from api_server.cortex.graph import run_cortex_turn
from api_server.cortex.model_config import apply_effort_decision
from api_server.cortex.self_context import (
    compose_self_context_prompt,
    load_self_context,
    mark_pursuits_surfaced,
    self_context_meta,
)
from api_server.cortex.threads import (
    CortexNoTenantError,
    append_turn,
    create_conversation,
    recent_history_for_prompt,
    resolve_cortex_tenant_id,
)
from api_server.cortex.tools import CortexToolContext, cortex_enabled_tool_names
from api_server.db.platform_settings import get_cortex_web_enabled

_log = structlog.get_logger("api_server.cortex.voice_turn")


def _cortex_voice_base_prompt(*, web_enabled: bool = False, language_instruction: str = "") -> str:
    """System prompt base del córtex en voz (copy honesto — sin fingir afecto).

    Espejo del prompt del chat (``routers.cortex._cortex_base_prompt``) con una
    nota de brevedad propia de la voz: las respuestas habladas deben ser concisas
    (la TTS las lee), sin Markdown ni listas largas. ``web_enabled`` anuncia la
    affordance de la web (el modelo no usa lo que no sabe que tiene).
    ``language_instruction`` fija el idioma de la RESPUESTA al de la voz elegida
    (el owner reportó que con voz española el córtex contestaba en inglés)."""
    base = (
        "Eres el córtex del System Owner en una videollamada de voz: un asistente "
        "de deliberación con memoria persistente entre conversaciones. Razonas, "
        "recuerdas lo que el owner te cuenta y lo usas para ayudarle mejor. Como te "
        "están ESCUCHANDO (no leyendo), responde de forma breve, natural y hablada: "
        "sin Markdown, sin listas largas, frases cortas. No afirmes tener emociones "
        "ni consciencia: eres un modelo computacional."
    )
    if language_instruction:
        base += language_instruction
    else:
        base += " Responde con honestidad y en el idioma del owner (español o inglés)."
    if web_enabled:
        base += (
            " SÍ tienes acceso a Internet mediante tus tools web_search y web_fetch "
            "(salida por un proxy seguro). NUNCA digas que no tienes acceso a Internet "
            "ni permiso para buscar: LO TIENES. Siempre que te pregunten por información "
            "ACTUAL o del mundo real (el tiempo, noticias, precios, datos recientes, "
            "cualquier cosa que no sepas con certeza), LLAMA a web_search ANTES de "
            "responder y basa tu respuesta en los resultados, mencionando la fuente. "
            "Solo di que no lo sabes si la búsqueda no devuelve nada útil."
        )
    return base


async def run_cortex_voice_turn(
    session: AsyncSession,
    model: AssistantModelClient,
    *,
    owner_user_id: UUID,
    user_text: str,
    conversation_id: UUID | None,
    affect: AffectState | None = None,
    now: datetime | None = None,
    language_instruction: str = "",
) -> tuple[AssistantTurnResult, UUID, UUID]:
    """Corre UN turno del córtex para ``user_text`` y persiste ambos turnos.

    Mismo pipeline que ``POST /owner/cortex/turns`` (ver ``routers/cortex.py``),
    extraído aquí para que el WS de voz lo REUTILICE sin duplicarlo. El ``session``
    debe ser admin/BYPASSRLS con una transacción ABIERTA por el caller (el WS abre
    ``sessionmaker()`` + ``session.begin()`` por turno, como el endpoint REST).

    Devuelve ``(result, conversation_id, cortex_turn_id)`` — el caller usa el
    ``cortex_turn_id`` para disparar el distilador afectivo tras el COMMIT, y el
    ``conversation_id`` para mantener el hilo entre turnos del mismo socket.

    Aísla por ``owner_user_id`` en TODO acceso (las tablas del córtex no tienen
    RLS): :func:`append_turn` levanta ``PermissionError`` si el hilo no es del
    owner — defensa en profundidad frente a un ``conversation_id`` ajeno.
    """
    tenant_id = await resolve_cortex_tenant_id(session, owner_user_id)

    if conversation_id is None:
        conv = await create_conversation(
            session, owner_user_id=owner_user_id, tenant_id=tenant_id, model_id=None
        )
        conversation_id = conv.id

    await append_turn(
        session,
        conversation_id=conversation_id,
        owner_user_id=owner_user_id,
        role="user",
        content=user_text,
    )

    web_enabled = await get_cortex_web_enabled(session)
    enabled_tools = cortex_enabled_tool_names(web_enabled=web_enabled)

    # Self-context unificado (mismo composer que el chat): el WS ya cargó el
    # afecto para la prosodia y lo pasa aquí — cero lecturas duplicadas.
    turn_now = now or datetime.now(UTC)
    ctx = await load_self_context(
        session,
        None,
        owner_user_id=owner_user_id,
        tenant_id=tenant_id,
        query=user_text,
        now=turn_now,
        affect=affect,
    )
    decision = modulate_reasoning_effort(
        getattr(model, "reasoning_effort", None),
        getattr(model, "provider_kind", None),
        ctx.affect,
    )
    model = apply_effort_decision(model, decision)
    system_prompt = compose_self_context_prompt(
        _cortex_voice_base_prompt(
            web_enabled=web_enabled, language_instruction=language_instruction
        ),
        ctx,
        remember_enabled="cortex_remember" in enabled_tools,
    )

    chat_history = await recent_history_for_prompt(
        session, conversation_id=conversation_id, owner_user_id=owner_user_id
    )
    tool_ctx = CortexToolContext(
        session=session,
        owner_user_id=owner_user_id,
        tenant_id=tenant_id,
        web_enabled=web_enabled,
    )

    result = await run_cortex_turn(
        model,
        system_prompt=system_prompt,
        enabled_tools=enabled_tools,
        tool_ctx=tool_ctx,
        chat_history=chat_history,
    )

    reasoning_effort = (
        decision.effective
        if decision.effective is not None
        else getattr(model, "reasoning_effort", None)
    )
    degraded = bool(getattr(model, "degraded", False))
    cortex_turn = await append_turn(
        session,
        conversation_id=conversation_id,
        owner_user_id=owner_user_id,
        role="cortex",
        content=result.content,
        model_id=getattr(model, "model", None),
        tools_called=result.tools_called,
        rounds=result.rounds,
        reasoning_effort=reasoning_effort,
        metadata={
            "degraded": degraded,
            "recall_hits": len(ctx.known_facts),
            "channel": "voice",
            "self_context": self_context_meta(ctx, decision),
        },
    )
    # Surfacing (ADR 0078): mismo contrato que el chat — se marca en ESTA
    # transacción; un fallo previo del turno los deja pendientes (rollback).
    await mark_pursuits_surfaced(
        session,
        owner_user_id=owner_user_id,
        pursuit_ids=[p.pursuit_id for p in ctx.pending_learnings],
        now=turn_now,
    )
    return result, conversation_id, cortex_turn.id


async def load_current_affect(
    redis: Redis,
    sessionmaker: Callable[[], Any] | async_sessionmaker[AsyncSession],
    *,
    owner_user_id: UUID,
    now: datetime,
) -> AffectState:
    """El afecto VIGENTE del owner: caché Redis (decay lazy) → BD → neutro.

    Mismo orden de lectura que ``GET /owner/cortex/mind``: la caché viva primero,
    la BD si la caché está fría, y el baseline neutro si no hay snapshot. **Fail-
    open**: cualquier excepción (Redis caído, BD inaccesible) se traga y devuelve
    el baseline neutro — la voz debe hablar aunque el dial afectivo no esté.

    El reloj entra como ``now`` (determinismo; el decay se aplica en lectura)."""
    try:
        cached = await read_affect_state(redis, str(owner_user_id), now=now)
    except Exception as exc:  # caché best-effort
        _log.warning("cortex_voice.affect_cache_failed", error=str(exc))
        cached = None
    if cached is not None:
        return cached
    try:
        async with sessionmaker() as session:
            return await load_affect_state(session, owner_user_id, now=now)
    except Exception as exc:  # fail-open: la voz no debe romperse por el dial
        _log.warning("cortex_voice.affect_db_failed", error=str(exc))
        return neutral_affect_state()


def affect_frame(affect: AffectState, *, language: Language = "es") -> dict[str, Any]:
    """Frame ``{type:'affect', ...}`` para el avatar del front (builder PURO).

    Expone la EMOCIÓN viva (valence/arousal/dominance/intensity), la etiqueta de
    mood derivada (SOLO-UI, bilingüe) y los drives. El avatar mapea esto a
    color/expresión/parpadeo/sway (ADR 0075). Determinista, sin I/O."""
    return {
        "type": "affect",
        "valence": affect.emotion.valence,
        "arousal": affect.emotion.arousal,
        "dominance": affect.emotion.dominance,
        "intensity": affect.emotion.intensity,
        "mood_label": affect.mood_label(language=language),
        "drives": affect.drives.as_dict(),
    }


__all__ = [
    "CortexNoTenantError",
    "affect_frame",
    "load_current_affect",
    "run_cortex_voice_turn",
]
