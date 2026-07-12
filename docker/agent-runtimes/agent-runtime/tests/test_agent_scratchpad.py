"""P1-6 (investigación 2026-07-11): el scratchpad del agente (update_plan).

El nodo `plan` solo pide UNA acción por turno y la reconstrucción single-turn
del prompt hacía que el agente no tuviera memoria de su propia estrategia salvo
lo que cupiera en la ventana de 8 items. `update_plan` es una capacidad del
LOOP (interceptada en `act`, no una tool del registry): guarda la estrategia
como sticky `agent_plan` que `_decide_messages` renderiza TODOS los turnos.
"""

from __future__ import annotations

from agent_runtime.providers import _decide_messages


def test_agent_plan_sticky_renders_every_turn() -> None:
    state = {
        "task": {"title": "T"},
        "agent_plan": "1) leer modelo 2) escribir endpoint 3) tests",
        "context": [],
    }
    user = _decide_messages(state)[1].content
    assert "YOUR PLAN" in user
    assert "escribir endpoint" in user


def test_without_plan_no_block() -> None:
    user = _decide_messages({"task": {"title": "T"}, "context": []})[1].content
    assert "YOUR PLAN" not in user


def test_act_intercepts_update_plan_and_stores_sticky() -> None:
    from agent_runtime.graph import AgentDeps, _AgentLoop
    from agent_runtime.loop_detection import LoopDetector
    from agent_runtime.model import ScriptedModelClient
    from agent_runtime.safeguards import Budgets, SafeguardTracker
    from agent_runtime.state import initial_state

    deps = AgentDeps(model=ScriptedModelClient(decisions=[], reviews=[]))
    loop = _AgentLoop(deps, SafeguardTracker(Budgets()), LoopDetector())
    state = initial_state({"id": "t", "title": "T", "description": ""})
    state["last_decision"] = {
        "tool": "update_plan",
        "tool_args": {"plan": "primero A, luego B"},
    }
    delta = loop.act(state)
    assert delta["agent_plan"] == "primero A, luego B"
    assert delta["last_observation"]["ok"] is True


def test_update_plan_empty_keeps_previous_plan() -> None:
    from agent_runtime.graph import AgentDeps, _AgentLoop
    from agent_runtime.loop_detection import LoopDetector
    from agent_runtime.model import ScriptedModelClient
    from agent_runtime.safeguards import Budgets, SafeguardTracker
    from agent_runtime.state import initial_state

    deps = AgentDeps(model=ScriptedModelClient(decisions=[], reviews=[]))
    loop = _AgentLoop(deps, SafeguardTracker(Budgets()), LoopDetector())
    state = initial_state({"id": "t", "title": "T", "description": ""})
    state["agent_plan"] = "plan previo"
    state["last_decision"] = {"tool": "update_plan", "tool_args": {"plan": "  "}}
    delta = loop.act(state)
    assert delta["agent_plan"] == "plan previo"
    assert delta["last_observation"]["ok"] is False
