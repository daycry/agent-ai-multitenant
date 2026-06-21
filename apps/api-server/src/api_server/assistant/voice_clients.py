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
    async def synthesize(self, text: str, *, voice: str, response_format: str = "mp3") -> bytes: ...


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
        files = {"file": ("audio", audio, content_type)}
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

    async def synthesize(self, text: str, *, voice: str, response_format: str = "mp3") -> bytes:
        payload = {
            "model": self._model,
            "input": text,
            "voice": voice,
            "response_format": response_format,
        }
        url = f"{self._base_url}/v1/audio/speech"
        if self._client is not None:
            resp = await self._client.post(url, json=payload, timeout=self._timeout)
            resp.raise_for_status()
            return resp.content
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            return resp.content


def _extract_text(body: Any) -> str:
    """Pull the transcript out of an OpenAI-style transcription response."""
    if isinstance(body, dict):
        text = body.get("text")
        if isinstance(text, str):
            return text
    if isinstance(body, str):
        return body
    return ""


__all__ = [
    "HttpSpeechToText",
    "HttpTextToSpeech",
    "SpeechToText",
    "TextToSpeech",
]
