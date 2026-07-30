"""Unit tests for the ClaudeAgentProvider — no real SDK needed.

The provider accepts an injected `query_fn` shaped like
`claude_agent_sdk.query`. We feed it a fake that yields a sequence of
SDK-shaped messages so the parsing / `AgentRunEvent` translation is
exercised without importing the real package.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import pytest
from shared_llm.exceptions import ProviderError
from shared_llm.providers import ClaudeAgentProvider
from shared_llm.types import AgentRunEvent, Message


# ----------------------------------------------------------------------
# Fake SDK message shapes — duck-typed to match what the real SDK emits.
# ----------------------------------------------------------------------
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
    total_cost_usd: float
    usage: _UsageBlock


def _make_query(*messages: Any):  # type: ignore[no-untyped-def]
    """Build a fake query callable that yields the given SDK messages."""

    async def _q(prompt: str, options: Any) -> AsyncIterator[Any]:
        for m in messages:
            yield m

    return _q


@dataclass
class _ErrorResultMessage:
    """A ResultMessage shaped like the CLI's failing result: is_error=True with
    the real text in `result` (e.g. 'Not logged in'), while `errors` is empty and
    `subtype` is the misleading 'success' the SDK falls back to."""

    is_error: bool = True
    result: str | None = "Not logged in · Please run /login"
    subtype: str = "success"
    errors: list[str] | None = None
    api_error_status: int | None = None


def _make_query_then_raise(*messages: Any, exc: Exception):  # type: ignore[no-untyped-def]
    """Yield the messages, then raise — mirrors the SDK emitting a failing
    ResultMessage and then a trailing ProcessError on stream close."""

    async def _q(prompt: str, options: Any) -> AsyncIterator[Any]:
        for m in messages:
            yield m
        raise exc

    return _q


def test_the_api_key_is_kept_off_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Modo API key: la clave NO se exporta al entorno del proceso.

    Este test afirmaba lo contrario —que la clave aterrizaba en
    `ANTHROPIC_API_KEY`— y con ello **fijaba el defecto** que el ADR 0076 tenía
    anotado como prerequisito de seguridad. La credencial se entrega ahora por
    llamada, vía `ClaudeAgentOptions.env`; el detalle y el escenario de fuga
    entre proveedores están en `test_claude_agent_credential_isolation.py`.
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    ClaudeAgentProvider(api_key="sk-ant-test-DO-NOT-LEAK", query_fn=_make_query())
    assert os.environ.get("ANTHROPIC_API_KEY") is None


def test_the_subscription_token_is_kept_off_the_process_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Modo suscripción Pro/Max (ADR 0063): el token tampoco toca el entorno.

    Mismo cambio y mismo motivo que el de la API key: antes se exportaba a
    `CLAUDE_CODE_OAUTH_TOKEN` de forma global y permanente.
    """
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    ClaudeAgentProvider(oauth_token="sk-ant-oat-test-DO-NOT-LEAK", query_fn=_make_query())
    assert os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") is None


@pytest.mark.asyncio
async def test_complete_collects_text_blocks_and_usage() -> None:
    fake_query = _make_query(
        _AssistantMessage(content=[_TextBlock(text="Hello, ")]),
        _AssistantMessage(content=[_TextBlock(text="world.")]),
        _ResultMessage(
            total_cost_usd=0.005,
            usage=_UsageBlock(input_tokens=10, output_tokens=20),
        ),
    )
    p = ClaudeAgentProvider(query_fn=fake_query, default_model="claude-sonnet-4-5")
    resp = await p.complete([Message(role="user", content="hi")])
    assert resp.content == "Hello, world."
    assert resp.provider == "claude_agent"
    assert resp.usage.input_tokens == 10
    assert resp.usage.output_tokens == 20
    assert resp.usage.cost_usd == 0.005


@pytest.mark.asyncio
async def test_complete_surfaces_real_error_text_on_auth_failure() -> None:
    """When the CLI returns is_error with the real reason in `result` and the SDK
    raises a cryptic 'error result: success', the provider must surface the REAL
    text and raise AuthError (the failure is 'Not logged in'), not the useless
    SDK string."""
    from shared_llm.exceptions import AuthError

    fake_query = _make_query_then_raise(
        _ErrorResultMessage(result="Not logged in · Please run /login"),
        exc=RuntimeError("Claude Code returned an error result: success"),
    )
    p = ClaudeAgentProvider(query_fn=fake_query, default_model="claude-sonnet-4-5")
    with pytest.raises(AuthError) as ei:
        await p.complete([Message(role="user", content="hi")])
    assert "Not logged in" in str(ei.value)
    assert "error result: success" not in str(ei.value)


