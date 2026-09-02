"""La guía de ejecución sigue a las tools EFECTIVAS del agente, no al texto que
se horneó al sembrar (`task_cv_33`, auditoría 2026-09-01 F-03).

La guía («tienes stack_exec y shell_exec», «sólo shell», …) se concatenaba al
`system_prompt` al sembrar; las copias adoptadas la heredaban congelada, y una
migración que cambiaba las tools de un agente no tocaba el texto: el agente leía
que tenía `stack_exec` cuando ya no lo tenía, o al revés. Ahora el dispatch
resuelve la persona con las tools efectivas: retira cualquier guía horneada y
añade la que corresponde a lo que el run puede llamar de verdad.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from api_server.agent_persona import resolve_agent_persona
from api_server.seeds.tool_usage_guidance import (
    BOTH_DOORS_EN,
    SHELL_ONLY_ES,
    STACK_ONLY_ES,
    strip_execution_guidance,
    with_execution_guidance,
)

pytestmark = pytest.mark.unit


def test_strip_removes_any_baked_guidance_and_keeps_the_rest() -> None:
    prompt = "Eres el backend dev." + SHELL_ONLY_ES + "\n\nESTRUCTURA: raíz del workspace."
    stripped = strip_execution_guidance(prompt)
    assert "TU SANDBOX NO LLEVA" not in stripped
    assert stripped.startswith("Eres el backend dev.")
    assert "ESTRUCTURA: raíz del workspace." in stripped


def test_with_guidance_replaces_the_baked_one_by_the_effective_one() -> None:
    prompt = "Eres el backend dev." + SHELL_ONLY_ES
    out = with_execution_guidance(prompt, ["stack-exec"], "es")
    assert out.endswith(STACK_ONLY_ES)
    assert "TU SANDBOX NO LLEVA" not in out


def test_tool_names_with_underscore_are_accepted() -> None:
    """El dispatch resuelve NOMBRES (`stack_exec`); la guía se indexa por slug."""
    out = with_execution_guidance("Persona.", ["stack_exec", "shell_exec"], "en")
    assert out.endswith(BOTH_DOORS_EN)


def test_without_any_door_no_guidance_is_appended() -> None:
    out = with_execution_guidance("Persona." + SHELL_ONLY_ES, ["read_file"], "es")
    assert out == "Persona."


def _agent(prompt: str) -> SimpleNamespace:
    return SimpleNamespace(
        model_config={"system_prompts": {"es": prompt}}, system_prompt="", role="backend_dev"
    )


def test_the_persona_carries_the_guidance_of_the_effective_tools() -> None:
    persona = resolve_agent_persona(_agent("Eres X." + SHELL_ONLY_ES), tool_slugs=["stack-exec"])
    assert persona is not None
    assert persona["prompt"].endswith(STACK_ONLY_ES)
    assert "TU SANDBOX NO LLEVA" not in persona["prompt"]


def test_without_tool_slugs_the_persona_is_untouched() -> None:
    persona = resolve_agent_persona(_agent("Eres X." + SHELL_ONLY_ES))
    assert persona is not None and persona["prompt"] == "Eres X." + SHELL_ONLY_ES
