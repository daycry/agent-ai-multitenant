"""El recorte silencioso del contexto de Ollama se detecta (ADR 0169 D6, `task_cm_08`).

Ollama no deja fijar la ventana de contexto por su endpoint compatible con OpenAI y,
cuando el prompt no cabe, recorta el principio de la conversación sin avisar: el
prompt de sistema. El agente sigue, con respuestas que parecen válidas y no lo son.

**Por qué no se usa ``usage.prompt_tokens``**: Ollama informa ahí de los tokens que
EVALÚA, no de los que reutiliza de su caché de prefijos. En el segundo turno de un
run el prefijo es el mismo y el número sale bajo sin que haya recorte. Se pregunta en
cambio a ``/api/ps`` con qué contexto cargó el modelo, una vez por run, y se compara
con una cota INFERIOR del prompt (cinco caracteres por token), así que sólo salta
cuando el recorte es seguro.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from agent_runtime.graph import _provider_abort_code
from agent_runtime.providers import (
    ContextTruncated,
    OllamaModelClient,
    _ProviderModelClient,
    ollama_loaded_context,
)
from shared_llm import Message
from shared_llm.types import CompletionResponse, Usage


class _Provider:
    name = "ollama"

    async def complete(self, _messages: Sequence[Message], **_kwargs: Any) -> CompletionResponse:
        return CompletionResponse(content="hecho", model="m", provider="ollama", usage=Usage())

    async def aclose(self) -> None:
        return None


class _Probe:
    def __init__(self, value: int | None) -> None:
        self.value = value
        self.calls = 0

    def __call__(self) -> int | None:
        self.calls += 1
        return self.value


def _state(chars: int) -> dict[str, Any]:
    return {"task": {"title": "t", "description": "x" * chars}, "context": [], "iteration": 0}


def _client(probe: _Probe) -> OllamaModelClient:
    client = OllamaModelClient(model="qwen3.5:9b", base_url="http://ollama:11434/v1")
    client.provider = _Provider()  # type: ignore[assignment]
    client._context_probe = probe
    return client


def test_a_prompt_that_cannot_fit_aborts_with_a_typed_error() -> None:
    with pytest.raises(ContextTruncated, match="OLLAMA_CONTEXT_LENGTH") as info:
        _client(_Probe(4096)).decide(_state(60_000))
    assert _provider_abort_code(info.value) == "context_truncated"


def test_a_prompt_that_fits_goes_through() -> None:
    assert _client(_Probe(32768)).decide(_state(60_000)).decision.output == "hecho"


def test_an_unknown_context_never_aborts() -> None:
    """Ollama cloud o un servidor sin `/api/ps`: sin dato, no hay veredicto."""
    assert _client(_Probe(None)).decide(_state(60_000)).decision.output == "hecho"


def test_the_server_is_asked_once_per_client() -> None:
    probe = _Probe(32768)
    client = _client(probe)
    for _ in range(3):
        client.decide(_state(1_000))
    assert probe.calls == 1


def test_a_failed_probe_is_not_retried_every_turn() -> None:
    probe = _Probe(None)
    client = _client(probe)
    for _ in range(3):
        client.decide(_state(1_000))
    assert probe.calls == 1


def test_non_ollama_clients_do_not_probe() -> None:
    probe = _Probe(10)
    client = _ProviderModelClient(provider=_Provider(), model="m")  # type: ignore[arg-type]
    client._context_probe = probe  # type: ignore[attr-defined]
    client.decide(_state(60_000))
    assert probe.calls == 0


# --- el parser de /api/ps ---------------------------------------------------------
def test_loaded_context_is_read_for_the_matching_model() -> None:
    ps = {
        "models": [
            {"name": "nomic-embed-text:latest", "context_length": 2048},
            {"name": "qwen3.5:9b", "model": "qwen3.5:9b", "context_length": 4096},
        ]
    }
    assert ollama_loaded_context(ps, "qwen3.5:9b") == 4096


def test_a_tagless_model_name_matches_latest() -> None:
    ps = {"models": [{"name": "llama3.1:latest", "context_length": 8192}]}
    assert ollama_loaded_context(ps, "llama3.1") == 8192


def test_an_unlisted_model_or_a_bad_payload_reads_as_unknown() -> None:
    assert ollama_loaded_context({"models": []}, "qwen3.5:9b") is None
    assert ollama_loaded_context({"oops": 1}, "qwen3.5:9b") is None
    assert ollama_loaded_context({"models": [{"name": "qwen3.5:9b"}]}, "qwen3.5:9b") is None
