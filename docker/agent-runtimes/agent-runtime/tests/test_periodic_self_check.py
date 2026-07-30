"""ADR 0112 — reflexión semántica periódica durante el run (fase 1).

El `reflect` heurístico (regex, contadores) no incluye auto-evaluación del
MODELO. Fase 1 elegida: cada K iteraciones el loop inyecta un sticky
`self_check_nudge` que viaja en el turno NORMAL de decide() — el modelo
puntúa su progreso contra los criterios, actualiza su scratchpad
(`update_plan`) y, si lleva dos self-checks sin avanzar, cierra con
`submit_result` status='failed' explicando el bloqueo (escalado temprano).
Cero llamadas LLM extra (la llamada dedicada queda como fase 2 si la
telemetría lo pide).
"""

from __future__ import annotations

from agent_runtime.graph import _SELF_CHECK_EVERY, AgentDeps, _AgentLoop
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.model import ScriptedModelClient
from agent_runtime.providers import _decide_messages
from agent_runtime.safeguards import Budgets, SafeguardTracker
from agent_runtime.state import initial_state


def _loop() -> _AgentLoop:
    deps = AgentDeps(model=ScriptedModelClient(decisions=[], reviews=[]))
    return _AgentLoop(deps, SafeguardTracker(Budgets()), LoopDetector())


def _reflect_at(loop: _AgentLoop, iteration: int) -> dict[str, object]:
    state = initial_state({"id": "t", "title": "T", "description": ""})
    state["last_decision"] = {"tool": "noop", "tool_args": {}}
    state["last_observation"] = {"tool": "noop", "ok": True, "output": None, "error": None}
    for _ in range(iteration):
        loop.tracker.tick_iteration()
    return loop.reflect(state)


def test_self_check_fires_every_k_iterations() -> None:
    loop = _loop()
    updates = _reflect_at(loop, _SELF_CHECK_EVERY)
    nudge = updates.get("self_check_nudge")
    assert nudge, "a la iteración K el reflect debe inyectar el self-check"
    assert "SELF-CHECK" not in str(updates.get("guidance_nudge") or "")


def test_self_check_absent_off_cadence() -> None:
    loop = _loop()
    updates = _reflect_at(loop, _SELF_CHECK_EVERY - 1)
    # Fuera de cadencia el sticky se LIMPIA (None explícito, no ausente) para
    # que el self-check no presione todos los turnos siguientes.
    assert updates.get("self_check_nudge") is None


def test_self_check_renders_in_decide_messages() -> None:
    state = {
        "task": {"title": "T"},
        "context": [],
        "self_check_nudge": "score your progress against the acceptance criteria",
    }
    user = _decide_messages(state)[1].content
    assert "SELF-CHECK" in user
    assert "score your progress" in user


def test_self_check_not_rendered_when_absent() -> None:
    user = _decide_messages({"task": {"title": "T"}, "context": []})[1].content
    assert "SELF-CHECK" not in user
