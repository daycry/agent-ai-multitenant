"""Unit tests for the AzureFoundryAPIMProvider."""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx
import pytest
from shared_llm.exceptions import AuthError, ProviderError
from shared_llm.providers import AzureFoundryAPIMProvider
from shared_llm.types import Message


class _RaisingStream(httpx.AsyncByteStream):
    """SSE body that yields good lines then raises mid-stream."""

    def __init__(self, *, good: list[bytes], exc: BaseException) -> None:
        self._good = good
        self._exc = exc

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._good:
            yield chunk
        raise self._exc

    async def aclose(self) -> None:
        return None


def _mock_client(handler) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    return httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        headers={"Content-Type": "application/json"},
        timeout=5.0,
    )


def _azure(handler) -> AzureFoundryAPIMProvider:  # type: ignore[no-untyped-def]
    return AzureFoundryAPIMProvider(
        apim_base_url="https://x.azure-api.net/foundry",
        deployment="gpt-4o",
        subscription_key="sub-123",
        http_client=_mock_client(handler),
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


@pytest.mark.asyncio
async def test_stream_concatenates_deltas_until_done() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        body = b"".join(
            [
                b'data: {"choices":[{"delta":{"content":"he"}}]}\n\n',
                b'data: {"choices":[{"delta":{"content":"llo"}}]}\n\n',
                b"data: [DONE]\n\n",
            ]
        )
        return httpx.Response(200, content=body, headers={"Content-Type": "text/event-stream"})

    p = _azure(handler)
    chunks = [c async for c in p.stream([Message(role="user", content="hi")])]
    assert "".join(c.delta for c in chunks if not c.done) == "hello"
    assert chunks[-1].done is True


@pytest.mark.asyncio
async def test_stream_midstream_error_becomes_provider_error() -> None:
    """A connection drop mid-body is converted to a typed ProviderError
    instead of leaking a raw httpx error to the caller."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=_RaisingStream(
                good=[b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'],
                exc=httpx.ReadTimeout("timed out"),
            ),
        )

    p = _azure(handler)
    with pytest.raises(ProviderError, match="stream interrupted"):
        async for _c in p.stream([Message(role="user", content="hi")]):
            pass


@pytest.mark.asyncio
async def test_stream_401_is_auth_error_403_is_provider_error() -> None:
    """401 → AuthError (re-auth), 403 → ProviderError (no permission)."""

    def handler_401(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad token")

    with pytest.raises(AuthError):
        async for _c in _azure(handler_401).stream([Message(role="user", content="hi")]):
            pass

    def handler_403(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    with pytest.raises(ProviderError) as info:
        async for _c in _azure(handler_403).stream([Message(role="user", content="hi")]):
            pass
    assert not isinstance(info.value, AuthError)
    assert info.value.status_code == 403
