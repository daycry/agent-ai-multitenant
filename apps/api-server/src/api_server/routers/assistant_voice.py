"""WebSocket voice endpoint for the personal assistant (ADR 0073, voz F1).

``/ws/assistant/voice`` — a per-turn spoken conversation that REUSES the existing
provider-agnostic assistant brain (the same graph + memory as ``/assistant/chat``)
plus the STT/TTS media services. F1 is non-streaming (one full turn at a time):

  client: binary audio frames (PCM16/wav) … then a text frame {"type":"eot"}
  server: {"type":"transcript",text} → {"type":"answer",text} → <binary audio>
          → {"type":"turn_end"}

Auth mirrors ``routers/ws.py`` exactly (the browser can't set headers, so the JWT
travels as ``?token=``; a live Redis session is required) and the personal-assistant
tenant gate (``require_assistant_access``) is enforced — any failure closes the
socket with 1008. Voice/STT/TTS are MEDIA add-ons: the closed LLM catalog (ADR
0021) is untouched, and the answer is produced by whichever provider the tenant
already assigned to the assistant.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import MutableMapping
from dataclasses import dataclass, field
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from redis.asyncio import Redis  # noqa: F401  (kept for parity / future stream taps)

from api_server.assistant.config import get_assistant_identity
from api_server.assistant.graph import run_assistant_turn
from api_server.assistant.memory import augment_system_prompt, recall_user_memories
from api_server.assistant.tools import AssistantToolContext
from api_server.assistant.voice_clients import HttpSpeechToText, HttpTextToSpeech
from api_server.assistant.voice_session import VoiceSession, VoiceTurn
from api_server.auth.deps import (
    AuthPrincipal,
    get_session_store,
    open_tenant_session,
)
from api_server.auth.sessions import SessionStore
from api_server.config import get_settings
from api_server.llm_providers.vault import LLMProviderVaultStore
from api_server.routers._helpers import require_tenant_id
from api_server.routers.assistant import get_assistant_model, require_assistant_access
from api_server.routers.llm_providers import get_provider_vault_store
from api_server.routers.ws import _resolve_principal

_log = structlog.get_logger("api_server.assistant_voice")

router = APIRouter(tags=["assistant-voice"])

_CLOSE_POLICY = 1008
# Cap the buffered utterance so a misbehaving client can't grow it unbounded
# (defence in depth; a normal turn is a few seconds of 16 kHz PCM16 ≈ <1 MB).
_MAX_UTTERANCE_BYTES = 8 * 1024 * 1024

# Kokoro voices offered by the UI (components/assistant/voice-call.tsx). The
# client's `voice` is forwarded to the internal TTS, so it MUST be validated
# server-side against this allowlist — a free-form value would reach Kokoro raw.
_SUPPORTED_VOICES = frozenset(
    {"af_heart", "am_michael", "bf_emma", "bm_george", "ef_dora", "em_alex"}
)


def _resolve_voice(requested: str, current: str) -> str:
    """The voice to use: ``requested`` iff it is a supported Kokoro voice,
    otherwise keep ``current`` (an unsupported/empty value never reaches TTS)."""
    return requested if requested in _SUPPORTED_VOICES else current


# RFC 6455: el payload de un frame de control cabe en 125 bytes; 2 van al
# código de cierre → el reason del close se limita a 123 bytes UTF-8. Un
# reason mayor hace LANZAR a ws.close() — y con el suppress de abajo el
# navegador acababa viendo un 1006 mudo en vez del 1008 con diagnóstico
# (visto en vivo: el detail de «no hay proveedor LLM» medía 205 bytes).
_MAX_CLOSE_REASON_BYTES = 123


def _clip_close_reason(reason: str) -> str:
    """Recorta ``reason`` a ≤123 bytes UTF-8 sin partir un carácter multibyte."""
    raw = reason.encode("utf-8")
    if len(raw) <= _MAX_CLOSE_REASON_BYTES:
        return reason
    return raw[:_MAX_CLOSE_REASON_BYTES].decode("utf-8", errors="ignore")


async def _reject(ws: WebSocket, reason: str) -> None:
    """Cierra con 1008 SIN perder el diagnóstico.

    El motivo completo viaja primero en un frame ``error`` (sin límite de
    tamaño); el close lleva la versión recortada a 123 bytes para que el
    cierre en sí nunca falle."""
    with contextlib.suppress(Exception):
        await ws.send_json({"type": "error", "detail": reason})
    with contextlib.suppress(Exception):
        await ws.close(code=_CLOSE_POLICY, reason=_clip_close_reason(reason))


async def _respond(principal: AuthPrincipal, model: Any, user_text: str) -> str:
    """Run ONE assistant turn for `user_text`, reusing the chat brain verbatim.

    Opens a fresh RLS-bound tenant session per turn (like the REST chat endpoint),
    recalls the user's memory, folds it into the system prompt, and runs the
    provider-agnostic graph. Returns the answer text."""
    tenant_id = require_tenant_id(principal)
    async with open_tenant_session(principal) as session:
        identity = await get_assistant_identity(session, tenant_id)
        enabled_tools = identity.effective_tools()
        known_facts = await recall_user_memories(
            session, tenant_id=tenant_id, user_id=principal.user_id
        )
        system_prompt = augment_system_prompt(
            identity.system_prompt(),
            known_facts=known_facts,
            remember_enabled="remember_about_me" in enabled_tools,
        )
        tool_ctx = AssistantToolContext(
            session=session, tenant_id=tenant_id, user_id=principal.user_id
        )
        result = await run_assistant_turn(
            model,
            system_prompt=system_prompt,
            enabled_tools=enabled_tools,
            tool_ctx=tool_ctx,
            chat_history=[{"role": "user", "content": user_text}],
        )
    return result.content


async def _run_turn(
    ws: WebSocket,
    audio: bytes,
    *,
    principal: AuthPrincipal,
    model: Any,
    stt: HttpSpeechToText,
    tts: HttpTextToSpeech,
    voice: str,
    audio_mime: str = "audio/wav",
) -> None:
    """Process one end-of-turn: STT → brain → TTS, sending the result frames.

    A media/provider failure surfaces as an ``error`` frame, never closing the
    socket (the user can keep talking). ``audio_mime`` is the real content type
    the browser announced (MediaRecorder emits webm/opus, not wav) — propagating
    it (instead of hardcoding ``audio/wav``) is the shared STT robustness fix."""

    async def _emit_transcript(user_text: str) -> None:
        # Feedback inmediato tras el STT (antes del cerebro): el usuario ve sus
        # palabras y el `thinking` mantiene tráfico mientras el modelo piensa —
        # un turno largo (40-90s) ya no muere por el keepalive del WS.
        await ws.send_json({"type": "transcript", "text": user_text})
        await ws.send_json({"type": "thinking"})

    session = VoiceSession(
        transcribe=lambda a: stt.transcribe(a, content_type=audio_mime),
        respond=lambda t: _respond(principal, model, t),
        synthesize=lambda t: tts.synthesize(t, voice=voice),
    )
    try:
        turn: VoiceTurn = await session.handle_turn(audio, on_transcript=_emit_transcript)
    except Exception as exc:  # media/provider failure must not kill the socket
        _log.warning("assistant_voice.turn_failed", error=str(exc))
        await ws.send_json({"type": "error", "detail": f"voice turn failed: {exc}"})
        return
    if turn.empty:
        await ws.send_json({"type": "turn_end", "empty": True})
        return
    await ws.send_json({"type": "answer", "text": turn.answer_text})
    if turn.audio:
        await ws.send_bytes(turn.audio)
    await ws.send_json({"type": "turn_end"})


@dataclass
class _VoiceLoopState:
    """Per-socket mutable state: utterance buffer + chosen voice + audio mime."""

    voice: str
    # Real content type the client announced for its audio (config.audio_mime).
    # Default wav for a bare PCM client; the browser overrides it with webm/opus.
    audio_mime: str = "audio/wav"
    buffer: bytearray = field(default_factory=bytearray)


async def _handle_frame(
    ws: WebSocket,
    msg: MutableMapping[str, Any],
    state: _VoiceLoopState,
    *,
    principal: AuthPrincipal,
    model: Any,
    stt: HttpSpeechToText,
    tts: HttpTextToSpeech,
) -> bool:
    """Process one received WS frame; return False to stop the loop.

    Binary frames buffer audio (capped); JSON control frames set the voice
    (``config``), clear the buffer (``reset``) or process the turn (``eot``)."""
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
        # Validate against the supported-voice allowlist before it reaches TTS;
        # propagate the real audio mime (webm/opus from MediaRecorder) to STT.
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
            await _run_turn(
                ws,
                audio,
                principal=principal,
                model=model,
                stt=stt,
                tts=tts,
                voice=state.voice,
                audio_mime=state.audio_mime,
            )
    return True


@router.websocket("/ws/assistant/voice")
async def assistant_voice(
    ws: WebSocket,
    token: str | None = Query(default=None),
    sessions: SessionStore = Depends(get_session_store),
    vault: LLMProviderVaultStore | None = Depends(get_provider_vault_store),
) -> None:
    """Per-turn spoken conversation with the personal assistant."""
    await ws.accept()
    principal = await _resolve_principal(token, sessions)
    if principal is None:
        await _reject(ws, "unauthenticated")
        return
    # Personal-assistant tenant gate (Tenant Admin + feature toggle). The gate
    # opens its own RLS session; an HTTPException → policy close (never a 500).
    try:
        async with open_tenant_session(principal) as gate_session:
            await require_assistant_access(principal, gate_session)
        model = await get_assistant_model(principal=principal, vault=vault)
    except HTTPException as exc:
        await _reject(ws, str(exc.detail))
        return

    settings = get_settings()
    stt = HttpSpeechToText(settings.assistant_stt_url)
    tts = HttpTextToSpeech(settings.assistant_tts_url)
    state = _VoiceLoopState(voice=settings.assistant_tts_default_voice)

    await ws.send_json({"type": "ready", "voice": state.voice})
    try:
        while await _handle_frame(
            ws, await ws.receive(), state, principal=principal, model=model, stt=stt, tts=tts
        ):
            pass
    except WebSocketDisconnect:
        return
    except Exception as exc:  # pragma: no cover - defensive
        _log.warning("assistant_voice.socket_error", error=str(exc))
        with contextlib.suppress(Exception):
            await ws.close(code=1011)


__all__ = ["router"]