@pytest.mark.asyncio
async def test_complete_surfaces_real_error_text_on_non_auth_failure() -> None:
    """A non-auth failing result (e.g. an API 529) surfaces its real text as a
    ProviderError — still better than the cryptic SDK string."""
    fake_query = _make_query_then_raise(
        _ErrorResultMessage(result="Overloaded", subtype="success", api_error_status=529),
        exc=RuntimeError("Claude Code returned an error result: success"),
    )
    p = ClaudeAgentProvider(query_fn=fake_query, default_model="claude-sonnet-4-5")
    with pytest.raises(ProviderError) as ei:
        await p.complete([Message(role="user", content="hi")])
    assert "Overloaded" in str(ei.value)


@pytest.mark.asyncio
async def test_stream_yields_text_deltas_and_a_final_done_chunk() -> None:
    fake_query = _make_query(
        _AssistantMessage(content=[_TextBlock(text="abc")]),
        _ResultMessage(
            total_cost_usd=0.001,
            usage=_UsageBlock(input_tokens=1, output_tokens=2),
        ),
    )
    p = ClaudeAgentProvider(query_fn=fake_query, default_model="claude-sonnet-4-5")
    chunks = []
    async for c in p.stream([Message(role="user", content="hi")]):
        chunks.append(c)
    assert chunks[0].delta == "abc"
    assert chunks[-1].done is True
    assert chunks[-1].usage is not None
    assert chunks[-1].usage.cost_usd == 0.001


@pytest.mark.asyncio
async def test_complete_emits_tool_calls_when_model_requests_a_tool() -> None:
    """Protocol contract (ADR 0021): complete() debe HONRAR `tools` y exponer las
    peticiones de tool del modelo como CompletionResponse.tool_calls — misma forma
    que los providers OpenAI-compatibles — para que el host (grafo del asistente /
    loop del agente) las ejecute. El SDK nombra las tools MCP in-process como
    ``mcp__<server>__<tool>``; lo recortamos al nombre base que registró el host."""
    fake_query = _make_query(
        _AssistantMessage(
            content=[
                _ToolUseBlock(
                    name="mcp__host_tools__remember_about_me",
                    input={"content": "Mi nombre es Dani"},
                    id="tu_42",
                )
            ]
        ),
    )
    p = ClaudeAgentProvider(query_fn=fake_query, default_model="claude-sonnet-4-5")
    resp = await p.complete(
        [Message(role="user", content="me llamo Dani")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "remember_about_me",
                    "description": "Guarda un dato del usuario",
                    "parameters": {
                        "type": "object",
                        "properties": {"content": {"type": "string"}},
                    },
                },
            }
        ],
    )
    assert resp.tool_calls is not None
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "remember_about_me"
    assert resp.tool_calls[0].arguments == {"content": "Mi nombre es Dani"}
    assert resp.tool_calls[0].id == "tu_42"


@pytest.mark.asyncio
async def test_complete_with_tools_returns_text_when_model_does_not_call_a_tool() -> None:
    """Si se ofrecen tools pero el modelo solo responde texto, complete() devuelve
    el texto y tool_calls=None (paridad con los providers OpenAI-compatibles)."""
    fake_query = _make_query(
        _AssistantMessage(content=[_TextBlock(text="Encantado.")]),
        _ResultMessage(total_cost_usd=0.001, usage=_UsageBlock(input_tokens=5, output_tokens=3)),
    )
    p = ClaudeAgentProvider(query_fn=fake_query, default_model="claude-sonnet-4-5")
    resp = await p.complete(
        [Message(role="user", content="hola")],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "remember_about_me",
                    "description": "x",
                    "parameters": {},
                },
            }
        ],
    )
    assert resp.tool_calls is None
    assert resp.content == "Encantado."


@pytest.mark.asyncio
async def test_run_agent_yields_typed_agent_run_events() -> None:
    fake_query = _make_query(
        _AssistantMessage(content=[_ToolUseBlock(name="Read", input={"path": "file.txt"})]),
        _AssistantMessage(content=[_TextBlock(text="done")]),
        _ResultMessage(
            total_cost_usd=0.002,
            usage=_UsageBlock(input_tokens=3, output_tokens=4),
        ),
    )
    p = ClaudeAgentProvider(query_fn=fake_query, default_model="claude-sonnet-4-5")
    events = []
    async for evt in p.run_agent("Read file.txt", allowed_tools=["Read"], max_turns=2):
        events.append(evt)

    assert all(isinstance(e, AgentRunEvent) for e in events)
    assert events[0].kind == "tool_use"
    assert events[0].tool_use == {"name": "Read", "input": {"path": "file.txt"}, "id": "tu_1"}
    assert events[1].kind == "text"
    assert events[1].text == "done"
    assert events[2].kind == "result"
    assert events[2].usage is not None
    assert events[2].usage.cost_usd == 0.002


