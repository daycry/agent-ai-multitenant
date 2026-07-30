"""Server-side allowlist for the assistant voice id (auditoría zona 'voz', hallazgo security).

Regression: the WS ``config`` frame's ``voice`` was forwarded verbatim to the
internal Kokoro TTS (``state.voice = chosen``) with no validation — despite the
UI/ADR claiming the server validates it. An arbitrary voice could be injected
into the internal media service. The server must accept only the supported voices.
"""

from __future__ import annotations

from api_server.routers.assistant_voice import (
    _SUPPORTED_VOICES,
    _resolve_voice,
    voice_language,
    voice_language_instruction,
)

_DEFAULT = "af_heart"


def test_supported_voice_is_accepted() -> None:
    assert _resolve_voice("em_alex", _DEFAULT) == "em_alex"


def test_unsupported_voice_falls_back_to_current() -> None:
    assert _resolve_voice("evil_voice; rm -rf", _DEFAULT) == _DEFAULT


def test_empty_keeps_current() -> None:
    assert _resolve_voice("", _DEFAULT) == _DEFAULT


def test_allowlist_is_the_six_ui_voices() -> None:
    assert (
        frozenset({"af_heart", "am_michael", "bf_emma", "bm_george", "ef_dora", "em_alex"})
        == _SUPPORTED_VOICES
    )


def test_spanish_voices_map_to_es() -> None:
    """Las voces con prefijo ``e`` (ef_/em_) son españolas."""
    assert voice_language("ef_dora") == "es"
    assert voice_language("em_alex") == "es"


def test_english_voices_map_to_en() -> None:
    """Las voces con prefijo ``a`` (US) o ``b`` (UK) son inglesas."""
    for v in ("af_heart", "am_michael", "bf_emma", "bm_george"):
        assert voice_language(v) == "en"


def test_unknown_voice_defaults_to_spanish() -> None:
    """Despliegue ES-first: ante una voz desconocida, español por defecto."""
    assert voice_language("zz_nada") == "es"


def test_spanish_instruction_forbids_english_and_english_forbids_spanish() -> None:
    """La instrucción es imperativa y explícita para el idioma de la voz — el
    operador reportó que con voz española el córtex contestaba en inglés."""
    es = voice_language_instruction("ef_dora")
    assert "español" in es.lower()
    assert "nunca respondas en inglés" in es.lower()
    en = voice_language_instruction("am_michael")
    assert "english" in en.lower()
    assert "español" not in en.lower()
