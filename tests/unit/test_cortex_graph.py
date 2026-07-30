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
    FINISH_NUDGE,
    AssistantState,
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
async def test_reasoning_preamble_of_a_tool_turn_never_becomes_the_answer() -> None:
    """El `content` de un turno que PIDE una tool es preámbulo/razonamiento del
    modelo (p.ej. gpt-oss emite «We need to use web_search»), NO una respuesta.
    Si el turno final tras la tool sale VACÍO, la respuesta NUNCA debe ser ese
    preámbulo — visto en vivo: el córtex «respondía» «We need to use
    web_search» (además en inglés). Mejor una respuesta vacía que el
    pensamiento crudo."""
    model = ScriptedAssistantModel(
        turns=[
            ModelTurn(
                content="We need to use web_search.",
                tool_calls=(ToolInvocation(name=READ_TOOL, arguments={"query": "tiempo"}),),
            ),
            ModelTurn(content=""),  # el modelo no produjo respuesta tras la tool
        ]
    )
    result = await run_cortex_turn(
        model,
        system_prompt="Eres el córtex.",
        enabled_tools=(WRITE_TOOL, READ_TOOL),
        tool_ctx=_ctx(),
        chat_history=[{"role": "user", "content": "¿qué tiempo hace?"}],
    )
    assert "web_search" not in result.content
    assert result.content == ""  # sin respuesta real → vacío, jamás el preámbulo


@pytest.mark.asyncio
async def test_real_answer_after_tool_is_used_not_the_preamble() -> None:
    """El caso feliz: el preámbulo del turno-tool se ignora y se usa la
    respuesta real del turno posterior."""
    model = ScriptedAssistantModel(
        turns=[
            ModelTurn(
                content="We need to use web_search.",
                tool_calls=(ToolInvocation(name=READ_TOOL, arguments={"query": "tiempo"}),),
            ),
            ModelTurn(content="En Barcelona hace sol, 28 grados."),
        ]
    )
    result = await run_cortex_turn(
        model,
        system_prompt="Eres el córtex.",
        enabled_tools=(WRITE_TOOL, READ_TOOL),
        tool_ctx=_ctx(),
        chat_history=[{"role": "user", "content": "¿qué tiempo hace?"}],
    )
    assert result.content == "En Barcelona hace sol, 28 grados."


@dataclass
class _RecordingModel:
    """Modelo scripted que ADEMÁS registra el estado que recibe cada ``decide``,
    para poder afirmar qué se le pasó en el turno de cierre (finish)."""

    turns: list[ModelTurn]
    seen: list[AssistantState] = field(default_factory=list)
    _cursor: int = 0

    async def decide(self, state: AssistantState) -> ModelTurn:
        self.seen.append(state)
        index = min(self._cursor, len(self.turns) - 1)
        self._cursor += 1
        return self.turns[index]


@pytest.mark.asyncio
async def test_finish_reask_forces_prose_with_a_nudge() -> None:
    """Modelo de razonamiento (gpt-oss:120b) que se queda pidiendo tools sin
    comprometer NUNCA una respuesta: llama a la tool, y el turno siguiente vuelve
    a pedir la MISMA tool (deduplicada) con content vacío → el loop llega a finish
    sin respuesta. Antes eso devolvía answer="" (visto en vivo con «¿últimas
    noticias de tecnología hoy?»). Ahora el turno de finish re-pregunta SIN tools y
    con una orden imperativa (``FINISH_NUDGE``) que fuerza la redacción final."""
    model = _RecordingModel(
        turns=[
            ModelTurn(
                content="We need to search the web.",
                tool_calls=(ToolInvocation(name=READ_TOOL, arguments={"query": "noticias"}),),
            ),
            # Misma firma → deduplicada → kept vacío → el loop enruta a finish.
            ModelTurn(
                content="",
                tool_calls=(ToolInvocation(name=READ_TOOL, arguments={"query": "noticias"}),),
            ),
            # Re-ask del finish: ahora sí redacta.
            ModelTurn(content="Hoy destacan tres noticias de tecnología."),
        ]
    )
    result = await run_cortex_turn(
        model,
        system_prompt="Eres el córtex.",
        enabled_tools=(READ_TOOL,),
        tool_ctx=_ctx(),
        chat_history=[{"role": "user", "content": "¿últimas noticias de tecnología hoy?"}],
    )
    assert result.content == "Hoy destacan tres noticias de tecnología."
    # El ÚLTIMO decide es el re-ask del finish: sin tools y con la orden imperativa.
    finish_state = model.seen[-1]
    assert finish_state.enabled_tools == ()
    assert finish_state.final_instruction == FINISH_NUDGE


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
