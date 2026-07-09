"""Córtex F1 — grafo reactivo ``CortexState`` + ``run_cortex_turn`` (clon del loop).

Espejo de ``test_assistant_tool_caps.py`` / ``test_assistant_memory`` chat-flow:
el grafo del córtex reutiliza el turn-loop ``decide→run_tools→decide→answer`` del
asistente (mismos topes: ``MAX_TOOL_ROUNDS``, cap 1/turno para la escritura de
memoria). Con un ``ScriptedAssistantModel`` que llama ``cortex_remember`` y luego
responde, ``run_cortex_turn`` devuelve la respuesta y ``tools_called`` contiene
``cortex_remember``; y un modelo que re-llama ``cortex_remember`` 3 veces con args
distintos se capa a 1 (reusa el cap real del asistente).

El ``CortexToolContext`` se inyecta con una sesión FAKE que registra las escrituras
(la persistencia real vive en los tests de integración) — este test es UNIT y solo
verifica el cableado del grafo + los topes, sin tocar la BD.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import pytest
from api_server.assistant.graph import (
    ModelTurn,
    ScriptedAssistantModel,
    ToolInvocation,
)
from api_server.cortex.graph import CortexState, run_cortex_turn
from api_server.cortex.tools import (
    CortexToolContext,
    cortex_tool_schemas,
    run_cortex_tool,
)

pytestmark = pytest.mark.unit

WRITE_TOOL = "cortex_remember"
READ_TOOL = "cortex_recall_more"


@dataclass
class _FakeSession:
    """Records cortex_remember writes; no DB. The tool calls our monkeypatched
    ``cortex.memory.cortex_remember`` so we never touch SQLAlchemy here."""

    writes: list[str] = field(default_factory=list)


def _ctx() -> CortexToolContext:
    return CortexToolContext(
        session=_FakeSession(),  # type: ignore[arg-type]
        owner_user_id=uuid4(),
        tenant_id=uuid4(),
    )


@pytest.fixture(autouse=True)
def _stub_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub the memory layer so the graph tools are exercised without a DB."""

    async def _fake_remember(session, *, owner_user_id, tenant_id, content, **_: Any):
        session.writes.append(content)
        return {"stored": True, "id": str(uuid4())}

    async def _fake_recall(session, *, owner_user_id, tenant_id, query, **_: Any):
        return ["un recuerdo recallado"]

    monkeypatch.setattr("api_server.cortex.tools.cortex_remember", _fake_remember)
    monkeypatch.setattr("api_server.cortex.tools.cortex_recall", _fake_recall)


@pytest.mark.asyncio
async def test_tool_failure_does_not_crash_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Una tool que LANZA (p.ej. web_fetch con el egress bloqueado) NO debe
    tumbar el turno: el error se devuelve al modelo como resultado de la tool
    y el modelo responde igualmente (con lo que ya tiene). Visto en vivo: un
    web_fetch fallido mataba la voz del córtex con «voice turn failed» en vez
    de responder desde los resultados de búsqueda."""

    async def _boom(session, *, owner_user_id, tenant_id, query, **_: Any):
        raise RuntimeError("All connection attempts failed")

    monkeypatch.setattr("api_server.cortex.tools.cortex_recall", _boom)

    model = ScriptedAssistantModel(
        turns=[
            ModelTurn(tool_calls=(ToolInvocation(name=READ_TOOL, arguments={"query": "tiempo"}),)),
            ModelTurn(content="Según lo que sé, hace sol."),
        ]
    )
    ctx = _ctx()
    result = await run_cortex_turn(
        model,
        system_prompt="Eres el córtex.",
        enabled_tools=(WRITE_TOOL, READ_TOOL),
        tool_ctx=ctx,
        chat_history=[{"role": "user", "content": "¿qué tiempo hace?"}],
    )
    # El turno SOBREVIVE y responde; la tool que falló se registra igualmente.
    assert result.content == "Según lo que sé, hace sol."
    assert READ_TOOL in result.tools_called


@pytest.mark.asyncio
async def test_run_cortex_turn_calls_remember_then_answers() -> None:
    model = ScriptedAssistantModel(
        turns=[
            ModelTurn(
                tool_calls=(
                    ToolInvocation(
                        name=WRITE_TOOL,
                        arguments={"content": "Al owner le interesa la arquitectura hexagonal"},
                    ),
                )
            ),
            ModelTurn(content="Anotado. ¿Seguimos con el diseño?"),
        ]
    )
    ctx = _ctx()
    result = await run_cortex_turn(
        model,
        system_prompt="Eres el córtex.",
        enabled_tools=(WRITE_TOOL, READ_TOOL),
        tool_ctx=ctx,
        chat_history=[{"role": "user", "content": "me interesa la arquitectura hexagonal"}],
    )
    assert result.content == "Anotado. ¿Seguimos con el diseño?"
    assert WRITE_TOOL in result.tools_called
    assert ctx.session.writes == ["Al owner le interesa la arquitectura hexagonal"]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_run_cortex_turn_caps_remember_to_once_per_turn() -> None:
    """An over-eager model re-calling cortex_remember 3 times (distinct args) must
    run it exactly ONCE — reusing the assistant's per-tool cap (1/turn)."""
    turns = [
        ModelTurn(tool_calls=(ToolInvocation(name=WRITE_TOOL, arguments={"content": f"dato {i}"}),))
        for i in range(3)
    ]
    turns.append(ModelTurn(content="Listo."))
    model = ScriptedAssistantModel(turns=turns)
    ctx = _ctx()
    result = await run_cortex_turn(
        model,
        system_prompt="Eres el córtex.",
        enabled_tools=(WRITE_TOOL,),
        tool_ctx=ctx,
        chat_history=[],
    )
    assert result.tools_called.count(WRITE_TOOL) == 1
    assert len(ctx.session.writes) == 1  # type: ignore[union-attr]


def test_cortex_state_is_a_dataclass_with_tool_ctx() -> None:
    """CortexState carries the same primitives AssistantState does + a CortexToolContext."""
    ctx = _ctx()
    state = CortexState(system_prompt="x", enabled_tools=(WRITE_TOOL,), tool_ctx=ctx)
    assert state.system_prompt == "x"
    assert state.enabled_tools == (WRITE_TOOL,)
    assert state.tool_ctx is ctx


def test_cortex_tool_schemas_lists_enabled_only() -> None:
    schemas = cortex_tool_schemas((WRITE_TOOL,))
    names = [s["name"] for s in schemas]
    assert names == [WRITE_TOOL]


@pytest.mark.asyncio
async def test_run_cortex_tool_dispatches_recall(monkeypatch: pytest.MonkeyPatch) -> None:
    ctx = _ctx()
    out = await run_cortex_tool(READ_TOOL, ctx, {"query": "arquitectura"})
    assert out["memories"] == ["un recuerdo recallado"]
