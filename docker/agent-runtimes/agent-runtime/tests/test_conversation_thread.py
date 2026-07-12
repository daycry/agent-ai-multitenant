"""ADR 0110 (mitad HTTP, flag OFF) — hilo conversacional en memoria por run.

Cada decide() reconstruía [system, user] desde cero: el modelo nunca veía su
historial real y el KV-cache del proveedor se invalidaba turno a turno. Con
``AGENT_CONVERSATION_THREAD=1`` (env del contenedor; el worker lo emite solo
si ``WORKERS_RUNTIME_CONVERSATION_THREAD`` está activo — default OFF), el
cliente HTTP acumula el hilo: primer turno = rebuild histórico; siguientes =
[system] + hilo + un TURN UPDATE compacto (observación + stickies). Con el
flag apagado, byte-a-byte el comportamiento previo.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agent_runtime.providers import _ProviderModelClient


class _RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[list[Any]] = []

    async def complete(self, messages: Any, **kwargs: Any) -> Any:  # noqa: ARG002
        self.calls.append(list(messages))
        return SimpleNamespace(
            content="",
            tool_calls=[SimpleNamespace(id="c1", name="read_file", arguments={"path": "a.py"})],
            model="m",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1, cost_usd=0.0),
            raw=None,
            stop_reason=None,
        )

    async def aclose(self) -> None:  # pragma: no cover
        return


def _state(iteration: int) -> dict[str, Any]:
    return {
        "task": {"title": "Tarea X", "description": "haz X"},
        "context": [{"role": "observation", "tool": "read_file", "ok": True}],
        "last_observation": {"tool": "read_file", "ok": True, "output": f"contenido {iteration}"},
        "iteration": iteration,
        "guidance_nudge": "no repitas lecturas",
    }


def test_thread_off_by_default_rebuilds_every_turn() -> None:
    provider = _RecordingProvider()
    client = _ProviderModelClient(provider=provider, model="m")
    client.decide(_state(1))
    client.decide(_state(2))
    # Sin flag: dos llamadas con el rebuild histórico (2 mensajes cada una).
    assert [len(call) for call in provider.calls] == [2, 2]


def test_thread_accumulates_when_enabled() -> None:
    provider = _RecordingProvider()
    client = _ProviderModelClient(provider=provider, model="m", conversation_thread=True)
    client.decide(_state(1))
    client.decide(_state(2))
    first, second = provider.calls
    assert len(first) == 2  # primer turno = rebuild completo
    # Segundo turno: [system, user1, assistant1, user2] — historial REAL.
    assert len(second) == 4
    assert [m.role for m in second] == ["system", "user", "assistant", "user"]
    # El assistant grabado refleja la acción del turno anterior.
    assert "read_file" in second[2].content
    # El turn update es COMPACTO: lleva la observación y stickies, no re-pega
    # el bloque completo de la tarea.
    assert "contenido 2" in second[3].content
    assert "no repitas lecturas" in second[3].content
    # Y el user1 del hilo es el original con la tarea.
    assert "Tarea X" in second[1].content


def test_thread_compacts_beyond_cap() -> None:
    provider = _RecordingProvider()
    client = _ProviderModelClient(provider=provider, model="m", conversation_thread=True)
    for i in range(30):
        client.decide(_state(i))
    last = provider.calls[-1]
    # [system] + hilo acotado + turn update — nunca crece sin límite.
    assert len(last) <= 2 + client._THREAD_MAX_MESSAGES
    # La compactación deja rastro honesto de lo evictado.
    assert any("EARLIER TURNS" in m.content for m in last if m.role == "user")
