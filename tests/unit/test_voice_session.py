"""Unit tests for the assistant voice per-turn orchestration (ADR 0073, voz F1)."""

from __future__ import annotations

import httpx
import pytest
from api_server.assistant.voice_clients import HttpSpeechToText, HttpTextToSpeech
from api_server.assistant.voice_session import VoiceSession


@pytest.mark.asyncio
async def test_handle_turn_runs_stt_then_brain_then_tts() -> None:
    calls: list[str] = []

    async def transcribe(audio: bytes) -> str:
        calls.append("stt")
        assert audio == b"RIFF-audio"
        return "  hola, me llamo Jordi  "

    async def respond(text: str) -> str:
        calls.append("brain")
        assert text == "hola, me llamo Jordi"  # trimmed
        return "¡Encantado, Jordi!"

    async def synthesize(text: str) -> bytes:
        calls.append("tts")
        assert text == "¡Encantado, Jordi!"
        return b"MP3-bytes"

    turn = await VoiceSession(transcribe, respond, synthesize).handle_turn(b"RIFF-audio")

    assert calls == ["stt", "brain", "tts"]
    assert turn.user_text == "hola, me llamo Jordi"
    assert turn.answer_text == "¡Encantado, Jordi!"
    assert turn.audio == b"MP3-bytes"
    assert turn.empty is False


@pytest.mark.asyncio
async def test_empty_transcript_short_circuits_brain_and_tts() -> None:
    calls: list[str] = []

    async def transcribe(audio: bytes) -> str:
        return "   "  # silence / no speech

    async def respond(text: str) -> str:
        calls.append("brain")
        return "should not run"

    async def synthesize(text: str) -> bytes:
        calls.append("tts")
        return b"x"

    turn = await VoiceSession(transcribe, respond, synthesize).handle_turn(b"silence")

    assert turn.empty is True
    assert turn.user_text == ""
    assert turn.audio == b""
    assert calls == []  # neither brain nor tts ran


@pytest.mark.asyncio
async def test_empty_answer_skips_synthesis() -> None:
    async def transcribe(audio: bytes) -> str:
        return "hola"

    async def respond(text: str) -> str:
        return "   "  # brain produced nothing

    async def synthesize(text: str) -> bytes:  # pragma: no cover - must not run
        raise AssertionError("synthesize must not run for an empty answer")

    turn = await VoiceSession(transcribe, respond, synthesize).handle_turn(b"a")

    assert turn.empty is False
    assert turn.answer_text == ""
    assert turn.audio == b""


@pytest.mark.asyncio
async def test_http_stt_posts_multipart_and_reads_text() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["has_multipart"] = b"form-data" in request.content
        return httpx.Response(200, json={"text": "transcripción"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stt = HttpSpeechToText("http://stt:8000", client=client)
        text = await stt.transcribe(b"audio-bytes")

    assert text == "transcripción"
    assert seen["url"] == "http://stt:8000/v1/audio/transcriptions"
    assert seen["has_multipart"] is True


@pytest.mark.asyncio
async def test_http_tts_posts_voice_and_returns_audio() -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["url"] = str(request.url)
        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, content=b"AUDIO")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tts = HttpTextToSpeech("http://tts:8880", client=client)
        audio = await tts.synthesize("hola", voice="am_michael")

    assert audio == b"AUDIO"
    assert seen["url"] == "http://tts:8880/v1/audio/speech"
    assert seen["body"]["voice"] == "am_michael"
    assert seen["body"]["input"] == "hola"
