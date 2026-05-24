"""Unit tests for the OllamaProvider (local + cloud)."""

from __future__ import annotations

import json

import httpx
import pytest
from shared_llm.providers import OllamaProvider
from shared_llm.types import Message


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
