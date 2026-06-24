"""Server-side allowlist for the assistant voice id (auditoría zona 'voz', hallazgo security).

Regression: the WS ``config`` frame's ``voice`` was forwarded verbatim to the
internal Kokoro TTS (``state.voice = chosen``) with no validation — despite the
UI/ADR claiming the server validates it. An arbitrary voice could be injected
into the internal media service. The server must accept only the supported voices.
"""

from __future__ import annotations

from api_server.routers.assistant_voice import _SUPPORTED_VOICES, _resolve_voice

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
