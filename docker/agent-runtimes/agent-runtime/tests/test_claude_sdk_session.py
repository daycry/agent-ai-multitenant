"""ADR 0097 — el hilo conversacional por run también para claude_sdk.

El hilo (ADR 0110) es UNA capacidad con DOS transportes, no una vía del SDK:

  * HTTP (azure/copilot/ollama): el cliente re-envía el hilo de mensajes.
  * claude_sdk: el proveedor mantiene una SESIÓN SDK viva y solo se le manda el
    mensaje nuevo (el historial vive dentro de la sesión; re-enviarlo no
    reusaría nada porque el transporte es un CLI con estado propio).

Aquí se fija lo que ve el runtime: misma flag (`conversation_thread` del spec),
mismo contrato del grafo (un ACT por turno), y el review SIEMPRE one-shot (no
contamina la sesión del hilo).
"""

from __future__ import annotations

from typing import Any

from agent_runtime.providers import ClaudeSDKModelClient, build_provider_client
from shared_llm.providers.claude_agent_session import ClaudeAgentSessionProvider
from shared_llm.types import CompletionResponse, Message, Usage


class _RecordingProvider:
    """Doble del provider: registra (messages, kwargs) de cada complete()."""

    name = "claude_sdk"

    def __init__(self) -> None:
        self.calls: list[tuple[list[Message], dict[str, Any]]] = []

    async def complete(self, messages: Any, **kwargs: Any) -> CompletionResponse:
        self.calls.append((list(messages), dict(kwargs)))
        return CompletionResponse(
            content="<finish>hecho</finish>",
            model="m",
            provider=self.name,
            usage=Usage(input_tokens=1, output_tokens=1),
            tool_calls=None,
            raw=None,
        )

    async def aclose(self) -> None:
        return None


def _state(step: str) -> dict[str, Any]:
    return {
        "task": {"id": "t", "title": "T", "description": "D"},
        "context": [],
        "history": [],
        "last_observation": {"tool": "read_file", "ok": True, "output": step, "error": None},
        "progress_summary": f"progreso {step}",
    }


def _client(*, thread: bool) -> tuple[ClaudeSDKModelClient, _RecordingProvider]:
    async def _query(prompt: str, options: Any):  # noqa: ARG001 — seam sin SDK
        yield None

    client = ClaudeSDKModelClient(
        model="claude-sonnet-4-5",
        query_fn=_query,
        tools=[{"name": "read_file", "description": "lee", "parameters": {}}],
        conversation_thread=thread,
    )
    provider = _RecordingProvider()
    client.provider = provider  # type: ignore[assignment]
    return client, provider


def test_thread_off_rebuilds_the_full_prompt_every_turn() -> None:
    """Flag OFF = comportamiento histórico intacto (byte a byte)."""
    client, provider = _client(thread=False)
    client.decide(_state("uno"))
    client.decide(_state("dos"))
    first, second = provider.calls[0][0], provider.calls[1][0]
    assert len(first) == len(second) == 2  # [system, user] reconstruido cada turno
    assert "conversation_session" not in provider.calls[1][1]


def test_thread_on_keeps_the_sdk_session_and_sends_only_the_new_turn() -> None:
    client, provider = _client(thread=True)
    client.decide(_state("uno"))
    client.decide(_state("dos"))

    # Turno 1: el contexto completo (abre la sesión).
    assert provider.calls[0][1]["conversation_session"] is True
    # Turno 2: el mensaje NUEVO es el último y solo lleva la observación +
    # stickies — el historial ya vive en la sesión del SDK.
    second_messages, second_kwargs = provider.calls[1]
    assert second_kwargs["conversation_session"] is True
    last = second_messages[-1]
    assert last.role == "user"
    assert "dos" in last.content, "la observación del turno anterior viaja en el mensaje nuevo"
    assert "progreso dos" in last.content
    assert "D" not in last.content.replace("dos", ""), "no re-pega la descripción de la tarea"


def test_review_never_rides_the_session() -> None:
    """El review es one-shot: entra por otro camino y NO contamina el hilo."""
    client, provider = _client(thread=True)
    client.decide(_state("uno"))
    client.review(
        {
            "task": {"id": "t", "title": "T", "description": "D"},
            "artifacts": [],
            "history": [],
            "acceptance_criteria": [],
        }
    )
    assert "conversation_session" not in provider.calls[-1][1]


def test_the_session_provider_is_only_built_with_the_flag_on() -> None:
    """El spec manda: sin flag, el provider one-shot histórico."""
    spec = {"kind": "claude_sdk", "model": "claude-sonnet-4-5", "oauth_token": "x"}
    plain = build_provider_client(spec)
    threaded = build_provider_client({**spec, "conversation_thread": True})
    assert not isinstance(plain.provider, ClaudeAgentSessionProvider)  # type: ignore[attr-defined]
    assert isinstance(threaded.provider, ClaudeAgentSessionProvider)  # type: ignore[attr-defined]
    assert threaded._conversation_thread is True  # type: ignore[attr-defined]


def test_closing_the_client_tears_down_the_live_session() -> None:
    """El run acaba → la sesión (y su CLI) se cierran: nada colgando."""
    client, provider = _client(thread=True)
    closed: list[bool] = []

    async def _aclose() -> None:
        closed.append(True)

    provider.aclose = _aclose  # type: ignore[method-assign]
    client.close()
    assert closed == [True]
