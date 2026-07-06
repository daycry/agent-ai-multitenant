"""Un run de AI-reviewer NO debe pasar por una segunda self-review (prod-17 A5).

Hallazgo de la auditoría 2026-07-06: el grafo encadena `finalize→self_review`
sin exención por `is_review`, y el skip-set solo cubre estados terminales de
escalada. Un run de reviewer (que ya emitió su `<verdict>` en el output que el
WORKER parsea con `parse_reviewer_output`) pasaba por una SEGUNDA `model.review()`
que juzgaba la prosa del veredicto contra los acceptance_criteria de la tarea
revisada → coste x2 por review, y si esa 2ª review salía inconclusa un approve
correcto acababa en `blocked` (rama ADR 0096 en el worker).

Fix: `self_review` se salta por completo cuando `is_review` y enruta a END; el
veredicto vive en el output del run, no en `review_passed`.
"""

from __future__ import annotations

import pytest
from agent_runtime.graph import AgentDeps, _AgentLoop, _route_after_review
from agent_runtime.loop_detection import LoopDetector
from agent_runtime.model import ReviewResponse
from agent_runtime.safeguards import Budgets, SafeguardTracker
from agent_runtime.state import STATUS_DONE, STATUS_RUNNING

pytestmark = pytest.mark.unit


class _RecordingModel:
    def __init__(self) -> None:
        self.review_calls = 0

    def decide(self, state: dict) -> object:  # noqa: ARG002  # pragma: no cover
        raise AssertionError("decide no debe llamarse aquí")

    def review(self, state: dict) -> ReviewResponse:  # noqa: ARG002
        self.review_calls += 1
        return ReviewResponse(passed=True)


def _review_loop(model: _RecordingModel) -> _AgentLoop:
    return _AgentLoop(
        AgentDeps(model=model, is_review=True),  # type: ignore[arg-type]
        SafeguardTracker(Budgets()),
        LoopDetector(),
    )


def _impl_loop(model: _RecordingModel) -> _AgentLoop:
    return _AgentLoop(
        AgentDeps(model=model, is_review=False),  # type: ignore[arg-type]
        SafeguardTracker(Budgets()),
        LoopDetector(),
    )


def test_review_run_skips_self_review_and_routes_end() -> None:
    model = _RecordingModel()
    loop = _review_loop(model)
    state = {"status": STATUS_DONE, "steps": [], "review_passed": False}

    result = loop.self_review(state)  # type: ignore[arg-type]

    # No hay segunda review: cero coste extra.
    assert model.review_calls == 0
    # Enruta a END (no a retry) — el run termina con su status de finalize.
    merged = {**state, **result}
    assert _route_after_review(merged) == "end"  # type: ignore[arg-type]


def test_implementer_run_still_self_reviews() -> None:
    # Regresión: un run NORMAL (no review) sí pasa por model.review().
    model = _RecordingModel()
    loop = _impl_loop(model)
    state = {"status": STATUS_RUNNING, "steps": [], "review_passed": False}

    loop.self_review(state)  # type: ignore[arg-type]

    assert model.review_calls == 1
