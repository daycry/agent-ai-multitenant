"""Integration: el `reasoning_effort` del spec/model_config debe llegar al body
de la llamada del proveedor traducido a su parámetro nativo (ADR 0070).

  * azure_foundry / copilot → `reasoning_effort` en el JSON de /chat/completions
  * ollama                  → `think: true`
  * claude_sdk              → `effort` hacia el SDK (vía complete())

`off`/ausente = no enviar nada (no-op; modelos sin razonamiento lo ignoran).
Transportes mockeados (httpx.MockTransport) — sin red ni credenciales reales.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from agent_runtime.providers import (
    AzureFoundryModelClient,
    ClaudeSDKModelClient,
    OllamaModelClient,
    build_provider_client,
)

pytestmark = pytest.mark.integration

_STATE: dict[str, Any] = {
    "task": {"id": "t-1", "title": "Write a sea poem", "description": "about the tide"},
    "context": [{"role": "task", "title": "Write a sea poem"}],
    "last_observation": None,
    "output": "the sea poem",
}


def _chat_text(content: str) -> dict[str, Any]:
    return {
        "model": "m",
        "choices": [{"message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 8, "total_tokens": 48},
    }


def _capture_body() -> tuple[dict[str, Any], Any]:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_text("done"))

    return seen, httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# OpenAI-compat (azure/copilot comparten el helper + la base): reasoning_effort
# ---------------------------------------------------------------------------
def test_azure_sends_reasoning_effort_in_body() -> None:
    seen, http = _capture_body()
    AzureFoundryModelClient(
        model="o3",
        apim_base_url="https://x.azure-api.net/f",
        deployment="o3",
        subscription_key="sub",
        http_client=http,
        reasoning_effort="high",
    ).decide(_STATE)
    assert seen["body"]["reasoning_effort"] == "high"


def test_azure_off_omits_reasoning_effort() -> None:
    seen, http = _capture_body()
    AzureFoundryModelClient(
        model="gpt-4o",
        apim_base_url="https://x.azure-api.net/f",
        deployment="gpt-4o",
        subscription_key="sub",
        http_client=http,
        reasoning_effort="off",
    ).decide(_STATE)
    assert "reasoning_effort" not in seen["body"]


# ---------------------------------------------------------------------------
# Ollama: think (booleano)
# ---------------------------------------------------------------------------
def test_ollama_think_in_body() -> None:
    seen, http = _capture_body()
    OllamaModelClient(model="qwen3", http_client=http, reasoning_effort="think").decide(_STATE)
    assert seen["body"]["think"] is True


def test_ollama_off_omits_think() -> None:
    seen, http = _capture_body()
    OllamaModelClient(model="llama3.2", http_client=http, reasoning_effort="off").decide(_STATE)
    assert "think" not in seen["body"]


# ---------------------------------------------------------------------------
# build_provider_client: thread spec["reasoning_effort"] a cada adaptador
# ---------------------------------------------------------------------------
def test_factory_threads_reasoning_to_ollama() -> None:
    client = build_provider_client(
        {"kind": "ollama", "model": "qwen3", "reasoning_effort": "think"}
    )
    assert client._extra_call_kwargs == {"think": True}  # type: ignore[attr-defined]


def test_factory_threads_reasoning_to_azure() -> None:
    client = build_provider_client(
        {
            "kind": "azure_foundry",
            "model": "o3",
            "apim_base_url": "https://x.azure-api.net/f",
            "subscription_key": "sub",
            "reasoning_effort": "high",
        }
    )
    assert client._extra_call_kwargs == {"reasoning_effort": "high"}  # type: ignore[attr-defined]


def test_factory_threads_reasoning_to_claude() -> None:
    client = build_provider_client(
        {"kind": "claude_sdk", "model": "claude-opus-4-8", "reasoning_effort": "xhigh"}
    )
    assert client._effort == "xhigh"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# claude_sdk: off→None y forwarding de `effort` a complete()
# ---------------------------------------------------------------------------
def test_claude_client_maps_off_and_absent_to_none() -> None:
    assert ClaudeSDKModelClient(model="m", reasoning_effort="off")._effort is None
    assert ClaudeSDKModelClient(model="m")._effort is None
    assert ClaudeSDKModelClient(model="m", reasoning_effort="xhigh")._effort == "xhigh"


class _RecordingProvider:
    """Fake LLMProvider que registra los kwargs de complete()."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def complete(self, _messages: Any, *, model: str | None = None, **kw: Any) -> Any:
        self.kwargs = kw
        return SimpleNamespace(
            content="ok",
            model=model,
            usage=SimpleNamespace(input_tokens=1, output_tokens=1, cost_usd=0.0),
            tool_calls=None,
        )


def test_claude_decide_forwards_effort_to_complete() -> None:
    client = ClaudeSDKModelClient(model="m", reasoning_effort="xhigh")
    rec = _RecordingProvider()
    client.provider = rec  # type: ignore[assignment]
    client.decide(_STATE)
    assert rec.kwargs.get("effort") == "xhigh"
