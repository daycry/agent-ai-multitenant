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


class _ToolsRecordingProvider:
    """Fake LLMProvider que registra el argumento ``tools`` de complete()."""

    name = "fake"

    def __init__(self) -> None:
        self.tools: Any = "unset"

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
        self.tools = tools
        return SimpleNamespace(content="ok", tool_calls=None)


@pytest.mark.asyncio
async def test_schema_fn_lets_cortex_send_its_own_tool_schemas() -> None:
    """Hallazgo #10e: el córtex debe poder inyectar SU catálogo de schemas.

    Antes, ``decide`` usaba SIEMPRE ``tool_schemas`` del asistente, que no conoce
    las tools del córtex → toda ``complete()`` del córtex iba con ``tools=None``
    (el modelo nunca recibía los schemas de cortex_remember/cortex_recall_more).
    Con ``schema_fn`` inyectable, el córtex pasa ``cortex_tool_schemas`` y el
    provider recibe los schemas correctos, envueltos {type:function, function}."""
    from api_server.cortex.tools import cortex_tool_schemas

    rec = _ToolsRecordingProvider()
    model = LLMAssistantModel(
        provider=rec,  # type: ignore[arg-type]
        model="m",
        schema_fn=cortex_tool_schemas,
    )
    state = AssistantState(
        system_prompt="sys",
        chat_history=[{"role": "user", "content": "hi"}],
        enabled_tools=("cortex_remember", "cortex_recall_more"),
    )
    await model.decide(state)
    assert isinstance(rec.tools, list) and rec.tools, "el córtex debe enviar sus schemas"
    names = [t["function"]["name"] for t in rec.tools]
    assert names == ["cortex_remember", "cortex_recall_more"]
    assert all(t["type"] == "function" for t in rec.tools)


@pytest.mark.asyncio
async def test_default_schema_fn_is_the_assistant_catalogue() -> None:
    """Regresión: sin ``schema_fn`` el modelo sigue usando ``tool_schemas`` del
    asistente (una tool del córtex NO existe en ese catálogo → tools=None)."""
    rec = _ToolsRecordingProvider()
    model = LLMAssistantModel(provider=rec, model="m")  # type: ignore[arg-type]
    # Una tool del córtex no está en el catálogo del asistente → sin schemas.
    state = AssistantState(
        system_prompt="sys",
        chat_history=[{"role": "user", "content": "hi"}],
        enabled_tools=("cortex_remember",),
    )
    await model.decide(state)
    assert rec.tools is None
    # Y una tool REAL del asistente sí produce su schema.
    rec2 = _ToolsRecordingProvider()
    model2 = LLMAssistantModel(provider=rec2, model="m")  # type: ignore[arg-type]
    state2 = AssistantState(
        system_prompt="sys",
        chat_history=[{"role": "user", "content": "hi"}],
        enabled_tools=("remember_about_me",),
    )
    await model2.decide(state2)
    assert isinstance(rec2.tools, list) and rec2.tools
    assert rec2.tools[0]["function"]["name"] == "remember_about_me"
