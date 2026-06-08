"""Unit tests for the api-server provider factory (ADR 0053).

``build_provider_from_kind`` is the pure mapping ``kind + resolved config +
model -> concrete shared_llm provider``. The deterministic, no-optional-dep
cases are tested here:

  * the happy path for the httpx-backed kinds (ollama, azure_foundry) — httpx
    is a hard dep of shared_llm, so these construct without an optional SDK;
  * the "not configured" guards (missing endpoint / credential) that must
    return ``None`` BEFORE any import, so they need no optional dep;
  * an unknown kind returns ``None``.

The Claude/Copilot happy paths depend on optional SDKs and are exercised in
real deployments; here we only assert their missing-credential guards.

The DB-backed ``build_llm_provider`` (row read + Vault resolve) is covered by
the integration suite.
"""

from __future__ import annotations

import pytest
from api_server.llm_providers.factory import build_provider_from_kind

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# ollama — base_url + model land on the client; bearer token becomes api_key
# ---------------------------------------------------------------------------
def test_ollama_builds_with_base_url_and_model() -> None:
    provider = build_provider_from_kind(
        "ollama",
        base_url="http://ollama.internal:11434/v1",
        secret={},
        model="llama3.1:70b",
    )
    assert provider is not None
    assert type(provider).__name__ == "OllamaProvider"
    assert provider.default_model == "llama3.1:70b"
    assert provider.base_url == "http://ollama.internal:11434/v1"


def test_ollama_bearer_token_becomes_api_key() -> None:
    provider = build_provider_from_kind(
        "ollama",
        base_url=None,
        secret={"bearer_token": "tok-123"},
        model="llama3.1",
    )
    assert provider is not None
    # OllamaProvider stores the bearer as its private api key.
    assert provider._api_key == "tok-123"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# azure_foundry — the model becomes the deployment (URL pins it)
# ---------------------------------------------------------------------------
def test_azure_foundry_builds_with_deployment_from_model() -> None:
    provider = build_provider_from_kind(
        "azure_foundry",
        base_url="https://apim.example.com",
        secret={"api_key": "sub-key"},
        model="gpt-4o-2024-08-06",
    )
    assert provider is not None
    assert type(provider).__name__ == "AzureFoundryAPIMProvider"
    # The selected model is the deployment (the per-call model is ignored for
    # routing — the APIM URL pins it).
    assert provider.deployment == "gpt-4o-2024-08-06"


def test_azure_foundry_without_base_url_is_none() -> None:
    assert (
        build_provider_from_kind(
            "azure_foundry", base_url=None, secret={"api_key": "k"}, model="gpt-4o"
        )
        is None
    )


def test_azure_foundry_without_credential_is_none() -> None:
    assert (
        build_provider_from_kind(
            "azure_foundry", base_url="https://apim.example.com", secret={}, model="gpt-4o"
        )
        is None
    )


# ---------------------------------------------------------------------------
# copilot — missing OAuth token short-circuits to None before any import
# ---------------------------------------------------------------------------
def test_copilot_without_token_is_none() -> None:
    assert build_provider_from_kind("copilot", base_url=None, secret={}, model="gpt-4o") is None


# ---------------------------------------------------------------------------
# unknown / unsupported kind
# ---------------------------------------------------------------------------
def test_unknown_kind_is_none() -> None:
    assert build_provider_from_kind("litellm", base_url=None, secret={}, model="x") is None
