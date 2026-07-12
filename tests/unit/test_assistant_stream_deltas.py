"""A2 fase 2 / ADR 0073 F2 — deltas token-a-token en el turno del asistente.

`stream()` ya existía en los 4 providers de shared-llm; el hueco era el grafo:
`LLMAssistantModel.decide_stream` (síntesis final SIN tools vía
provider.stream) + el nodo `finish` que, con `on_delta` presente y sin
respuesta comprometida, redacta la respuesta en streaming (camino
FINISH_NUDGE). Los turnos que ya traían la respuesta en `last_content`
conservan el comportamiento previo (frame único `answer`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from api_server.assistant.graph import run_assistant_turn
from api_server.assistant.llm import LLMAssistantModel
from shared_llm import Message
from shared_llm.types import CompletionResponse, StreamChunk, Usage

pytestmark = pytest.mark.unit


class _StreamingProvider:
    name = "fake"

    def __init__(self) -> None:
        self.stream_calls = 0

    async def complete(self, messages: Sequence[Message], **kwargs: Any) -> CompletionResponse:
        # decide() con tools: el modelo pide una tool la 1.ª vez y luego nada
        # (content vacío) → el grafo entra por el camino FINISH_NUDGE.
        return CompletionResponse(content="", model="m", provider="fake", usage=Usage(1, 1, 0.0))

    async def stream(
        self, messages: Sequence[Message], **kwargs: Any
    ) -> AsyncIterator[StreamChunk]:
        self.stream_calls += 1
        for piece in ("Hola ", "mundo"):
            yield StreamChunk(delta=piece)
        yield StreamChunk(delta="", done=True, usage=Usage(5, 2, 0.0))

    async def aclose(self) -> None:  # pragma: no cover
        return


@pytest.mark.asyncio
async def test_decide_stream_yields_deltas_and_accumulates_usage() -> None:
    provider = _StreamingProvider()
    model = LLMAssistantModel(provider=provider, model="m")
    from api_server.assistant.graph import AssistantState

    state = AssistantState(system_prompt="s", chat_history=[], enabled_tools=())
    deltas = [d async for d in model.decide_stream(state)]
    assert deltas == ["Hola ", "mundo"]
    assert model.usage_calls == 1
    assert model.usage_output_tokens == 2


@pytest.mark.asyncio
async def test_finish_nudge_streams_when_on_delta_present() -> None:
    provider = _StreamingProvider()
    model = LLMAssistantModel(provider=provider, model="m")
    seen: list[str] = []

    async def _on_delta(text: str) -> None:
        seen.append(text)

    result = await run_assistant_turn(
        model,
        system_prompt="s",
        enabled_tools=(),
        tool_ctx=None,  # type: ignore[arg-type]
        on_delta=_on_delta,
    )
    # La respuesta final es la concatenación de los deltas emitidos.
    assert result.content == "Hola mundo"
    assert seen == ["Hola ", "mundo"]
    assert provider.stream_calls == 1


@pytest.mark.asyncio
async def test_without_on_delta_behaviour_unchanged() -> None:
    class _ProseProvider(_StreamingProvider):
        async def complete(self, messages: Sequence[Message], **kwargs: Any) -> CompletionResponse:
            return CompletionResponse(
                content="directa", model="m", provider="fake", usage=Usage(1, 1, 0.0)
            )

    provider = _ProseProvider()
    model = LLMAssistantModel(provider=provider, model="m")
    result = await run_assistant_turn(
        model,
        system_prompt="s",
        enabled_tools=(),
        tool_ctx=None,  # type: ignore[arg-type]
    )
    assert result.content == "directa"
    assert provider.stream_calls == 0
