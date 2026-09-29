"""Transporte Responses para Codex y los GPT-5.x que lo exigen (ADR 0169, `task_cm_06`).

Copilot contesta ``unsupported_api_for_model`` y Azure ``OperationNotSupported`` cuando
un modelo sólo servido por la Responses API llega por ``/chat/completions``. El
proveedor cambia de transporte para ese modelo, lo memoriza, y el resto de la capa no
se entera: devuelve el mismo `CompletionResponse`, con las mismas señales de truncado
que usa la guarda F32 del runtime.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
import pytest
from shared_llm.providers import AzureFoundryAPIMProvider, CopilotProvider
from shared_llm.providers._openai_compat import completion_signals, corrupt_tool_call_indexes
from shared_llm.providers._openai_responses import parse_responses, to_responses_body
from shared_llm.types import Message

_TOOL = {
    "type": "function",
    "function": {
        "name": "write_file",
        "description": "Write a file.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
}
_MSGS = [
    Message(role="system", content="Eres un agente."),
    Message(role="user", content="Escribe hola.txt"),
]


def _response(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "object": "response",
        "id": "resp_1",
        "model": "gpt-5-codex",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "Hecho."}],
            },
            {
                "type": "function_call",
                "call_id": "call_9",
                "name": "write_file",
                "arguments": json.dumps({"path": "hola.txt"}),
            },
        ],
        "usage": {
            "input_tokens": 120,
            "output_tokens": 30,
            "input_tokens_details": {"cached_tokens": 100},
        },
    }
    base.update(overrides)
    return base


# --- construcción del cuerpo ------------------------------------------------------
def test_body_maps_the_chat_shape_to_the_responses_shape() -> None:
    body = to_responses_body(
        _MSGS,
        model="gpt-5-codex",
        max_tokens=8192,
        temperature=None,
        tools=[_TOOL],
        tool_choice={"type": "function", "function": {"name": "write_file"}},
        reasoning_effort="high",
    )
    assert body["instructions"] == "Eres un agente."
    assert body["input"] == [{"role": "user", "content": "Escribe hola.txt"}]
    assert body["tools"] == [
        {
            "type": "function",
            "name": "write_file",
            "description": "Write a file.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ]
    assert body["tool_choice"] == {"type": "function", "name": "write_file"}
    assert body["max_output_tokens"] == 8192
    assert body["reasoning"] == {"effort": "high"}
    assert body["store"] is False
    assert "temperature" not in body
    assert "max_tokens" not in body


def test_body_drops_chat_only_kwargs() -> None:
    body = to_responses_body(
        _MSGS,
        model="m",
        max_tokens=10,
        temperature=None,
        tools=None,
        stream_options={"include_usage": True},
    )
    assert "stream_options" not in body


# --- parseo -----------------------------------------------------------------------
def test_parse_reads_text_tool_calls_and_usage() -> None:
    resp = parse_responses(_response(), provider="p", fallback_model="m")
    assert resp.content == "Hecho."
    assert resp.tool_calls is not None
    assert [(c.id, c.name, c.arguments) for c in resp.tool_calls] == [
        ("call_9", "write_file", {"path": "hola.txt"})
    ]
    assert (resp.usage.input_tokens, resp.usage.output_tokens) == (120, 30)
    assert resp.usage.cache_read_tokens == 100
    assert resp.stop_reason == "completed"


def test_an_incomplete_response_at_the_cap_reads_as_truncated() -> None:
    raw = _response(status="incomplete", incomplete_details={"reason": "max_output_tokens"})
    resp = parse_responses(raw, provider="p", fallback_model="m")
    assert resp.stop_reason == "length"
    assert completion_signals(raw).truncated is True


def test_signals_see_corrupt_function_call_arguments() -> None:
    raw = _response()
    raw["output"][2]["arguments"] = '{"path": "hol'
    assert completion_signals(raw).malformed_tool_args is True
    assert corrupt_tool_call_indexes(raw) == {0}


# --- enrutado en los proveedores ------------------------------------------------------
class _Vendor:
    """Chat/completions rechaza el modelo; /responses lo sirve."""

    def __init__(self, error: dict[str, Any], responses_path: str) -> None:
        self.paths: list[str] = []
        self.bodies: list[dict[str, Any]] = []
        self._error = error
        self._responses_path = responses_path

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/copilot_internal/v2/token"):
            return httpx.Response(200, json={"token": "jwt", "expires_at": time.time() + 1500})
        self.paths.append(req.url.path)
        self.bodies.append(json.loads(req.content))
        if req.url.path.endswith("/chat/completions"):
            return httpx.Response(400, json=self._error)
        assert req.url.path == self._responses_path, req.url.path
        return httpx.Response(200, json=_response())

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


_COPILOT_ERR = {
    "error": {
        "message": "model gpt-5-codex is not accessible via the /chat/completions endpoint",
        "code": "unsupported_api_for_model",
    }
}
_AZURE_ERR = {
    "error": {
        "code": "OperationNotSupported",
        "message": "The chatCompletion operation does not work with the specified model, "
        "gpt-5-codex.",
    }
}


@pytest.mark.asyncio
async def test_copilot_switches_to_responses_and_remembers() -> None:
    vendor = _Vendor(_COPILOT_ERR, "/responses")
    provider = CopilotProvider(github_token="gho", http_client=vendor.client())
    resp = await provider.complete(_MSGS, model="gpt-5-codex", tools=[_TOOL], max_tokens=100)
    assert resp.tool_calls is not None and resp.tool_calls[0].name == "write_file"
    assert vendor.paths == ["/chat/completions", "/responses"]
    await provider.complete(_MSGS, model="gpt-5-codex", max_tokens=100)
    assert vendor.paths[-1] == "/responses"
    assert len(vendor.paths) == 3, "el segundo turno ya no pasa por chat/completions"


@pytest.mark.asyncio
async def test_azure_switches_to_the_v1_responses_endpoint() -> None:
    vendor = _Vendor(_AZURE_ERR, "/openai/v1/responses")
    provider = AzureFoundryAPIMProvider(
        apim_base_url="https://apim",
        deployment="codex-dep",
        subscription_key="k",
        http_client=vendor.client(),
    )
    resp = await provider.complete(_MSGS, max_tokens=100)
    assert resp.content == "Hecho."
    assert vendor.bodies[-1]["model"] == "codex-dep"


@pytest.mark.asyncio
async def test_stream_on_a_responses_only_model_yields_the_whole_answer() -> None:
    vendor = _Vendor(_COPILOT_ERR, "/responses")
    provider = CopilotProvider(github_token="gho", http_client=vendor.client())
    chunks = [c async for c in provider.stream(_MSGS, model="gpt-5-codex", max_tokens=100)]
    assert "".join(c.delta for c in chunks) == "Hecho."
    assert chunks[-1].done is True
    assert chunks[-1].usage is not None and chunks[-1].usage.output_tokens == 30
