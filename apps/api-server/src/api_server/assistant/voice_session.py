"""Per-turn orchestration for the assistant voice mode (ADR 0073, voz F1).

``VoiceSession`` is the pure turn logic, decoupled from the WebSocket transport
and from the concrete STT/TTS/brain so it is fully unit-testable:

    audio (PCM/wav) ──▶ transcribe ──▶ user text
                                          │
                                          ▼
                                       respond (the SAME provider-agnostic
                                       assistant brain as /assistant/chat)
                                          │
                                          ▼
                          answer text ──▶ synthesize ──▶ audio out

The WebSocket endpoint injects the real callables (STT client, the assistant
graph, TTS client + chosen voice); tests inject fakes + a ScriptedAssistantModel.
Because ``respond`` is just "user text → answer text", the voice mode reuses the
existing brain verbatim and inherits its provider-agnosticism and memory.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class VoiceTurn:
    """The outcome of one spoken turn."""

    user_text: str
    answer_text: str
    audio: bytes
    # True when STT produced nothing (silence / no speech) — the caller should
    # not run the brain or synthesize, just prompt the user to speak again.
    empty: bool = False


Transcribe = Callable[[bytes], Awaitable[str]]
Respond = Callable[[str], Awaitable[str]]
Synthesize = Callable[[str], Awaitable[bytes]]
OnTranscript = Callable[[str], Awaitable[None]]


@dataclass
class VoiceSession:
    """Drives one spoken turn through STT → brain → TTS."""

    transcribe: Transcribe
    respond: Respond
    synthesize: Synthesize

    async def handle_turn(
        self, audio: bytes, *, on_transcript: OnTranscript | None = None
    ) -> VoiceTurn:
        """Transcribe the utterance, answer with the assistant brain, synthesize.

        Empty/whitespace transcripts short-circuit (no brain call, no TTS); an
        empty answer is returned without synthesizing (no point speaking silence).

        ``on_transcript`` (opcional) se dispara TRAS el STT y ANTES de la llamada
        lenta al cerebro, con el texto transcrito: el WS lo usa para enviar el
        frame ``transcript`` al instante — el usuario ve sus palabras mientras el
        modelo piensa (el turno completo puede durar 40-90s), y ese tráfico
        intermedio aleja el keepalive del WS en un turno largo pero legítimo."""
        user_text = (await self.transcribe(audio)).strip()
        if not user_text:
            return VoiceTurn(user_text="", answer_text="", audio=b"", empty=True)
        if on_transcript is not None:
            await on_transcript(user_text)
        answer = (await self.respond(user_text)).strip()
        audio_out = await self.synthesize(answer) if answer else b""
        return VoiceTurn(user_text=user_text, answer_text=answer, audio=audio_out)


__all__ = ["Respond", "Synthesize", "Transcribe", "VoiceSession", "VoiceTurn"]
