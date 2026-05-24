"""Unit tests for the AzureFoundryAPIMProvider."""

from __future__ import annotations

import httpx
import pytest
from shared_llm.providers import AzureFoundryAPIMProvider
from shared_llm.types import Message


def _mock_client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Content-Type": "application/json"},
        timeout=5.0,
    )


def _ok_response(**overrides) -> httpx.Response:  # type: ignore[no-untyped-def]
    body = {
        "model": "gpt-4o-foundry",
        "choices": [{"message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2},
    }
    body.update(overrides)
    return httpx.Response(200, json=body)


def test_constructor_requires_some_auth() -> None:
    with pytest.raises(ValueError, match="subscription_key or bearer_token"):
        AzureFoundryAPIMProvider(
            apim_base_url="https://x.azure-api.net/foundry",
            deployment="gpt-4o",
        )


@pytest.mark.asyncio
async def test_complete_targets_the_apim_url_with_subscription_key() -> None:
    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["sub"] = req.headers.get("Ocp-Apim-Subscription-Key")
        captured["auth"] = req.headers.get("Authorization")
        return _ok_response()

    p = AzureFoundryAPIMProvider(
        apim_base_url="https://x.azure-api.net/foundry",
        deployment="gpt-4o",
        subscription_key="sub-123",
        http_client=_mock_client(handler),
    )
    resp = await p.complete([Message(role="user", content="hi")])
    assert resp.provider == "azure_foundry_apim"

    url = str(captured["url"])
    assert "x.azure-api.net/foundry/openai/deployments/gpt-4o/chat/completions" in url
    assert "api-version=2024-10-21" in url
    assert captured["sub"] == "sub-123"
    # When only subscription_key is set, no Bearer is sent.
    assert captured["auth"] is None


@pytest.mark.asyncio
async def test_bearer_token_takes_precedence_when_both_given() -> None:
    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["sub"] = req.headers.get("Ocp-Apim-Subscription-Key")
        captured["auth"] = req.headers.get("Authorization")
        return _ok_response()

    p = AzureFoundryAPIMProvider(
        apim_base_url="https://x.azure-api.net/foundry",
        deployment="gpt-4o",
        subscription_key="sub-123",
        bearer_token="bearer-456",
        http_client=_mock_client(handler),
    )
    await p.complete([Message(role="user", content="hi")])
    # Both headers are sent — APIM may use either depending on policy.
    assert captured["sub"] == "sub-123"
    assert captured["auth"] == "Bearer bearer-456"


@pytest.mark.asyncio
async def test_apim_cost_is_propagated_to_usage() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return _ok_response(usage={"prompt_tokens": 10, "completion_tokens": 20, "cost": 0.0123})

    p = AzureFoundryAPIMProvider(
        apim_base_url="https://x.azure-api.net/foundry",
        deployment="gpt-4o",
        subscription_key="k",
        http_client=_mock_client(handler),
    )
    resp = await p.complete([Message(role="user", content="hi")])
    assert resp.usage.cost_usd == 0.0123
