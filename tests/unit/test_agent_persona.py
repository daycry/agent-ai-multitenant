"""P0-1 (investigación 2026-07-11): la persona del agente viaja al run.

`resolve_agent_persona` es la resolución server-side de la persona efectiva de
un agente — la MISMA precedencia que el frontend (`lib/persona/persona.ts::
resolvePromptSource`): `model_config.system_prompts.es` → `.en` → campo plano
`system_prompt`. Devuelve el dict `agent_persona` que el orquestador emite en el
payload del run (o `None` si el agente no tiene persona con contenido).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from api_server.agent_persona import PERSONA_MAX_CHARS, resolve_agent_persona

pytestmark = pytest.mark.unit


def _agent(**kwargs: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "name": "ci4-backend",
        "role": "backend_dev",
        "system_prompt": "flat legacy prompt",
        "model_config": {},
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def test_prefers_spanish_bilingual_prompt() -> None:
    agent = _agent(
        model_config={"system_prompts": {"es": "Eres el backend CI4.", "en": "You are..."}}
    )
    persona = resolve_agent_persona(agent)
    assert persona is not None
    assert persona["prompt"] == "Eres el backend CI4."


def test_falls_back_to_english_then_flat() -> None:
    only_en = _agent(model_config={"system_prompts": {"en": "You are the CI4 backend."}})
    assert resolve_agent_persona(only_en)["prompt"] == "You are the CI4 backend."

    flat_only = _agent(model_config={}, system_prompt="  persona plana  ")
    assert resolve_agent_persona(flat_only)["prompt"] == "persona plana"


def test_blank_everything_yields_none() -> None:
    agent = _agent(model_config={"system_prompts": {"es": "   "}}, system_prompt="   ")
    assert resolve_agent_persona(agent) is None


def test_includes_role_and_name() -> None:
    persona = resolve_agent_persona(_agent())
    assert persona["role"] == "backend_dev"
    assert persona["name"] == "ci4-backend"


def test_long_prompt_is_capped_with_marker() -> None:
    agent = _agent(system_prompt="x" * (PERSONA_MAX_CHARS + 500), model_config={})
    persona = resolve_agent_persona(agent)
    assert len(persona["prompt"]) <= PERSONA_MAX_CHARS + 100  # marker allowance
    assert "truncated" in persona["prompt"]


def test_tolerates_malformed_model_config() -> None:
    # JSONB permite cualquier cosa: system_prompts como string/None no rompe.
    agent = _agent(model_config={"system_prompts": "no soy un dict"})
    assert resolve_agent_persona(agent)["prompt"] == "flat legacy prompt"
    agent2 = _agent(model_config=None)
    assert resolve_agent_persona(agent2)["prompt"] == "flat legacy prompt"
