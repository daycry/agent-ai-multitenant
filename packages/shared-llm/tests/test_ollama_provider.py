"""Unit tests for the OllamaProvider (local + cloud)."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from shared_llm.exceptions import AuthError, ProviderError
from shared_llm.providers import OllamaProvider
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
    transport = httpx.MockTransport(handler)
    return httpx.AsyncClient(
        transport=transport,
        headers={"Content-Type": "application/json"},
        timeout=5.0,
    )


@pytest.mark.asyncio
async def test_local_factory_builds_localhost_provider_without_auth() -> None:
    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "model": "llama3.1",
                "choices": [{"message": {"role": "assistant", "content": "hola"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        )

    p = OllamaProvider.local(http_client=_mock_client(handler))
    resp = await p.complete([Message(role="user", content="hi")])
    assert resp.content == "hola"
    assert resp.provider == "ollama"
    # Local should not send an Authorization header.
    assert captured["auth"] is None
    assert "localhost:11434/v1/chat/completions" in str(captured["url"])


@pytest.mark.asyncio
async def test_cloud_factory_sends_bearer_token() -> None:
    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["url"] = str(req.url)
        captured["auth"] = req.headers.get("Authorization")
        return httpx.Response(
            200,
            json={
                "model": "gpt-oss:120b",
                "choices": [{"message": {"role": "assistant", "content": "cloud!"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2},
            },
        )

    p = OllamaProvider.cloud(api_key="sk-test", http_client=_mock_client(handler))
    resp = await p.complete([Message(role="user", content="hi")])
    assert resp.content == "cloud!"
    # Cloud must send the Bearer token from the api_key.
    assert captured["auth"] == "Bearer sk-test"
    assert "ollama.com/v1/chat/completions" in str(captured["url"])


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

    p = OllamaProvider.local(http_client=_mock_client(handler))
    chunks = []
    async for c in p.stream([Message(role="user", content="hi")]):
        chunks.append(c)
    assert "".join(c.delta for c in chunks if not c.done) == "hello"
    assert chunks[-1].done is True


@pytest.mark.asyncio
async def test_stream_midstream_error_becomes_provider_error() -> None:
    """A connection drop while iterating the SSE body is converted to a
    typed ProviderError instead of leaking a raw httpx error."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=_RaisingStream(
                good=[b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'],
                exc=httpx.ReadError("connection reset"),
            ),
        )

    p = OllamaProvider.local(http_client=_mock_client(handler))
    deltas: list[str] = []
    with pytest.raises(ProviderError, match="stream interrupted"):
        async for c in p.stream([Message(role="user", content="hi")]):
            if c.delta:
                deltas.append(c.delta)
    assert deltas == ["hi"]


@pytest.mark.asyncio
async def test_stream_403_is_provider_error_not_auth_error() -> None:
    """A 403 on the stream call maps to ProviderError (permission), not
    AuthError (which is reserved for 401 / re-auth)."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="forbidden")

    p = OllamaProvider.cloud(api_key="sk-x", http_client=_mock_client(handler))
    with pytest.raises(ProviderError) as info:
        async for _c in p.stream([Message(role="user", content="hi")]):
            pass
    assert not isinstance(info.value, AuthError)
    assert info.value.status_code == 403


@pytest.mark.asyncio
async def test_stream_401_is_auth_error() -> None:
    """A 401 on the stream call maps to AuthError."""

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="bad key")

    p = OllamaProvider.cloud(api_key="sk-x", http_client=_mock_client(handler))
    with pytest.raises(AuthError):
        async for _c in p.stream([Message(role="user", content="hi")]):
            pass


@pytest.mark.asyncio
async def test_complete_passes_tools_when_provided() -> None:
    captured: dict[str, object] = {}

    def handler(req: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "model": "llama3.1",
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {},
            },
        )

    p = OllamaProvider.local(http_client=_mock_client(handler))
    tools = [{"type": "function", "function": {"name": "echo"}}]
    await p.complete([Message(role="user", content="hi")], tools=tools)
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["tools"] == tools


@pytest.mark.asyncio
async def test_owned_client_is_fresh_per_call() -> None:
    """Regression: an OWNED client must be created per call (bound to the current
    loop), not cached. A single cached client breaks when the provider is used across
    event loops (planning bridge calls complete() via asyncio.run repeatedly)."""
    p = OllamaProvider.cloud(api_key="sk-x")  # owned (no injected http_client)
    async with p._acquire() as c1:
        first = c1
    async with p._acquire() as c2:
        second = c2
    assert first is not second  # distinct per call → safe across loops
    assert first.is_closed and second.is_closed  # each closed on context exit