@pytest.mark.asyncio
async def test_run_agent_propagates_effort_to_options() -> None:
    # Regression (córtex F0 precondición): run_agent no propagaba `effort` a
    # _build_options (a diferencia de complete/stream), así que el razonamiento
    # extendido (ADR 0070) se ignoraba en silencio en el modo agéntico.
    fake_query = _make_query(_AssistantMessage(content=[_TextBlock(text="ok")]))
    p = ClaudeAgentProvider(query_fn=fake_query, default_model="claude-sonnet-4-5")
    captured: dict[str, Any] = {}
    original = p._build_options

    def _spy(**kwargs: Any):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        return original(**kwargs)

    p._build_options = _spy  # type: ignore[method-assign]
    async for _ in p.run_agent("hola", effort="high"):
        pass
    assert captured.get("effort") == "high"


@pytest.mark.asyncio
async def test_complete_routes_native_allowed_tools_into_tool_path() -> None:
    """ADR 0076 (córtex F1): las web tools NATIVAS del SDK (WebSearch/WebFetch) van
    como `allowed_tools` y deben seguir activas AUN cuando hay host tools (MCP) en
    juego — el córtex usa ambas a la vez. Verificamos que complete() reenvía
    `allowed_tools` al camino con tools host (`_complete_with_tools`)."""
    fake_query = _make_query(_AssistantMessage(content=[_TextBlock(text="ok")]))
    p = ClaudeAgentProvider(query_fn=fake_query, default_model="claude-sonnet-4-5")
    captured: dict[str, Any] = {}

    async def _spy(**kwargs: Any):  # type: ignore[no-untyped-def]
        captured.update(kwargs)
        from shared_llm.types import CompletionResponse

        return CompletionResponse(content="", model="m", provider="claude_agent")

    p._complete_with_tools = _spy  # type: ignore[method-assign]
    await p.complete(
        [Message(role="user", content="busca en la web")],
        tools=[
            {
                "type": "function",
                "function": {"name": "cortex_remember", "description": "x", "parameters": {}},
            }
        ],
        allowed_tools=["WebSearch", "WebFetch"],
    )
    assert captured.get("allowed_tools") == ["WebSearch", "WebFetch"]


def test_build_tool_options_keeps_native_allowed_tools() -> None:
    """`_build_tool_options` asigna las web tools nativas a `allowed_tools` del
    `ClaudeAgentOptions` (auto-aprobadas), separadas de las host tools (MCP) que el
    interceptor `can_use_tool` captura. Requiere el SDK real (opcional)."""
    pytest.importorskip("claude_agent_sdk")
    p = ClaudeAgentProvider(default_model="claude-sonnet-4-5")
    options = p._build_tool_options(
        system="s",
        model="claude-sonnet-4-5",
        specs=[{"name": "cortex_remember", "description": "x", "parameters": {}}],
        max_turns=4,
        effort="high",
        allowed_tools=["WebSearch", "WebFetch"],
    )
    assert list(options.allowed_tools) == ["WebSearch", "WebFetch"]


@pytest.mark.asyncio
async def test_flatten_collapses_chat_into_human_assistant_transcript() -> None:
    """The SDK's `query()` takes a string prompt; we collapse the chat
    history into a `Human:`/`Assistant:` transcript with system prepended."""
    captured_prompts: list[str] = []

    async def _q(prompt: str, options: Any) -> AsyncIterator[Any]:
        captured_prompts.append(prompt)
        yield _AssistantMessage(content=[_TextBlock(text="ok")])

    p = ClaudeAgentProvider(query_fn=_q)
    await p.complete(
        [
            Message(role="system", content="Be concise."),
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
            Message(role="user", content="goodbye"),
        ]
    )
    transcript = captured_prompts[0]
    assert transcript == "Human: hi\n\nAssistant: hello\n\nHuman: goodbye"


