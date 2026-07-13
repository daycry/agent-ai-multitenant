"""ADR 0097 — sesión SDK persistente por run (la otra mitad del hilo del ADR 0110).

El hilo conversacional por run es UNA capacidad del contrato con DOS transportes:
los providers HTTP re-envían el hilo de mensajes (ADR 0110), y claude_sdk mantiene
**una sesión SDK viva** (aquí) porque su transporte es un CLI con estado propio.

Spike 2026-07-13 (credencial viva, contenedor api-server) que habilita esto:

  * `can_use_tool` con deny **sin interrupt** cierra el turno LIMPIO (1 solo deny,
    con `ResultMessage` y usage íntegros — adiós al usage frágil del interrupt);
  * la MISMA sesión recuerda el turno anterior y reusa la KV-cache del provider
    (cache_read ~45k tokens en el turno 2).

La mediación del host NO cambia: la tool se deniega en la sesión (el host la
ejecuta con su ToolRegistry/approvals/loop-detection) y su observación viaja en
el siguiente mensaje del hilo — exactamente el contrato que ya cumplen los 4
providers.

Sin SDK real: la sesión se inyecta con un `client_factory` fake.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, ClassVar

import pytest
from shared_llm.exceptions import LLMError
from shared_llm.providers.claude_agent_session import (
    HOST_EXECUTED_DENY,
    ClaudeAgentSessionProvider,
    deny_payload,
)
from shared_llm.types import Message


@dataclass
class _TextBlock:
    text: str


@dataclass
class _ToolUseBlock:
    name: str
    input: dict[str, Any]
    id: str = "tu_1"


@dataclass
class _AssistantMessage:
    content: list[Any]


@dataclass
class _UsageBlock:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _ResultMessage:
    total_cost_usd: float = 0.01
    usage: _UsageBlock = field(default_factory=_UsageBlock)


class _FakeSession:
    """Doble de ``ClaudeSDKClient``: registra los prompts y sirve turnos scriptados."""

    instances: ClassVar[list[_FakeSession]] = []

    def __init__(self, *, options: Any = None, turns: list[list[Any]] | None = None) -> None:
        self.options = options
        self.turns = turns if turns is not None else []
        self.prompts: list[str] = []
        self.connected = 0
        self.disconnected = 0
        # BaseException, no Exception: un turno puede cancelarse (CancelledError).
        self.fail_on_query: BaseException | None = None
        _FakeSession.instances.append(self)

    async def connect(self) -> None:
        self.connected += 1

    async def disconnect(self) -> None:
        self.disconnected += 1

    async def query(self, prompt: str, session_id: str = "default") -> None:
        if self.fail_on_query is not None:
            raise self.fail_on_query
        self.prompts.append(prompt)

    async def receive_response(self) -> AsyncIterator[Any]:
        turn = self.turns.pop(0) if self.turns else [_ResultMessage()]
        for msg in turn:
            yield msg


def _provider(turns: list[list[Any]] | None = None, **kw: Any) -> ClaudeAgentSessionProvider:
    _FakeSession.instances.clear()

    def _factory(options: Any) -> _FakeSession:
        return _FakeSession(options=options, turns=[list(t) for t in (turns or [])])

    return ClaudeAgentSessionProvider(
        default_model="claude-sonnet-4-5", client_factory=_factory, **kw
    )


_TOOLS = [{"name": "read_file", "description": "lee", "parameters": {}}]


@pytest.mark.asyncio
async def test_first_turn_opens_one_session_with_the_full_transcript() -> None:
    provider = _provider(turns=[[_AssistantMessage([_TextBlock("hola")]), _ResultMessage()]])
    resp = await provider.complete(
        [Message(role="system", content="SYS"), Message(role="user", content="TAREA")],
        tools=_TOOLS,
        conversation_session=True,
    )
    assert resp.content == "hola"
    assert len(_FakeSession.instances) == 1
    session = _FakeSession.instances[0]
    assert session.connected == 1
    # El primer turno lleva TODO el contexto (el system va en las options).
    assert "TAREA" in session.prompts[0]


@pytest.mark.asyncio
async def test_next_turns_reuse_the_session_and_send_only_the_new_message() -> None:
    provider = _provider(
        turns=[
            [_AssistantMessage([_TextBlock("t1")]), _ResultMessage()],
            [_AssistantMessage([_TextBlock("t2")]), _ResultMessage()],
        ]
    )
    base = [Message(role="system", content="SYS"), Message(role="user", content="TAREA")]
    await provider.complete(base, tools=_TOOLS, conversation_session=True)
    await provider.complete(
        [*base, Message(role="assistant", content="t1"), Message(role="user", content="SIGUE")],
        tools=_TOOLS,
        conversation_session=True,
    )
    assert len(_FakeSession.instances) == 1, "la sesión se reusa: memoria + KV-cache"
    session = _FakeSession.instances[0]
    assert session.prompts[1] == "SIGUE", "el historial YA vive en la sesión: no se re-envía"


@pytest.mark.asyncio
async def test_tool_call_is_harvested_and_the_partial_text_is_dropped() -> None:
    provider = _provider(
        turns=[
            [
                _AssistantMessage(
                    [
                        _TextBlock("voy a leer"),
                        _ToolUseBlock(name="mcp__host_tools__read_file", input={"path": "a.py"}),
                    ]
                ),
                _AssistantMessage([_TextBlock("ESPERANDO_HOST")]),
                _ResultMessage(usage=_UsageBlock(input_tokens=5, output_tokens=7)),
            ]
        ]
    )
    resp = await provider.complete(
        [Message(role="user", content="lee a.py")], tools=_TOOLS, conversation_session=True
    )
    assert resp.tool_calls is not None
    assert [c.name for c in resp.tool_calls] == ["read_file"]
    assert resp.content == "", "un turno de tool call no aporta respuesta al usuario"
    # El deny SIN interrupt cierra el turno limpio → el usage del turno SOBREVIVE
    # (con interrupt el ResultMessage llegaba vacío: causa raíz del usage frágil).
    assert resp.usage.output_tokens == 7


@pytest.mark.asyncio
async def test_the_host_deny_never_interrupts_the_session() -> None:
    """El contrato con el CLI: denegar la ejecución (la corre el host) SIN cortar
    la sesión — si interrumpiéramos, perderíamos el ResultMessage y la sesión."""
    payload = deny_payload()
    assert payload["interrupt"] is False
    assert payload["message"] == HOST_EXECUTED_DENY
    assert "host" in HOST_EXECUTED_DENY.lower()


@pytest.mark.asyncio
async def test_a_broken_turn_resets_the_session_and_the_next_one_reopens() -> None:
    """Auto-sanación: si la sesión muere, el siguiente turno abre otra con el
    historial COMPLETO que el cliente sigue trayendo (nunca se pierde contexto)."""
    provider = _provider(turns=[[_ResultMessage()]])
    _FakeSession.instances.clear()
    sessions: list[_FakeSession] = []

    def _factory(options: Any) -> _FakeSession:
        session = _FakeSession(
            options=options, turns=[[_AssistantMessage([_TextBlock("ok")]), _ResultMessage()]]
        )
        if not sessions:  # la primera sesión revienta al enviar
            session.fail_on_query = RuntimeError("CLI muerto")
        sessions.append(session)
        return session

    provider._client_factory = _factory
    with pytest.raises(LLMError):
        await provider.complete(
            [Message(role="user", content="A")], tools=_TOOLS, conversation_session=True
        )
    resp = await provider.complete(
        [Message(role="user", content="A"), Message(role="user", content="B")],
        tools=_TOOLS,
        conversation_session=True,
    )
    assert resp.content == "ok"
    assert len(sessions) == 2, "la sesión rota se descarta y se reabre"
    assert sessions[0].disconnected == 1
    assert "A" in sessions[1].prompts[0], "la sesión nueva recibe el historial entero"


@pytest.mark.asyncio
async def test_a_cancelled_turn_also_drops_the_poisoned_session() -> None:
    """Un timeout del turno (asyncio.wait_for) CANCELA la coroutine: el
    ``CancelledError` hereda de ``BaseException``, no de ``Exception``. Si no lo
    capturamos, la sesión a medias queda viva y el turno siguiente la reusa
    envenenada. Debe descartarse igual que un fallo normal, y el
    ``CancelledError`` re-propagarse tal cual (no envolverse)."""
    provider = _provider()
    _FakeSession.instances.clear()
    sessions: list[_FakeSession] = []

    def _factory(options: Any) -> _FakeSession:
        session = _FakeSession(
            options=options, turns=[[_AssistantMessage([_TextBlock("ok")]), _ResultMessage()]]
        )
        if not sessions:  # el primer turno se cancela a mitad
            session.fail_on_query = asyncio.CancelledError()
        sessions.append(session)
        return session

    provider._client_factory = _factory
    with pytest.raises(asyncio.CancelledError):
        await provider.complete(
            [Message(role="user", content="A")], tools=_TOOLS, conversation_session=True
        )
    assert sessions[0].disconnected == 1, "la sesión cancelada se descarta"
    # El turno siguiente NO reusa la sesión envenenada: abre una nueva.
    resp = await provider.complete(
        [Message(role="user", content="A"), Message(role="user", content="B")],
        tools=_TOOLS,
        conversation_session=True,
    )
    assert resp.content == "ok"
    assert len(sessions) == 2


@pytest.mark.asyncio
async def test_without_the_session_flag_it_is_the_one_shot_provider() -> None:
    """Sin `conversation_session` (p.ej. el review, o el flag global OFF) el
    comportamiento es EXACTAMENTE el histórico: una query one-shot, sin sesión."""

    async def _query(prompt: str, options: Any) -> AsyncIterator[Any]:
        yield _AssistantMessage([_TextBlock("one-shot")])
        yield _ResultMessage()

    _FakeSession.instances.clear()
    provider = ClaudeAgentSessionProvider(default_model="m", query_fn=_query)
    resp = await provider.complete([Message(role="user", content="hola")])
    assert resp.content == "one-shot"
    assert _FakeSession.instances == [], "sin flag de sesión no se abre ninguna"


@pytest.mark.asyncio
async def test_aclose_disconnects_the_live_session() -> None:
    provider = _provider(turns=[[_ResultMessage()]])
    await provider.complete(
        [Message(role="user", content="A")], tools=_TOOLS, conversation_session=True
    )
    await provider.aclose()
    assert _FakeSession.instances[0].disconnected == 1
