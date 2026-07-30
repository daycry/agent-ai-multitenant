"""Intervención humana sobre un run EN MARCHA (`task_wf_71`).

La única intervención posible era **matar** el run: si el agente iba por mal
camino se tiraba todo el trabajo hecho y se relanzaba a ciegas, con el mismo
prompt que ya había fallado.

Ahora una persona puede redirigirlo desde el visor. El bucle consulta la guía
una vez por iteración y la inyecta como sticky del turno siguiente.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.graph import AgentDeps, _AgentLoop
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.model import DecisionKind, ModelDecision, ModelResponse
from agent_runtime.safeguards import Budgets, SafeguardTracker
from agent_runtime.state import initial_state


class _Model:
    def decide(self, state: dict) -> ModelResponse:  # noqa: ARG002
        return ModelResponse(
            decision=ModelDecision(kind=DecisionKind.FINISH, rationale="ok", output="x"),
            model="m",
        )

    def review(self, state: dict) -> object:  # noqa: ARG002  # pragma: no cover
        raise AssertionError


class _NoTools:
    def call(self, tool: str, args: dict) -> object:  # noqa: ARG002  # pragma: no cover
        raise AssertionError


def _loop(poll: Any) -> _AgentLoop:
    deps = AgentDeps(model=_Model(), tools=_NoTools(), guardrails=None, guidance_poll=poll)  # type: ignore[arg-type]
    return _AgentLoop(deps, SafeguardTracker(Budgets()), LoopDetector())


def _state() -> dict[str, Any]:
    state = dict(initial_state({"title": "t", "description": ""}))
    state["steps"] = []
    return state


def test_the_guidance_becomes_a_sticky_for_the_next_turn() -> None:
    out = _loop(lambda: "no toques el esquema, usa el adaptador de legacy.py").plan(_state())
    assert out["human_guidance"] == "no toques el esquema, usa el adaptador de legacy.py"


def test_the_guidance_shows_up_in_the_run_timeline() -> None:
    # El operador tiene que poder confirmar que su corrección LLEGÓ; si no, no
    # sabe si repetirla.
    out = _loop(lambda: "cambia de enfoque").plan(_state())
    summaries = " ".join(str(step.get("summary") or "") for step in out["steps"])
    assert "Human guidance received" in summaries


def test_it_reaches_the_model_prompt_above_the_automatic_nudge() -> None:
    # Si el modelo tiene que elegir entre lo que le empuja la heurística y lo
    # que le acaba de decir una persona, gana la persona.
    from agent_runtime.providers import _decide_messages

    state = _state()
    state["human_guidance"] = "PARA de leer y escribe el test"
    state["guidance_nudge"] = "llevas varios turnos leyendo"
    prompt = "\n".join(m.content for m in _decide_messages(state))  # type: ignore[arg-type]
    assert "HUMAN OPERATOR INSTRUCTION" in prompt
    assert prompt.index("HUMAN OPERATOR INSTRUCTION") < prompt.index("GUIDANCE:")


def test_no_guidance_leaves_the_turn_untouched() -> None:
    out = _loop(lambda: None).plan(_state())
    assert out["human_guidance"] is None
    assert not any("Human guidance" in str(s.get("summary") or "") for s in out["steps"])


def test_a_run_without_the_poll_behaves_exactly_as_before() -> None:
    # Bare run / sin API interna: la feature no existe y el bucle es el de antes.
    out = _loop(None).plan(_state())
    assert out["human_guidance"] is None


def test_a_failing_poll_never_breaks_the_run() -> None:
    # Es una comodidad del operador: que el api-server no conteste no puede
    # tumbar un run que está trabajando.
    def _boom() -> str | None:
        raise RuntimeError("api-server caído")

    out = _loop(_boom).plan(_state())
    assert out["human_guidance"] is None
    assert out["last_decision"]["kind"] == "finish"