# ======================================================================
# prod-07 task_prod07_09 (c) — timeout de pared alrededor del query del SDK
#
# El SDK arranca un CLI de Node como SUBPROCESO. Si ese proceso se queda
# colgado (sin salida y sin cerrar el stream), el `async for` que lo drena no
# vuelve NUNCA: la request del asistente que lo espera se cuelga
# indefinidamente y ni el usuario ni un timeout de uvicorn la rescatan (el
# turno ya empezó a escribir la respuesta). Los otros tres providers están
# protegidos por el timeout de httpx; este camino no tenía ninguno.
#
# El presupuesto es POR MENSAJE, no por llamada: un modelo razonando 20 min
# emite tokens mientras piensa, así que el progreso reinicia el reloj y solo
# salta cuando de verdad no hay nada al otro lado.
# ======================================================================
def _hanging_query(delay: float = 30.0):  # type: ignore[no-untyped-def]
    """Un query del SDK que se cuelga ANTES de emitir su primer mensaje."""

    async def _q(prompt: str, options: Any) -> AsyncIterator[Any]:
        await asyncio.sleep(delay)
        yield _AssistantMessage(content=[_TextBlock(text="jamás llega")])

    return _q


@pytest.mark.asyncio
async def test_complete_timeout_when_the_sdk_cli_wedges() -> None:
    p = ClaudeAgentProvider(query_fn=_hanging_query(), timeout=0.05)
    with pytest.raises(ProviderError) as info:
        await p.complete([Message(role="user", content="hi")])
    assert "0s" in str(info.value) or "no respondió" in str(info.value)


@pytest.mark.asyncio
async def test_sdk_timeout_is_transient_so_the_retry_policy_retries_it() -> None:
    """Un CLI encasquillado suele arreglarse volviendo a lanzarlo, así que el
    error tiene que clasificarse como transitorio para `shared_llm.retry`."""
    from shared_llm.retry import is_transient

    p = ClaudeAgentProvider(query_fn=_hanging_query(), timeout=0.05)
    with pytest.raises(ProviderError) as info:
        await p.complete([Message(role="user", content="hi")])
    assert is_transient(info.value) is True


@pytest.mark.asyncio
async def test_complete_with_tools_timeout_too() -> None:
    """El camino con tools drena el stream en OTRO sitio: verificar uno y
    extrapolar es justo lo que este plan prohíbe."""
    p = ClaudeAgentProvider(query_fn=_hanging_query(), timeout=0.05)
    with pytest.raises(ProviderError):
        await p.complete(
            [Message(role="user", content="hi")],
            tools=[{"type": "function", "function": {"name": "echo", "parameters": {}}}],
        )


@pytest.mark.asyncio
async def test_stream_timeout_too() -> None:
    p = ClaudeAgentProvider(query_fn=_hanging_query(), timeout=0.05)
    with pytest.raises(ProviderError):
        async for _chunk in p.stream([Message(role="user", content="hi")]):
            pass


@pytest.mark.asyncio
async def test_run_agent_timeout_too() -> None:
    p = ClaudeAgentProvider(query_fn=_hanging_query(), timeout=0.05)
    with pytest.raises(ProviderError):
        async for _event in p.run_agent("haz algo"):
            pass


@pytest.mark.asyncio
async def test_timeout_budget_is_per_message_not_per_call() -> None:
    """Un modelo lento que SIGUE emitiendo no debe morir por el timeout: el
    progreso reinicia el reloj. Sin esto, un timeout por-llamada mataría runs
    largos legítimos (y el operador subiría el valor hasta hacerlo inútil)."""

    async def _slow_but_alive(prompt: str, options: Any) -> AsyncIterator[Any]:
        for _ in range(4):
            await asyncio.sleep(0.02)
            yield _AssistantMessage(content=[_TextBlock(text="x")])

    # 4 mensajes × 0.02s = 0.08s TOTAL, por encima del presupuesto de 0.05s...
    p = ClaudeAgentProvider(query_fn=_slow_but_alive, timeout=0.05)
    resp = await p.complete([Message(role="user", content="hi")])
    assert resp.content == "xxxx"  # ...y no salta, porque hubo progreso


@pytest.mark.asyncio
async def test_healthy_call_unaffected_by_the_default_timeout() -> None:
    """No-regresión: sin colgarse, el presupuesto por defecto no se nota."""
    p = ClaudeAgentProvider(
        query_fn=_make_query(_AssistantMessage(content=[_TextBlock(text="ok")]))
    )
    resp = await p.complete([Message(role="user", content="hi")])
    assert resp.content == "ok"
