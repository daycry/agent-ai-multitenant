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
async def test_on_transcript_fires_before_the_slow_brain_call() -> None:
    """Streaming del transcript: el callback se dispara TRAS el STT y ANTES del
    cerebro, para que el usuario vea sus palabras al instante (el turno completo
    puede tardar 40-90s) y haya tráfico intermedio que aleje el keepalive."""
    order: list[str] = []

    async def transcribe(audio: bytes) -> str:
        order.append("stt")
        return "hola mundo"

    async def respond(text: str) -> str:
        order.append("brain")
        return "respuesta"

    async def synthesize(text: str) -> bytes:
        order.append("tts")
        return b"a"

    seen: list[str] = []

    async def on_transcript(user_text: str) -> None:
        order.append("on_transcript")
        seen.append(user_text)

    turn = await VoiceSession(transcribe, respond, synthesize).handle_turn(
        b"audio", on_transcript=on_transcript
    )

    assert order == ["stt", "on_transcript", "brain", "tts"]
    assert seen == ["hola mundo"]
    assert turn.user_text == "hola mundo"


@pytest.mark.asyncio
async def test_on_transcript_not_called_on_silence() -> None:
    async def transcribe(audio: bytes) -> str:
        return "   "

    async def respond(text: str) -> str:  # pragma: no cover - must not run
        raise AssertionError("brain must not run on silence")

    async def synthesize(text: str) -> bytes:  # pragma: no cover
        raise AssertionError("tts must not run on silence")

    fired: list[str] = []

    async def on_transcript(user_text: str) -> None:
        fired.append(user_text)

    turn = await VoiceSession(transcribe, respond, synthesize).handle_turn(
        b"silence", on_transcript=on_transcript
    )
    assert turn.empty is True
    assert fired == []  # sin habla no hay transcript que emitir


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
    # Default speed (1.0) is NOT sent — payload stays identical to the legacy one.
    assert "speed" not in seen["body"]


@pytest.mark.asyncio
async def test_http_tts_forwards_speed_param() -> None:
    """Kokoro speed modulation (córtex F5): a non-default ``speed`` rides the
    /v1/audio/speech payload so the answer's pace follows the affective arousal."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        import json as _json

        seen["body"] = _json.loads(request.content)
        return httpx.Response(200, content=b"AUDIO")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        tts = HttpTextToSpeech("http://tts:8880", client=client)
        audio = await tts.synthesize("hola", voice="ef_dora", speed=1.4)

    assert audio == b"AUDIO"
    assert seen["body"]["speed"] == 1.4


@pytest.mark.asyncio
async def test_http_stt_defaults_content_type_to_wav() -> None:
    """Default content_type stays audio/wav — the legacy assistant behaviour."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "ok"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stt = HttpSpeechToText("http://stt:8000", client=client)
        await stt.transcribe(b"audio-bytes")

    body = seen["body"]
    assert isinstance(body, bytes)
    assert b"audio/wav" in body


@pytest.mark.asyncio
async def test_http_stt_propagates_custom_content_type() -> None:
    """Shared robustness fix (córtex F5): the real audio mime (e.g. audio/webm
    from MediaRecorder) is propagated to STT instead of being forced to wav —
    forcing wav was the assistant voice bug (the browser sends webm/opus)."""
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "ok"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        stt = HttpSpeechToText("http://stt:8000", client=client)
        await stt.transcribe(b"webm-bytes", content_type="audio/webm")

    body = seen["body"]
    assert isinstance(body, bytes)
    assert b"audio/webm" in body
    assert b"audio/wav" not in body
