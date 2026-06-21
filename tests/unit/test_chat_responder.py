"""Unit tests for the project-chat responder helpers (Plan 04 wiring)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from api_server.chat.responder import history_from_messages


def _msg(author_kind: str, content: str) -> Any:
    return SimpleNamespace(author_kind=author_kind, content=content)


def test_history_maps_author_kind_to_llm_role() -> None:
    out = history_from_messages(
        [
            _msg("user", "hola"),
            _msg("agent", "hola, equipo"),
            _msg("system", "modo cambiado"),
            _msg("weird", "x"),  # unknown kind → user (safe default)
        ]
    )
    assert out == [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "hola, equipo"},
        {"role": "system", "content": "modo cambiado"},
        {"role": "user", "content": "x"},
    ]


def test_history_empty() -> None:
    assert history_from_messages([]) == []
