"""La temperatura sólo viaja si alguien la configuró (ADR 0169 D3, `task_cm_04`).

Los tres proveedores HTTP tenían ``temperature: float = 0.7`` en la firma y la metían
en el cuerpo siempre. GPT-5 y la serie o sólo aceptan la temperatura por defecto, así
que ese 0.7 que nadie pidió era un 400 en cada llamada. Sin valor explícito, la clave
no se manda y cada servidor aplica el suyo.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
from shared_llm.providers import AzureFoundryAPIMProvider, CopilotProvider, OllamaProvider
from shared_llm.types import Message

_OK = {
    "model": "m",
    "choices": [{"message": {"role": "assistant", "content": "hola"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}
_SSE = b'data: {"choices":[{"delta":{"content":"hola"}}]}\n\ndata: [DONE]\n\n'


def _recorder(bodies: list[dict[str, Any]], *, stream: bool = False) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/copilot_internal/v2/token"):
            return httpx.Response(200, json={"token": "jwt", "expires_at": time.time() + 1500})
        bodies.append(json.loads(req.content))
        if stream:
            return httpx.Response(200, content=_SSE)
        return httpx.Response(200, json=_OK)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _providers(client: httpx.AsyncClient) -> list[Any]:
    return [
        OllamaProvider(base_url="http://ollama/v1", http_client=client, default_model="m"),
        AzureFoundryAPIMProvider(
            apim_base_url="https://apim", deployment="d", subscription_key="k", http_client=client
        ),
        CopilotProvider(github_token="gho", http_client=client),
    ]


_MSGS = [Message(role="user", content="hola")]


@pytest.mark.asyncio
async def test_complete_omits_temperature_when_not_set() -> None:
    bodies: list[dict[str, Any]] = []
    for provider in _providers(_recorder(bodies)):
        await provider.complete(_MSGS, model="m")
    assert len(bodies) == 3
    assert all("temperature" not in body for body in bodies), bodies


@pytest.mark.asyncio
async def test_complete_sends_temperature_when_set() -> None:
    bodies: list[dict[str, Any]] = []
    for provider in _providers(_recorder(bodies)):
        await provider.complete(_MSGS, model="m", temperature=0.2)
    assert [body["temperature"] for body in bodies] == [0.2, 0.2, 0.2]


@pytest.mark.asyncio
async def test_stream_omits_temperature_when_not_set() -> None:
    bodies: list[dict[str, Any]] = []
    for provider in _providers(_recorder(bodies, stream=True)):
        async for _chunk in provider.stream(_MSGS, model="m"):
            pass
    assert len(bodies) == 3
    assert all("temperature" not in body for body in bodies), bodies
