"""Unit tests for the assistant memory system-prompt augmentation (ADR 0054).

``augment_system_prompt`` is the pure piece: it folds the user's known facts
(+ a write hint when the remember tool is enabled) into the system prompt.
"""

from __future__ import annotations

import pytest
from api_server.assistant.memory import augment_system_prompt

pytestmark = pytest.mark.unit


def test_no_facts_no_tool_returns_base_unchanged() -> None:
    assert augment_system_prompt("BASE", known_facts=[], remember_enabled=False) == "BASE"


def test_known_facts_render_as_a_section() -> None:
    out = augment_system_prompt(
        "BASE",
        known_facts=["Se llama Jose", "Prefiere respuestas concisas"],
        remember_enabled=False,
    )
    assert out.startswith("BASE")
    assert "Lo que sé de ti" in out
    assert "- Se llama Jose" in out
    assert "- Prefiere respuestas concisas" in out


def test_write_hint_present_only_when_tool_enabled() -> None:
    with_tool = augment_system_prompt("BASE", known_facts=[], remember_enabled=True)
    without_tool = augment_system_prompt("BASE", known_facts=[], remember_enabled=False)
    assert "remember_about_me" in with_tool
    assert "remember_about_me" not in without_tool
