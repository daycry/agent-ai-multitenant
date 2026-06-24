"""Unit: el asistente personal debe propagar el reasoning_effort ya traducido
(extra_call_kwargs) al ``complete()`` del proveedor (ADR 0070)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from api_server.assistant.graph import AssistantState
from api_server.assistant.llm import LLMAssistantModel

pytestmark = pytest.mark.unit


class _RecordingProvider:
    """Fake LLMProvider que registra los kwargs extra de complete()."""

    name = "fake"

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}

    async def complete(
        self,
        _messages: Any,
        *,
        model: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        tools: Any = None,
        **kw: Any,
    ) -> Any:
        self.kwargs = kw
        return SimpleNamespace(content="hola", tool_calls=None)


@pytest.mark.asyncio
async def test_assistant_spreads_reasoning_kwargs() -> None:
    rec = _RecordingProvider()
    model = LLMAssistantModel(
        provider=rec,  # type: ignore[arg-type]
        model="m",
        extra_call_kwargs={"reasoning_effort": "high"},
    )
    state = AssistantState(system_prompt="sys", chat_history=[{"role": "user", "content": "hi"}])
    await model.decide(state)
    assert rec.kwargs.get("reasoning_effort") == "high"


@pytest.mark.asyncio
async def test_assistant_without_reasoning_sends_nothing_extra() -> None:
    rec = _RecordingProvider()
    model = LLMAssistantModel(provider=rec, model="m")  # type: ignore[arg-type]
    state = AssistantState(system_prompt="sys", chat_history=[{"role": "user", "content": "hi"}])
    await model.decide(state)
    assert "reasoning_effort" not in rec.kwargs
    assert "effort" not in rec.kwargs
