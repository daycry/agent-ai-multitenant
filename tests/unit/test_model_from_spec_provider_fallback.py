"""model_from_spec honours real-provider intent (ADR 0057 F1).

The critical bug: an agent's model_config is `provider`-keyed (catalog kind)
but the runtime read only `kind` and silently fell back to the scripted client
for every real-provider spec. Pin the hardened behaviour: `provider` is read as
a kind fallback, so a real spec builds the real client (or fails loudly), and
only explicit/bare scripted specs stay scripted.
"""

from __future__ import annotations

import pytest
from agent_runtime.model import ScriptedModelClient, model_from_spec
from agent_runtime.providers import OllamaModelClient

pytestmark = pytest.mark.unit


def test_provider_keyed_spec_builds_the_real_client_not_scripted() -> None:
    """The exact dispatch shape (ADR 0055 model_config) must NOT be scripted."""
    client = model_from_spec(
        {
            "provider": "ollama",
            "model": "qwen3-coder:480b",
            "temperature": 0.2,
            "base_url": "http://ollama:11434/v1",
        }
    )
    assert isinstance(client, OllamaModelClient)
    assert not isinstance(client, ScriptedModelClient)


def test_explicit_scripted_kind_stays_scripted() -> None:
    client = model_from_spec(
        {"kind": "scripted", "decisions": [{"kind": "finish", "output": "ok"}]}
    )
    assert isinstance(client, ScriptedModelClient)


def test_bare_empty_spec_stays_scripted() -> None:
    """`{}` keeps the historical scripted default (legacy/test compat)."""
    assert isinstance(model_from_spec({}), ScriptedModelClient)


def test_kind_wins_over_provider_when_both_present() -> None:
    client = model_from_spec(
        {"kind": "scripted", "provider": "ollama", "decisions": [], "reviews": []}
    )
    assert isinstance(client, ScriptedModelClient)


def test_unknown_provider_kind_fails_loudly() -> None:
    with pytest.raises(ValueError, match="unknown provider kind"):
        model_from_spec({"provider": "nonexistent-kind", "model": "m"})
