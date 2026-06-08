"""Unit tests for the catalog-id → provider-API model-name transform (ADR 0053).

The price catalog keys models the LiteLLM way (``ollama/llama3.1``,
``azure/gpt-4o``); the provider API wants the bare name. ``to_provider_model_name``
strips a leading ``<family>/`` matching the provider kind and leaves
already-bare names untouched.
"""

from __future__ import annotations

import pytest
from api_server.assistant.model_config import to_provider_model_name

pytestmark = pytest.mark.unit


def test_ollama_prefix_is_stripped() -> None:
    assert to_provider_model_name("ollama", "ollama/llama3.1") == "llama3.1"


def test_azure_family_prefix_is_stripped() -> None:
    assert to_provider_model_name("azure_foundry", "azure/gpt-4o") == "gpt-4o"


def test_bare_name_passes_through() -> None:
    # No family prefix → unchanged (e.g. a real Ollama Cloud model id).
    assert to_provider_model_name("ollama", "gemma3:4b") == "gemma3:4b"
    assert to_provider_model_name("claude_sdk", "claude-sonnet-4-5") == "claude-sonnet-4-5"


def test_unknown_kind_passes_through() -> None:
    assert to_provider_model_name("nope", "ollama/llama3.1") == "ollama/llama3.1"


def test_only_matching_family_prefix_is_stripped() -> None:
    # A leading segment that is NOT one of the kind's families is left alone.
    assert to_provider_model_name("ollama", "anthropic/claude") == "anthropic/claude"
