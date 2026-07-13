"""ADR 0112 fase 2 — mini-turno DEDICADO de reflexión + escalado determinista.

La fase 1 (sticky self-check) depende de que el modelo obedezca. La fase 2,
gated por flag (OFF por defecto), añade: cada K iteraciones una llamada LLM
DEDICADA con veredicto ESTRUCTURADO (tool_choice forzado a `submit_progress`:
{score 0-10, stuck, reason}) y, tras 2 veredictos «stuck» consecutivos, el
loop escala DETERMINISTA (needs_human_review si produjo; aborted si estéril)
con abort_code `reflection_stalled` — sin depender de la obediencia del
modelo. Solo providers HTTP (tool_choice); best-effort: un assess roto jamás
rompe el run.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from agent_runtime.graph import AgentDeps, _AgentLoop
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.model import DecisionKind, ModelDecision, ModelResponse, ScriptedModelClient
from agent_runtime.providers import _SUBMIT_PROGRESS_TOOL, _ProviderModelClient
from agent_runtime.safeguards import Budgets, SafeguardTracker
from agent_runtime.state import initial_state


class _AssessProvider:
    def __init__(self, *, stuck: bool) -> None:
        self.stuck = stuck
        self.calls: list[dict[str, Any]] = []

    async def complete(self, messages: Any, **kwargs: Any) -> Any:  # noqa: ARG002
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            content="",
            tool_calls=[
                SimpleNamespace(
                    id="p1",
                    name="submit_progress",
                    arguments={"score": 1 if self.stuck else 8, "stuck": self.stuck},
                )
            ],
            model="m",
            usage=SimpleNamespace(input_tokens=1, output_tokens=1, cost_usd=0.0),
            raw=None,
            stop_reason=None,
        )

    async def aclose(self) -> None:  # pragma: no cover
        return


def test_assess_progress_forces_structured_tool() -> None:
    provider = _AssessProvider(stuck=True)
    client = _ProviderModelClient(provider=provider, model="m")
    verdict = client.assess_progress({"task": {"title": "T"}, "context": []})
    assert verdict is not None
    assert verdict["stuck"] is True
    assert verdict["score"] == 1
    # La llamada fuerza el veredicto estructurado (tool_choice + solo esa tool).
    kwargs = provider.calls[0]
    assert kwargs["tools"] == [_SUBMIT_PROGRESS_TOOL]
    assert kwargs["tool_choice"]["function"]["name"] == "submit_progress"


class _StuckAssessModel(ScriptedModelClient):
    """Modelo scripted cuyo assess dedicado SIEMPRE se declara estancado."""

    def __init__(self) -> None:
        super().__init__(
            decisions=[
                ModelResponse(
                    decision=ModelDecision(
                        kind=DecisionKind.ACT, tool="noop", tool_args={"reason": "loop"}
                    )
                )
            ]
        )
        self.assess_calls = 0

    def assess_progress(self, state: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
        self.assess_calls += 1
        return {"score": 1, "stuck": True, "reason": "sin avance"}


def test_two_stuck_assessments_escalate_deterministically() -> None:
    model = _StuckAssessModel()
    deps = AgentDeps(model=model, reflection_assess_every=1)
    loop = _AgentLoop(deps, SafeguardTracker(Budgets(max_iterations=20)), LoopDetector())
    state = initial_state({"id": "t", "title": "T", "description": ""})
    state["last_decision"] = {"tool": "noop", "tool_args": {}}
    state["last_observation"] = {"tool": "noop", "ok": True, "output": None, "error": None}

    # Dos reflects en cadencia → dos veredictos stuck consecutivos.
    loop.tracker.tick_iteration()
    loop.reflect(state)
    loop.tracker.tick_iteration()
    loop.reflect(state)
    assert model.assess_calls == 2

    # El siguiente plan ESCALA determinista (estéril → aborted).
    delta = loop.plan(state)
    assert delta["status"] == "aborted"
    assert delta["abort_code"] == "reflection_stalled"


def test_assess_off_by_default() -> None:
    model = _StuckAssessModel()
    deps = AgentDeps(model=model)  # sin reflection_assess_every → OFF
    loop = _AgentLoop(deps, SafeguardTracker(Budgets()), LoopDetector())
    state = initial_state({"id": "t", "title": "T", "description": ""})
    state["last_decision"] = {"tool": "noop", "tool_args": {}}
    state["last_observation"] = {"tool": "noop", "ok": True, "output": None, "error": None}
    loop.tracker.tick_iteration()
    loop.reflect(state)
    assert model.assess_calls == 0


def test_recovered_assessment_resets_the_streak() -> None:
    class _Flaky(_StuckAssessModel):
        def assess_progress(self, state: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG002
            self.assess_calls += 1
            # stuck, luego recuperado, luego stuck: nunca 2 seguidos.
            return {"score": 5, "stuck": self.assess_calls != 2, "reason": ""}

    model = _Flaky()
    deps = AgentDeps(model=model, reflection_assess_every=1)
    loop = _AgentLoop(deps, SafeguardTracker(Budgets(max_iterations=20)), LoopDetector())
    state = initial_state({"id": "t", "title": "T", "description": ""})
    state["last_decision"] = {"tool": "noop", "tool_args": {}}
    state["last_observation"] = {"tool": "noop", "ok": True, "output": None, "error": None}
    for _ in range(3):
        loop.tracker.tick_iteration()
        loop.reflect(state)
    # stuck→ok→stuck: la racha se resetea y NO se escala.
    delta = loop.plan(state)
    assert delta.get("abort_code") != "reflection_stalled"
