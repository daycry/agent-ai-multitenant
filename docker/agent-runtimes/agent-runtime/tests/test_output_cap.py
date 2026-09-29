"""Cada llamada HTTP del runtime lleva un tope de salida explícito (ADR 0169 D4).

`task_cm_01` del plan `compat-modelos-2026-09-29`. Sin él, los cuatro proveedores
HTTP de ``shared-llm`` aplican su default de 1024 tokens, y un ``write_file`` de un
fichero mediano no cabe: la tool call llega cortada. Se veía poco con los modelos
de frontera porque el camino principal era ``claude_sdk``, cuyo SDK no expone el
parámetro; con un modelo de Ollama es el primer fallo que aparece.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agent_runtime.providers import (
    ClaudeSDKModelClient,
    OllamaModelClient,
    _ProviderModelClient,
    build_provider_client,
)
from shared_llm import Message
from shared_llm.types import CompletionResponse, ToolCall, Usage


class _RecordingProvider:
    """Proveedor falso que guarda los kwargs de cada ``complete``."""

    name = "fake"

    def __init__(self, response: CompletionResponse | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._response = response or CompletionResponse(
            content="hecho", model="m", provider="fake", usage=Usage()
        )

    async def complete(self, _messages: Sequence[Message], **kwargs: Any) -> CompletionResponse:
        self.calls.append(kwargs)
        return self._response

    async def aclose(self) -> None:
        return None


def _state() -> dict[str, Any]:
    return {"task": {"title": "t", "description": "d"}, "context": [], "iteration": 0}


def _client(provider: _RecordingProvider, **kwargs: Any) -> _ProviderModelClient:
    return _ProviderModelClient(provider=provider, model="m", **kwargs)


def test_decide_sends_the_default_output_cap() -> None:
    provider = _RecordingProvider()
    _client(provider).decide(_state())
    assert provider.calls[-1]["max_tokens"] == 8192


def test_decide_honours_the_configured_output_cap() -> None:
    provider = _RecordingProvider()
    _client(provider, max_output_tokens=3000).decide(_state())
    assert provider.calls[-1]["max_tokens"] == 3000


def test_review_and_assess_carry_their_own_lower_caps() -> None:
    verdict = CompletionResponse(
        content="",
        model="m",
        provider="fake",
        usage=Usage(),
        tool_calls=[ToolCall(id="1", name="submit_verdict", arguments={"passed": True})],
    )
    provider = _RecordingProvider(verdict)
    client = _client(provider)
    client.review({"task": {"title": "t"}, "output": "o", "criteria": []})  # type: ignore[arg-type]
    review_cap = provider.calls[-1]["max_tokens"]
    client.assess_progress(_state())
    assess_cap = provider.calls[-1]["max_tokens"]
    assert 0 < assess_cap <= review_cap < 8192


def test_a_configured_cap_is_never_raised_by_the_verdict_caps() -> None:
    """Un modelo pequeño configurado a 1500 no recibe 4096 en el review."""
    verdict = CompletionResponse(
        content="",
        model="m",
        provider="fake",
        usage=Usage(),
        tool_calls=[ToolCall(id="1", name="submit_verdict", arguments={"passed": True})],
    )
    provider = _RecordingProvider(verdict)
    client = _client(provider, max_output_tokens=1500)
    client.review({"task": {"title": "t"}, "output": "o", "criteria": []})  # type: ignore[arg-type]
    assert provider.calls[-1]["max_tokens"] <= 1500


def test_claude_sdk_does_not_receive_the_cap() -> None:
    """Su SDK no expone el parámetro; mandarlo sería ruido o un error."""
    assert ClaudeSDKModelClient._passes_output_cap is False


def test_the_spec_threads_max_output_tokens_into_the_client() -> None:
    client = build_provider_client(
        {"kind": "ollama", "model": "qwen", "base_url": "http://x/v1", "max_output_tokens": 2048}
    )
    assert isinstance(client, OllamaModelClient)
    assert client._max_output_tokens == 2048


# ---------------------------------------------------------------------------
# `task_cm_12`: el plazo HTTP del proveedor no corta antes que el del runtime.
# Medido el 2026-09-29 con `qwen3:8b` en CPU: los turnos pasaban de 2 minutos, el
# cliente httpx del proveedor (120 s) cortaba, Ollama registraba un 500 a los 2m0s
# exactos y el runtime reintentaba lo mismo hasta abortar con `provider_error`,
# con 900 s de presupuesto por llamada sin usar.
# ---------------------------------------------------------------------------
def test_http_clients_wait_as_long_as_the_runtime_budget() -> None:
    from agent_runtime.providers import (
        _DEFAULT_CALL_TIMEOUT_S,
        AzureFoundryModelClient,
        CopilotModelClient,
    )

    clients = [
        OllamaModelClient(model="qwen3:8b", base_url="http://ollama:11434/v1"),
        AzureFoundryModelClient(
            model="gpt-5", apim_base_url="https://apim", deployment="d", subscription_key="k"
        ),
        CopilotModelClient(model="gpt-5", github_token="gho"),
    ]
    for client in clients:
        assert client.provider._timeout >= _DEFAULT_CALL_TIMEOUT_S, type(client).__name__
