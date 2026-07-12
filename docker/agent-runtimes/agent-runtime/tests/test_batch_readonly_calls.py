"""ADR 0111 — batch de tool calls READ-ONLY en un mismo turno.

F36 descartaba todo tool call más allá del primero: batch-leer 4 ficheros
costaba 4 iteraciones del presupuesto (50 máx.). Con el ADR 0111, cuando el
modelo emite VARIOS calls y el primero es read-only, el prefijo consecutivo
de calls read-only (cap 4 en total) viaja como lote en la decisión y el nodo
`act` los ejecuta todos en la misma iteración, agregando los resultados en
una sola observación. Los MUTADORES siguen siendo de a uno (semántica de
una-acción intacta: el lote se corta en el primer call no read-only).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agent_runtime.graph import AgentDeps, _AgentLoop
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.model import DecisionKind, ScriptedModelClient
from agent_runtime.providers import _decision_from
from agent_runtime.safeguards import Budgets, SafeguardTracker
from agent_runtime.state import initial_state
from agent_runtime.tools import ToolRegistry, ToolResult


def _resp(*, tool_calls: Any = None, content: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        tool_calls=tool_calls or [],
        content=content,
        model="m",
        usage=SimpleNamespace(input_tokens=1, output_tokens=2, cost_usd=0.0),
        raw=None,
        stop_reason=None,
    )


def _call(name: str, **args: Any) -> SimpleNamespace:
    return SimpleNamespace(id="c1", name=name, arguments=args)


# --- capa de decisión (_decision_from) ---------------------------------------


def test_readonly_prefix_becomes_batch() -> None:
    resp = _resp(
        tool_calls=[
            _call("read_file", path="a.py"),
            _call("read_file", path="b.py"),
            _call("list_files", path="src"),
        ]
    )
    decision = _decision_from(resp, model="m").decision
    assert decision.kind == DecisionKind.ACT
    assert decision.tool == "read_file"
    assert decision.tool_args == {"path": "a.py"}
    assert decision.batch_calls == (
        {"tool": "read_file", "args": {"path": "b.py"}},
        {"tool": "list_files", "args": {"path": "src"}},
    )


def test_batch_stops_at_first_mutator() -> None:
    # [read, write, read] → solo el prefijo read-only entra al lote; el write y
    # lo que le sigue se descartan (semántica de una-acción para mutadores).
    resp = _resp(
        tool_calls=[
            _call("read_file", path="a.py"),
            _call("write_file", path="b.py", content="x"),
            _call("read_file", path="c.py"),
        ]
    )
    decision = _decision_from(resp, model="m").decision
    assert decision.tool == "read_file"
    assert decision.batch_calls == ()


def test_mutator_first_keeps_single_action() -> None:
    resp = _resp(
        tool_calls=[
            _call("write_file", path="a.py", content="x"),
            _call("read_file", path="b.py"),
        ]
    )
    decision = _decision_from(resp, model="m").decision
    assert decision.tool == "write_file"
    assert decision.batch_calls == ()


def test_batch_is_capped() -> None:
    resp = _resp(tool_calls=[_call("read_file", path=f"f{i}.py") for i in range(8)])
    decision = _decision_from(resp, model="m").decision
    # cap total 4: el principal + 3 extras.
    assert decision.tool == "read_file"
    assert len(decision.batch_calls) == 3


def test_single_call_has_no_batch() -> None:
    resp = _resp(tool_calls=[_call("read_file", path="a.py")])
    decision = _decision_from(resp, model="m").decision
    assert decision.batch_calls == ()


def test_decision_as_dict_carries_batch() -> None:
    resp = _resp(
        tool_calls=[
            _call("read_file", path="a.py"),
            _call("read_file", path="b.py"),
        ]
    )
    as_dict = _decision_from(resp, model="m").decision.as_dict()
    assert as_dict["batch_calls"] == [{"tool": "read_file", "args": {"path": "b.py"}}]


# --- nodo act: ejecución del lote ---------------------------------------------


def _registry(reads: dict[str, str]) -> ToolRegistry:
    registry = ToolRegistry()

    def _read(args: dict[str, Any]) -> ToolResult:
        path = str(args.get("path"))
        if path not in reads:
            return ToolResult(ok=False, error=f"FileNotFoundError: {path}")
        return ToolResult(ok=True, output=reads[path])

    registry.register("read_file", _read)
    return registry


def _loop(reads: dict[str, str]) -> _AgentLoop:
    deps = AgentDeps(
        model=ScriptedModelClient(decisions=[], reviews=[]),
        tools=_registry(reads),
    )
    return _AgentLoop(deps, SafeguardTracker(Budgets()), LoopDetector())


def test_act_executes_batch_and_aggregates_observation() -> None:
    loop = _loop({"a.py": "AAA", "b.py": "BBB"})
    state = initial_state({"id": "t", "title": "T", "description": ""})
    state["last_decision"] = {
        "tool": "read_file",
        "tool_args": {"path": "a.py"},
        "batch_calls": [
            {"tool": "read_file", "args": {"path": "b.py"}},
            {"tool": "read_file", "args": {"path": "missing.py"}},
        ],
    }
    delta = loop.act(state)
    observation = delta["last_observation"]
    # El principal conserva su shape histórico…
    assert observation["tool"] == "read_file"
    assert observation["ok"] is True
    assert observation["output"] == "AAA"
    # …y el lote viaja agregado, con error POR ELEMENTO (no tumba el turno).
    batch = observation["batch"]
    assert len(batch) == 2
    assert batch[0]["ok"] is True
    assert batch[0]["output"] == "BBB"
    assert batch[1]["ok"] is False
    # Un step tool_call por elemento (el visor de runs los muestra todos).
    tool_steps = [s for s in delta["steps"] if s["kind"] == "tool_call"]
    assert len(tool_steps) == 3
    # El presupuesto de tool_calls cuenta cada elemento del lote.
    assert loop.tracker.usage.tool_calls == 3


def test_batch_reads_register_novelty_per_element() -> None:
    loop = _loop({"a.py": "AAA", "b.py": "BBB"})
    state = initial_state({"id": "t", "title": "T", "description": ""})
    state["last_decision"] = {
        "tool": "read_file",
        "tool_args": {"path": "a.py"},
        "batch_calls": [{"tool": "read_file", "args": {"path": "b.py"}}],
    }
    delta = loop.act(state)
    state.update({"last_observation": delta["last_observation"], "steps": list(delta["steps"])})
    loop.reflect(state)
    # Ambos targets cuentan como explorados (novedad por elemento del lote).
    assert "read_file:a.py" in loop.read_targets
    assert "read_file:b.py" in loop.read_targets


def test_act_without_batch_unchanged() -> None:
    loop = _loop({"a.py": "AAA"})
    state = initial_state({"id": "t", "title": "T", "description": ""})
    state["last_decision"] = {"tool": "read_file", "tool_args": {"path": "a.py"}}
    delta = loop.act(state)
    observation = delta["last_observation"]
    assert observation["ok"] is True
    assert "batch" not in observation
    assert loop.tracker.usage.tool_calls == 1
