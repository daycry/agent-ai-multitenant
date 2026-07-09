"""Endurecimiento del modo voz (diagnóstico 2026-07-09).

Cuatro causas raíz encontradas reproduciendo la voz en vivo:

  1. Un `HTTPException.detail` largo (>123 bytes UTF-8) hacía FALLAR el
     ``ws.close`` (RFC 6455 limita el reason a 123 bytes) y el
     ``contextlib.suppress`` se lo tragaba → el navegador veía un 1006 mudo
     en vez del 1008 con diagnóstico. `_reject` ahora manda un frame
     ``error`` ANTES del close y recorta el reason.
  2. Un 200 del STT con forma inesperada (p.ej. ``{"error": ...}``) se
     convertía en transcript vacío = «no te he oído», indistinguible de un
     STT roto. `_extract_text` ahora falla con SttResponseError.
  3. El multipart al STT iba con filename ``audio`` SIN extensión — los
     backends OpenAI-compatibles estrictos lo rechazan. El filename ahora
     deriva del content_type.
  4. La voz por defecto era ``af_heart`` (inglés US) en un despliegue
     ES-first: el asistente y el córtex hablaban inglés leyendo español.
     Ambos defaults pasan a ``ef_dora`` (ES) y siguen siendo overridables
     por entorno.
"""

from __future__ import annotations

from typing import Any

import pytest
from api_server.assistant.voice_clients import (
    SttResponseError,
    _extract_text,
    _stt_filename,
)
from api_server.routers.assistant_voice import _SUPPORTED_VOICES, _clip_close_reason, _reject

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 1. Cierre WS con diagnóstico (reason ≤123 bytes + frame error previo)
# ---------------------------------------------------------------------------
def test_clip_close_reason_caps_at_123_utf8_bytes() -> None:
    long_reason = "detalle de error del proveedor LLM " * 10  # >123 bytes
    clipped = _clip_close_reason(long_reason)
    assert len(clipped.encode("utf-8")) <= 123
    assert long_reason.startswith(clipped)


def test_clip_close_reason_never_splits_multibyte_chars() -> None:
    # 'ñ' son 2 bytes en UTF-8: el recorte no puede partir uno por la mitad.
    reason = "ñ" * 200
    clipped = _clip_close_reason(reason)
    raw = clipped.encode("utf-8")
    assert len(raw) <= 123
    assert raw.decode("utf-8") == clipped  # decodifica limpio


def test_clip_close_reason_short_reason_untouched() -> None:
    assert _clip_close_reason("unauthenticated") == "unauthenticated"


class _FakeWs:
    """WS mínimo: registra frames y simula el fallo RFC 6455 con reason largo."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed: tuple[int, str] | None = None

    async def send_json(self, payload: dict[str, Any]) -> None:
        self.sent.append(payload)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        if len(reason.encode("utf-8")) > 123:
            raise RuntimeError("close reason too long (RFC 6455)")
        self.closed = (code, reason)


@pytest.mark.asyncio
async def test_reject_sends_error_frame_and_truncated_close() -> None:
    ws = _FakeWs()
    detail = (
        "No hay un proveedor LLM utilizable para el asistente de este tenant: "
        "el proveedor configurado (claude_sdk) requiere el Claude Agent SDK y "
        "esta imagen del api-server se construyó sin él."
    )
    await _reject(ws, detail)  # type: ignore[arg-type]
    # El diagnóstico completo viaja en un frame ANTES del cierre…
    assert ws.sent and ws.sent[0]["type"] == "error"
    assert ws.sent[0]["detail"] == detail
    # …y el close SÍ se emite (código policy 1008) con el reason recortado.
    assert ws.closed is not None
    code, reason = ws.closed
    assert code == 1008
    assert len(reason.encode("utf-8")) <= 123


# ---------------------------------------------------------------------------
# 2. STT honesto: una respuesta 200 sin `text` es un ERROR, no silencio
# ---------------------------------------------------------------------------
def test_extract_text_accepts_openai_shape() -> None:
    assert _extract_text({"text": "hola"}) == "hola"


def test_extract_text_accepts_plain_string_body() -> None:
    assert _extract_text("hola") == "hola"


def test_extract_text_empty_text_is_silence_not_error() -> None:
    # text="" es una respuesta VÁLIDA (silencio real) — no debe lanzar.
    assert _extract_text({"text": ""}) == ""


def test_extract_text_raises_on_unexpected_shape() -> None:
    with pytest.raises(SttResponseError):
        _extract_text({"error": "model not loaded"})
    with pytest.raises(SttResponseError):
        _extract_text(None)
    with pytest.raises(SttResponseError):
        _extract_text(["segmentos", "sueltos"])


# ---------------------------------------------------------------------------
# 3. Filename multipart con extensión derivada del content_type
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        ("audio/webm", "audio.webm"),
        ("audio/webm;codecs=opus", "audio.webm"),
        ("audio/wav", "audio.wav"),
        ("audio/x-wav", "audio.wav"),
        ("audio/ogg", "audio.ogg"),
        ("audio/mpeg", "audio.mp3"),
        ("audio/mp4", "audio.m4a"),
        ("application/octet-stream", "audio.bin"),
    ],
)
def test_stt_filename_derives_extension(content_type: str, expected: str) -> None:
    assert _stt_filename(content_type) == expected


# ---------------------------------------------------------------------------
# 4. Defaults de voz en español (ES-first) y dentro del allowlist
# ---------------------------------------------------------------------------
def test_default_voices_are_spanish_and_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    from api_server.config import Settings

    settings = Settings(jwt_secret="x")
    assert settings.assistant_tts_default_voice == "ef_dora"
    assert settings.cortex_tts_default_voice == "ef_dora"
    assert settings.assistant_tts_default_voice in _SUPPORTED_VOICES
    assert settings.cortex_tts_default_voice in _SUPPORTED_VOICES
