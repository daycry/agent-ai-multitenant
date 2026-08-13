"""STT/TTS HTTP clients for the assistant voice mode (ADR 0073, voz F1).

Thin async wrappers over the two OpenAI-compatible media services:

  * **STT** — faster-whisper-server: ``POST /v1/audio/transcriptions``
    (multipart audio → ``{"text": ...}``).
  * **TTS** — Kokoro-FastAPI: ``POST /v1/audio/speech``
    (JSON ``{model,input,voice,response_format}`` → raw audio bytes).

These are **media services, NOT LLMProviders** — the closed LLM catalog (ADR
0021) is untouched; the assistant's "brain" stays provider-agnostic.

The `SpeechToText`/`TextToSpeech` Protocols let the voice session run against
fakes in tests, so the per-turn flow is verifiable without the real models.
"""

from __future__ import annotations

from typing import Any, Protocol

import httpx


class SpeechToText(Protocol):
    async def transcribe(
        self, audio: bytes, *, content_type: str = "audio/wav", language: str | None = None
    ) -> str: ...


class TextToSpeech(Protocol):
    async def synthesize(
        self, text: str, *, voice: str, response_format: str = "mp3", speed: float = 1.0
    ) -> bytes: ...


class HttpSpeechToText:
    """faster-whisper-server STT over its OpenAI-compatible transcription API."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str = "Systran/faster-whisper-small",
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client
        self._timeout = timeout

    async def transcribe(
        self, audio: bytes, *, content_type: str = "audio/wav", language: str | None = None
    ) -> str:
        # Filename CON extensión derivada del mime: faster-whisper sniffa el
        # contenido, pero otros backends OpenAI-compatibles validan la
        # extensión del upload y rechazarían un fichero llamado solo `audio`.
        files = {"file": (_stt_filename(content_type), audio, content_type)}
        data: dict[str, str] = {"model": self._model, "response_format": "json"}
        if language:
            data["language"] = language
        url = f"{self._base_url}/v1/audio/transcriptions"
        if self._client is not None:
            resp = await self._client.post(url, files=files, data=data, timeout=self._timeout)
            resp.raise_for_status()
            return _extract_text(resp.json())
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, files=files, data=data)
            resp.raise_for_status()
            return _extract_text(resp.json())


class HttpTextToSpeech:
    """Kokoro-FastAPI TTS over its OpenAI-compatible speech API."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str = "kokoro",
        client: httpx.AsyncClient | None = None,
        timeout: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._client = client
        self._timeout = timeout

    async def synthesize(
        self, text: str, *, voice: str, response_format: str = "mp3", speed: float = 1.0
    ) -> bytes:
        payload: dict[str, Any] = {
            "model": self._model,
            "input": text,
            "voice": voice,
            "response_format": response_format,
        }
        # `speed` rides the payload ONLY when it differs from the default, so a
        # plain (assistant) call sends the exact legacy body and Kokoro applies
        # its own default. The córtex voice WS sets it from the affective arousal.
        if speed != 1.0:
            payload["speed"] = speed
        url = f"{self._base_url}/v1/audio/speech"
        if self._client is not None:
            resp = await self._client.post(url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
            return resp.content
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.content


class SttResponseError(RuntimeError):
    """A 200 from the STT whose body carries no usable transcript.

    Distinto de «silencio» (``{"text": ""}`` es VÁLIDO): esto es un backend
    que respondió otra forma (``{"error": ...}``, lista de segmentos, null…).
    Antes se degradaba a transcript vacío y el usuario veía «no te he oído»
    con el STT roto — ahora aflora como error explícito (el WS lo convierte
    en un frame ``error`` visible)."""


def _extract_text(body: Any) -> str:
    """Pull the transcript out of an OpenAI-style transcription response.

    Raises :class:`SttResponseError` for any shape that is not a transcript —
    a broken STT must never masquerade as silence."""
    if isinstance(body, dict):
        text = body.get("text")
        if isinstance(text, str):
            return text
    if isinstance(body, str):
        return body
    raise SttResponseError(f"unexpected STT response shape: {str(body)[:200]!r}")


# Extensión de fichero por content_type para el multipart del STT: los backends
# OpenAI-compatibles estrictos validan la EXTENSIÓN del upload (faster-whisper
# sniffa el contenido, pero no todos lo hacen). El navegador manda webm/opus.
_AUDIO_EXTENSIONS: dict[str, str] = {
    "audio/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "audio/aac": "aac",
    "audio/flac": "flac",
}


def _stt_filename(content_type: str) -> str:
    """``audio.<ext>`` derivado del mime (``audio.bin`` si es desconocido)."""
    base = content_type.split(";", maxsplit=1)[0].strip().lower()
    return f"audio.{_AUDIO_EXTENSIONS.get(base, 'bin')}"


__all__ = [
    "HttpSpeechToText",
    "HttpTextToSpeech",
    "SpeechToText",
    "SttResponseError",
    "TextToSpeech",
]
