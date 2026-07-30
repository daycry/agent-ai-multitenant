"""WebSocket de voz del córtex del System Owner — ``/ws/owner/cortex/voice``.

Córtex F5 (ADR 0073 voz + 0075 afecto). **Clona** el transporte por-turno del WS
de voz del asistente (:mod:`api_server.routers.assistant_voice`) cambiando sólo
tres cosas:

  1. **Gate** = System Owner DB-authoritative (``_is_db_system_owner``, ADR 0074),
     NO el gate de tenant del asistente. Un no-owner (aunque forje el claim ``own``)
     → cierre 1008 sin tocar el cerebro.
  2. **Cerebro** = el córtex (``run_cortex_voice_turn``, mismo pipeline que
     ``POST /owner/cortex/turns``) en vez del asistente.
  3. **Afecto** = tras el ``answer`` y ANTES del binario de audio, se emite un
     frame ``{type:'affect', valence, arousal, dominance, mood_label, drives}``
     (de F2) que el avatar mapea a color/expresión; y la síntesis Kokoro se
     **modula por el arousal** vigente (``voice_params_from_affect`` → ``speed``).

Protocolo (idéntico al asistente):

  cliente → frames binarios de audio … luego JSON ``{type:'eot'}``; control
            ``{type:'config', voice, audio_mime}`` y ``{type:'reset'}``.
  servidor → ``{type:'ready', voice}`` → ``{type:'transcript', text}`` →
             ``{type:'answer', text}`` → ``{type:'affect', …}`` → <binario audio>
             → ``{type:'turn_end'}``.

Auth: el JWT viaja como ``?token=`` (el navegador no pone cabeceras) y exige una
sesión Redis viva (``_resolve_principal`` reusado de ``routers/ws.py``). STT/TTS
son medios internos (ADR 0021 intacto: sin 5º provider LLM). El ``content_type``
del audio se propaga REAL a STT (fix compartido: el navegador manda webm/opus, no
wav).

> Honestidad (ADR 0075 §6): el frame afectivo es un modelo computacional
> determinista, NO sentimientos reales — el front lo rotula como tal.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis

from api_server.assistant.graph import AssistantModelClient
from api_server.assistant.voice_clients import (
    HttpSpeechToText,
    HttpTextToSpeech,
    SpeechToText,
    TextToSpeech,
)
from api_server.assistant.voice_session import VoiceSession, VoiceTurn
from api_server.auth.deps import AuthPrincipal, _is_db_system_owner, get_redis, get_session_store
from api_server.auth.sessions import SessionStore
from api_server.celery_client import enqueue_cortex_distill_affect
from api_server.config import get_settings
from api_server.cortex.threads import CortexNoTenantError
from api_server.cortex.voice_affect import voice_params_from_affect
from api_server.cortex.voice_turn import affect_frame, load_current_affect, run_cortex_voice_turn
from api_server.db.session import get_admin_sessionmaker
from api_server.llm_providers.vault import LLMProviderVaultStore
from api_server.routers.assistant_voice import (
    _MAX_UTTERANCE_BYTES,
    _SUPPORTED_VOICES,  # noqa: F401  (re-exported allowlist; kept for parity/tests)
    _reject,
    _resolve_voice,
    voice_language_instruction,
)
from api_server.routers.cortex import build_cortex_default_model
from api_server.routers.llm_providers import get_provider_vault_store
from api_server.routers.ws import _resolve_principal

_log = structlog.get_logger("api_server.cortex_voice")

router = APIRouter(tags=["cortex-voice"])

_CLOSE_POLICY = 1008
# Default audio mime when the client doesn't announce one — the browser sends
# webm/opus from MediaRecorder, but a bare PCM/wav client falls back to wav.
_DEFAULT_AUDIO_MIME = "audio/wav"


# ---------------------------------------------------------------------------
# Media injection seams (overridden in tests with fakes — no real STT/TTS)
# ---------------------------------------------------------------------------
def get_cortex_stt() -> SpeechToText:
    """The córtex voice STT client (faster-whisper). Overridden with a fake in tests."""
    return HttpSpeechToText(get_settings().assistant_stt_url)


def get_cortex_tts() -> TextToSpeech:
    """The córtex voice TTS client (Kokoro). Overridden with a fake in tests."""
    return HttpTextToSpeech(get_settings().assistant_tts_url)


async def get_cortex_voice_model(
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> AssistantModelClient:
    """The córtex brain for the voice WS (platform-default model, no header gate).

    A FastAPI ``Depends`` seam resolved BEFORE the handler body — so tests can
    override it with a ``ScriptedAssistantModel`` (the WS can't override a value
    it builds inline). It builds the same singleton model as the REST endpoint
    (:func:`api_server.routers.cortex.build_cortex_default_model`) but skips the
    REST ``require_system_owner`` gate, because the WS already gates the socket
    DB-authoritatively in its accept (and a header principal isn't available
    over a WebSocket). A 503 (no model configured) closes the socket with 1008."""
    return await build_cortex_default_model(vault)


# El _reject compartido (frame `error` + reason recortado a 123 bytes, RFC
# 6455) se importa de assistant_voice — antes cada WS llevaba su copia y un
# detail largo derribaba el socket con un 1006 mudo.


async def _resolve_voice_model(
    ws: WebSocket, vault: LLMProviderVaultStore | None
) -> AssistantModelClient:
    """The córtex brain, honouring a test ``dependency_overrides`` of the seam.

    The model is built AFTER the owner gate (so a rejected socket never touches
    the brain), which means it can't ride FastAPI's ``Depends`` resolution. We
    therefore look up :func:`get_cortex_voice_model` in the app's
    ``dependency_overrides`` ourselves (tests inject a ``ScriptedAssistantModel``)
    and otherwise call the real factory. ``override`` may be sync (a lambda) or a
    coroutine; both are supported."""
    override = ws.app.dependency_overrides.get(get_cortex_voice_model)
    if override is not None:
        result = override()
        if hasattr(result, "__await__"):
            return await result  # type: ignore[no-any-return]
        return result  # type: ignore[no-any-return]
    return await get_cortex_voice_model(vault=vault)


@dataclass
class _VoiceLoopState:
    """Per-socket mutable state: utterance buffer + voice + audio mime + thread id."""

    voice: str
    audio_mime: str = _DEFAULT_AUDIO_MIME
    conversation_id: Any = None  # UUID | None — kept across turns of this socket
    buffer: bytearray = field(default_factory=bytearray)


async def _run_turn(
    ws: WebSocket,
    audio: bytes,
    state: _VoiceLoopState,
    *,
    principal: AuthPrincipal,
    model: AssistantModelClient,
    stt: SpeechToText,
    tts: TextToSpeech,
) -> None:
    """Process one end-of-turn: STT → córtex brain → affect frame → modulated TTS.

    The brain runs in ONE admin/BYPASSRLS transaction (owner-scoped, like the REST
    endpoint); the affective distiller fires post-commit (fire-and-forget). The
    answer's speech speed follows the live affective arousal. A media/brain failure
    surfaces as an ``error`` frame, never closing the socket."""
    owner_id = principal.user_id
    now = datetime.now(UTC)
    redis: Redis = get_redis()
    admin_sessionmaker = get_admin_sessionmaker()

    # Read the live affect BEFORE synthesis so the speech pace follows it (fail-open
    # to neutral baseline if the dial is unavailable). Pure mapping → speed.
    affect = await load_current_affect(redis, admin_sessionmaker, owner_user_id=owner_id, now=now)
    params = voice_params_from_affect(affect, voice=state.voice)
    speed = params["speed"]

    mime = state.audio_mime

    async def _transcribe(a: bytes) -> str:
        return await stt.transcribe(a, content_type=mime)

    cortex_turn_id_holder: dict[str, Any] = {}

    async def _respond(user_text: str) -> str:
        # ONE admin transaction (owner-scoped, no RLS) — mirrors POST /turns.
        # The affect read for prosody above is passed down so the self-context
        # does not re-read it (single load per turn).
        async with admin_sessionmaker() as session, session.begin():
            result, conv_id, turn_id = await run_cortex_voice_turn(
                session,
                model,
                owner_user_id=owner_id,
                user_text=user_text,
                conversation_id=state.conversation_id,
                affect=affect,
                now=now,
                language_instruction=voice_language_instruction(state.voice),
            )
        state.conversation_id = conv_id
        cortex_turn_id_holder["id"] = turn_id
        return result.content

    async def _emit_transcript(user_text: str) -> None:
        # Transcript + thinking tras el STT (antes del cerebro): feedback
        # inmediato y tráfico intermedio que evita que el keepalive del WS mate
        # un turno del córtex largo pero legítimo (40-90s con razonamiento).
        await ws.send_json({"type": "transcript", "text": user_text})
        await ws.send_json({"type": "thinking"})

    session = VoiceSession(
        transcribe=_transcribe,
        respond=_respond,
        synthesize=lambda t: tts.synthesize(t, voice=state.voice, speed=speed),
    )
    try:
        turn: VoiceTurn = await session.handle_turn(audio, on_transcript=_emit_transcript)
    except CortexNoTenantError as exc:
        await ws.send_json({"type": "error", "detail": f"cortex has no tenant: {exc}"})
        return
    except Exception as exc:  # media/brain failure must not kill the socket
        _log.warning("cortex_voice.turn_failed", error=str(exc))
        await ws.send_json({"type": "error", "detail": f"voice turn failed: {exc}"})
        return

    if turn.empty:
        await ws.send_json({"type": "turn_end", "empty": True})
        return

    await ws.send_json({"type": "answer", "text": turn.answer_text})
    # Affect frame for the avatar (color/expression/sway) BEFORE the audio.
    await ws.send_json(affect_frame(affect))
    if turn.audio:
        await ws.send_bytes(turn.audio)
    await ws.send_json({"type": "turn_end"})

    # Fire the affective distiller post-answer (fire-and-forget, off the hot-path;
    # a broker outage is swallowed inside enqueue_cortex_distill_affect).
    turn_id = cortex_turn_id_holder.get("id")
    if turn_id is not None:
        await enqueue_cortex_distill_affect(turn_id)


async def _handle_frame(
    ws: WebSocket,
    msg: MutableMapping[str, Any],
    state: _VoiceLoopState,
    *,
    principal: AuthPrincipal,
    model: AssistantModelClient,
    stt: SpeechToText,
    tts: TextToSpeech,
) -> bool:
    """Process one received WS frame; return False to stop the loop."""
    if msg.get("type") == "websocket.disconnect":
        return False
    data = msg.get("bytes")
    if data is not None:
        if len(state.buffer) + len(data) > _MAX_UTTERANCE_BYTES:
            await ws.send_json({"type": "error", "detail": "utterance too large"})
            state.buffer.clear()
        else:
            state.buffer.extend(data)
        return True
    text = msg.get("text")
    if not text:
        return True
    try:
        control = json.loads(text)
    except json.JSONDecodeError:
        return True
    ctype = control.get("type")
    if ctype == "config":
        # Voice validated against the allowlist before it reaches TTS; the audio
        # mime (e.g. audio/webm from MediaRecorder) is propagated to STT as-is.
        state.voice = _resolve_voice(str(control.get("voice") or ""), state.voice)
        mime = control.get("audio_mime")
        if isinstance(mime, str) and mime.strip():
            state.audio_mime = mime.strip()
        await ws.send_json({"type": "ready", "voice": state.voice})
    elif ctype == "reset":
        state.buffer.clear()
    elif ctype == "eot":
        audio = bytes(state.buffer)
        state.buffer.clear()
        if not audio:
            await ws.send_json({"type": "turn_end", "empty": True})
        else:
            await _run_turn(ws, audio, state, principal=principal, model=model, stt=stt, tts=tts)
    return True


@router.websocket("/ws/owner/cortex/voice")
async def cortex_voice(
    ws: WebSocket,
    token: str | None = Query(default=None),
    sessions: SessionStore = Depends(get_session_store),
    stt: SpeechToText = Depends(get_cortex_stt),
    tts: TextToSpeech = Depends(get_cortex_tts),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> None:
    """Per-turn spoken conversation with the córtex — System Owner only."""
    await ws.accept()
    principal = await _resolve_principal(token, sessions)
    if principal is None:
        await _reject(ws, "unauthenticated")
        return
    # DB-authoritative owner gate (ADR 0074): the `own` claim is only a hint, so a
    # non-owner (even with a forged claim) is rejected here, BEFORE the brain is
    # built or any turn runs — never touching the córtex on a rejected socket.
    if not await _is_db_system_owner(principal.user_id):
        await _reject(ws, "forbidden")
        return
    try:
        model = await _resolve_voice_model(ws, vault)
    except HTTPException as exc:
        await _reject(ws, str(exc.detail))
        return

    state = _VoiceLoopState(voice=get_settings().cortex_tts_default_voice)
    await ws.send_json({"type": "ready", "voice": state.voice})
    try:
        while await _handle_frame(
            ws, await ws.receive(), state, principal=principal, model=model, stt=stt, tts=tts
        ):
            pass
    except WebSocketDisconnect:
        return
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("cortex_voice.socket_error", error=str(exc))
        with contextlib.suppress(Exception):
            await ws.close(code=1011)


__all__ = ["get_cortex_stt", "get_cortex_tts", "router"]
