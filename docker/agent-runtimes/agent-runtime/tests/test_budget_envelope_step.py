"""El sobre de presupuesto viaja al visor (G11, plan `guardas-research-por-novedad`).

El aviso de «te quedan N iteraciones» existía **solo dentro del prompt**: el
modelo lo veía y el operador no. En el visor se mostraba lo gastado sin techo
contra el que compararlo, que es como no mostrar nada — 12 iteraciones son
tranquilizadoras si el tope es 50 y una urgencia si es 15.

Va en el PRIMER step, no en `finalize` (donde vive `safeguard_stats`), porque el
caso de uso es un run EN CURSO: en finalize llegaría cuando ya no sirve para
decidir si intervenir.
"""

from __future__ import annotations

from typing import Any

from agent_runtime.graph import AgentDeps, _AgentLoop
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.safeguards import Budgets, SafeguardTracker
from agent_runtime.state import initial_state


class _Model:
    def decide(self, state: dict) -> Any:  # noqa: ARG002  # pragma: no cover
        raise AssertionError

    def review(self, state: dict) -> Any:  # noqa: ARG002  # pragma: no cover
        raise AssertionError


class _NoTools:
    def call(self, tool: str, args: dict) -> Any:  # noqa: ARG002  # pragma: no cover
        raise AssertionError


def _recall_step(budgets: Budgets) -> dict[str, Any]:
    deps = AgentDeps(model=_Model(), tools=_NoTools(), guardrails=None)  # type: ignore[arg-type]
    loop = _AgentLoop(deps, SafeguardTracker(budgets), LoopDetector())
    state = dict(initial_state({"title": "t", "description": ""}))
    state["steps"] = []
    out = loop.recall(state)  # type: ignore[arg-type]
    return out["steps"][0]


def test_the_first_step_carries_the_budget_envelope() -> None:
    step = _recall_step(Budgets(max_iterations=30, max_tokens=200_000))
    assert step["budgets"]["max_iterations"] == 30
    assert step["budgets"]["max_tokens"] == 200_000


def test_it_carries_the_envelope_this_run_actually_got() -> None:
    # Recalcularlo al LEER daría el presupuesto configurado hoy, no el que rigió
    # el run: en cuanto el operador lo cambiase, el visor mentiría sobre runs
    # pasados.
    step = _recall_step(Budgets(max_iterations=7))
    assert step["budgets"]["max_iterations"] == 7


def test_the_envelope_carries_what_the_viewer_needs_to_subtract() -> None:
    # El visor recibe gastado (`iterations`, `total_tokens`, `tool_call_count`,
    # coste) en vivo desde la fila de la ejecución; aquí van sus techos.
    step = _recall_step(Budgets())
    assert set(step["budgets"]) == {
        "max_iterations",
        "max_tokens",
        "max_cost_usd",
        "max_tool_calls",
    }


def test_the_envelope_is_json_serialisable() -> None:
    # Va a `steps_log`, que es JSONB: un dataclass ahí dentro rompería el
    # guardado del run entero.
    import json

    json.dumps(_recall_step(Budgets())["budgets"])
