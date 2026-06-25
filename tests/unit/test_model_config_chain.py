"""Ola A: herencia del modelo plataforma → proyecto → equipo → agente.

`resolve_model_config_chain` elige el primer nivel que PINEA provider+model
(de más específico a menos): agente → equipo → proyecto → plataforma. Las claves
no-modelo del agente (p.ej. system_prompts) se preservan. Un nivel que solo
pinea parcialmente (provider sin model) NO cuenta y se baja al siguiente."""

from __future__ import annotations

import pytest
from api_server.db.platform_settings import (
    config_needs_default_model,
    resolve_model_config_chain,
    resolve_model_config_origin,
)

pytestmark = pytest.mark.unit

_PLATFORM = {"provider": "ollama", "model": "llama3.2:1b", "temperature": 0.2}
_PINNED = {"provider": "claude_sdk", "model": "claude-sonnet-4-5"}
_PID = "019e83cd-bb5c-7f43-9031-b3a75f3bdd29"


# ---------------------------------------------------------------------------
# ADR 0082 — un config pineado por provider_id concreto cuenta como pin y se
# propaga por la cadena (sin necesitar provider/kind alongside).
# ---------------------------------------------------------------------------
def test_config_needs_default_recognizes_provider_id_pin() -> None:
    assert config_needs_default_model({"provider_id": _PID, "model": "gpt-oss:120b"}) is False
    # provider_id sin model NO es un pin → sigue heredando.
    assert config_needs_default_model({"provider_id": _PID}) is True
    # legacy por kind sigue contando como pin.
    assert config_needs_default_model({"provider": "ollama", "model": "x"}) is False


def test_agent_pinned_by_provider_id_only_is_returned_verbatim() -> None:
    agent = {"provider_id": _PID, "model": "gpt-oss:120b"}
    out = resolve_model_config_chain(agent, {}, {}, _PLATFORM)
    assert out == agent  # no hereda el default por kind


def test_platform_default_provider_id_propagates_to_empty_agent() -> None:
    default = {
        "provider": "ollama",
        "provider_id": _PID,
        "model": "gpt-oss:120b",
        "temperature": 0.1,
    }
    out = resolve_model_config_chain({"system_prompts": {"es": "hola"}}, {}, {}, default)
    assert out["provider_id"] == _PID
    assert out["model"] == "gpt-oss:120b"
    assert out["system_prompts"] == {"es": "hola"}


def test_agent_pin_wins_over_all() -> None:
    out = resolve_model_config_chain(
        {"provider": "claude_sdk", "model": "claude-sonnet-4-5"},
        {"provider": "copilot", "model": "gpt"},
        {"provider": "azure_foundry", "model": "x"},
        _PLATFORM,
    )
    assert out["provider"] == "claude_sdk" and out["model"] == "claude-sonnet-4-5"


def test_team_wins_when_agent_unpinned_and_prompts_preserved() -> None:
    out = resolve_model_config_chain(
        {"system_prompts": {"es": "hola"}},
        {"provider": "claude_sdk", "model": "claude-sonnet-4-5"},
        {},
        _PLATFORM,
    )
    assert out["provider"] == "claude_sdk"
    assert out["model"] == "claude-sonnet-4-5"
    assert out["system_prompts"] == {"es": "hola"}


def test_project_wins_when_agent_and_team_unpinned() -> None:
    out = resolve_model_config_chain({}, {}, {"provider": "copilot", "model": "gpt"}, _PLATFORM)
    assert out["provider"] == "copilot" and out["model"] == "gpt"


def test_platform_default_when_all_unpinned() -> None:
    out = resolve_model_config_chain({}, {}, {}, _PLATFORM)
    assert out["provider"] == "ollama" and out["model"] == "llama3.2:1b"


def test_partial_level_is_ignored() -> None:
    # El equipo solo pinea provider (sin model) → no cuenta; baja a plataforma.
    out = resolve_model_config_chain({}, {"provider": "claude_sdk"}, {}, _PLATFORM)
    assert out["provider"] == "ollama"


def test_scripted_kind_agent_passes_through() -> None:
    # Un spec con `kind` (scripted de tests) no necesita default → intacto.
    out = resolve_model_config_chain({"kind": "scripted"}, {}, {}, _PLATFORM)
    assert out == {"kind": "scripted"}


def test_origin_agent_when_agent_pins() -> None:
    assert resolve_model_config_origin(_PINNED, _PINNED, _PINNED) == "agent"


def test_origin_team_when_only_team_pins() -> None:
    assert resolve_model_config_origin({}, _PINNED, {}) == "team"


def test_origin_project_when_only_project_pins() -> None:
    assert resolve_model_config_origin({}, {}, _PINNED) == "project"


def test_origin_platform_when_none_above_pins() -> None:
    assert resolve_model_config_origin({}, {}, {}) == "platform"
    # Un nivel parcial (provider sin model) no cuenta → baja a plataforma.
    assert resolve_model_config_origin({}, {"provider": "claude_sdk"}, {}) == "platform"
