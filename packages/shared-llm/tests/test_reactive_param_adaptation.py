"""Los proveedores se adaptan a los parámetros que el modelo rechaza (ADR 0169 D2).

`task_cm_05` del plan `compat-modelos-2026-09-29`. En vez de listas de modelos por
nombre —que envejecen: GPT-5.4 no lleva «codex» en el nombre y también cambió de
reglas—, el proveedor lee el 400 del propio servidor, corrige el cuerpo, reintenta
UNA vez y memoriza la corrección para ese modelo. Cualquier otro 400 sigue siendo un
error, y un segundo 400 tras adaptar también: no hay bucle.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
from shared_llm.exceptions import ProviderError
from shared_llm.providers import AzureFoundryAPIMProvider, CopilotProvider, OllamaProvider
from shared_llm.providers._adaptive import adapt_body, needs_responses_api
from shared_llm.types import Message

_OK = {
    "model": "m",
    "choices": [{"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
}
_MAX_TOKENS_ERR = {
    "error": {
        "code": "unsupported_parameter",
        "message": "Unsupported parameter: 'max_tokens' is not supported with this model. "
        "Use 'max_completion_tokens' instead.",
        "param": "max_tokens",
    }
}
_TEMPERATURE_ERR = {
    "error": {
        "code": "unsupported_value",
        "message": "Unsupported value: 'temperature' does not support 0.2 with this model. "
        "Only the default (1) value is supported.",
        "param": "temperature",
    }
}
_CAP_ERR = {
    "error": {
        "message": "max_tokens is too large: 8192. This model supports at most 4096 "
        "completion tokens, whereas you provided 8192.",
    }
}
_MSGS = [Message(role="user", content="hola")]


class _Server:
    """Transporte falso: rechaza lo que un modelo de razonamiento rechazaría."""

    def __init__(self, *, reject: dict[str, Any] | None = None, always_400: bool = False) -> None:
        self.bodies: list[dict[str, Any]] = []
        self._reject = reject
        self._always_400 = always_400

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/copilot_internal/v2/token"):
            return httpx.Response(200, json={"token": "jwt", "expires_at": time.time() + 1500})
        body = json.loads(req.content)
        self.bodies.append(body)
        if self._always_400:
            return httpx.Response(400, json={"error": {"message": "invalid role 'foo'"}})
        if self._reject is _MAX_TOKENS_ERR and "max_tokens" in body:
            return httpx.Response(400, json=_MAX_TOKENS_ERR)
        if self._reject is _TEMPERATURE_ERR and "temperature" in body:
            return httpx.Response(400, json=_TEMPERATURE_ERR)
        if self._reject is _CAP_ERR and body.get("max_tokens", 0) > 4096:
            return httpx.Response(400, json=_CAP_ERR)
        return httpx.Response(200, json=_OK)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _azure(server: _Server) -> AzureFoundryAPIMProvider:
    return AzureFoundryAPIMProvider(
        apim_base_url="https://apim",
        deployment="gpt-5",
        subscription_key="k",
        http_client=server.client(),
    )


# --- el helper puro -------------------------------------------------------------
def test_adapt_body_moves_max_tokens_to_max_completion_tokens() -> None:
    fixed = adapt_body({"max_tokens": 8192, "model": "m"}, json.dumps(_MAX_TOKENS_ERR))
    assert fixed == {"max_completion_tokens": 8192, "model": "m"}


def test_adapt_body_drops_an_unsupported_temperature() -> None:
    assert adapt_body({"temperature": 0.2, "model": "m"}, json.dumps(_TEMPERATURE_ERR)) == {
        "model": "m"
    }


def test_adapt_body_lowers_the_cap_to_the_model_maximum() -> None:
    assert adapt_body({"max_tokens": 8192}, json.dumps(_CAP_ERR)) == {"max_tokens": 4096}


def test_adapt_body_returns_none_for_an_unrelated_error() -> None:
    assert adapt_body({"max_tokens": 10, "temperature": 0.2}, "invalid role 'foo'") is None


def test_needs_responses_api_recognises_both_vendors() -> None:
    assert needs_responses_api('{"error":{"code":"unsupported_api_for_model"}}')
    assert needs_responses_api("OperationNotSupported: The chatCompletion operation does not work")
    assert not needs_responses_api("rate limited")


# --- los proveedores ------------------------------------------------------------
@pytest.mark.asyncio
async def test_azure_retries_with_max_completion_tokens_and_remembers() -> None:
    server = _Server(reject=_MAX_TOKENS_ERR)
    provider = _azure(server)
    await provider.complete(_MSGS, max_tokens=8192)
    assert "max_tokens" in server.bodies[0]
    assert server.bodies[1]["max_completion_tokens"] == 8192
    await provider.complete(_MSGS, max_tokens=8192)
    assert len(server.bodies) == 3, "la segunda llamada ya sale adaptada"
    assert "max_tokens" not in server.bodies[2]


@pytest.mark.asyncio
async def test_copilot_drops_temperature_for_the_model_that_rejects_it() -> None:
    server = _Server(reject=_TEMPERATURE_ERR)
    provider = CopilotProvider(github_token="gho", http_client=server.client())
    await provider.complete(_MSGS, model="gpt-5", temperature=0.2)
    assert "temperature" not in server.bodies[-1]
    # Otro modelo del mismo proveedor no hereda la adaptación.
    await provider.complete(_MSGS, model="gpt-4o", temperature=0.2)
    assert "temperature" in server.bodies[-2]


@pytest.mark.asyncio
async def test_ollama_lowers_an_oversized_cap() -> None:
    server = _Server(reject=_CAP_ERR)
    provider = OllamaProvider(
        base_url="http://o/v1", http_client=server.client(), default_model="m"
    )
    await provider.complete(_MSGS, max_tokens=8192)
    assert server.bodies[-1]["max_tokens"] == 4096


@pytest.mark.asyncio
async def test_an_unrelated_400_is_raised_after_one_request() -> None:
    server = _Server(always_400=True)
    with pytest.raises(ProviderError):
        await _azure(server).complete(_MSGS, max_tokens=10)
    assert len(server.bodies) == 1


@pytest.mark.asyncio
async def test_stream_adapts_before_yielding() -> None:
    sse = b'data: {"choices":[{"delta":{"content":"hola"}}]}\n\ndata: [DONE]\n\n'
    bodies: list[dict[str, Any]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        bodies.append(body)
        if "max_tokens" in body:
            return httpx.Response(400, json=_MAX_TOKENS_ERR)
        return httpx.Response(200, content=sse)

    provider = AzureFoundryAPIMProvider(
        apim_base_url="https://apim",
        deployment="gpt-5",
        subscription_key="k",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    text = "".join([c.delta async for c in provider.stream(_MSGS, max_tokens=500)])
    assert text == "hola"
    assert bodies[-1]["max_completion_tokens"] == 500
