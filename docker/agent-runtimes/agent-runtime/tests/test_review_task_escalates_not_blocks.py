"""Una tarea de REVISIÓN/análisis (entregable en prosa, cero ficheros escritos)
que el self-review rechaza y luego agota el research NO debe abortar → blocked;
debe ESCALAR a validación humana con un motivo legible (ADR 0087 / 0130-fix).

Run real que motivó el fix: `019f9323` — «Revisión de seguridad básica» (CI4).
El agente entregó el informe, el self-review lo marcó fail (los criterios estaban
redactados como "no debe existir X" y el informe reconocía hallazgos no
bloqueantes), y en el reintento saltó `research_exhausted` → `aborted` → blocked.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_runtime.graph import (
    STATUS_ABORTED,
    STATUS_NEEDS_HUMAN_REVIEW,
    STATUS_RUNNING,
    _abort_or_escalate_status,
    _AgentLoop,
    _trip_outcome,
)
from agent_runtime.safeguards import SafeguardCode


def _loop() -> _AgentLoop:
    deps = SimpleNamespace(is_review=False)
    return _AgentLoop(deps, tracker=SimpleNamespace(), detector=SimpleNamespace())  # type: ignore[arg-type]


# --- Fix A: un entregable en prosa cuenta para escalar (no abortar) ----------
def test_abort_or_escalate_escalates_on_deliverable() -> None:
    # sin producing-tool (has_produced=False) ni review-role, pero CON entregable
    assert (
        _abort_or_escalate_status(False, is_review=False, has_deliverable=True)
        == STATUS_NEEDS_HUMAN_REVIEW
    )


def test_abort_or_escalate_sterile_still_aborts() -> None:
    assert (
        _abort_or_escalate_status(False, is_review=False, has_deliverable=False) == STATUS_ABORTED
    )


def test_finalize_latches_deliverable_on_real_finish() -> None:
    loop = _loop()
    assert loop.has_deliverable is False
    loop.finalize(
        {
            "status": STATUS_RUNNING,
            "steps": [],
            "output": None,
            "abort_code": None,
            "approval": None,
            "last_decision": {"output": "## Informe de revisión…", "finish_status": "success"},
        }
    )
    assert loop.has_deliverable is True


def test_finalize_no_deliverable_on_aborted() -> None:
    loop = _loop()
    loop.finalize({"status": STATUS_ABORTED, "steps": [], "output": None, "abort_code": "x"})
    assert loop.has_deliverable is False


def test_finalize_no_deliverable_on_empty_output() -> None:
    loop = _loop()
    loop.finalize(
        {
            "status": STATUS_RUNNING,
            "steps": [],
            "output": None,
            "abort_code": None,
            "approval": None,
            "last_decision": {},  # nunca llamó submit_result ni produjo output
        }
    )
    assert loop.has_deliverable is False


# --- Fix B: research_exhausted dentro de un ciclo de self-review = stalemate --
def test_trip_outcome_stalemate_in_review_cycle() -> None:
    code, summary, output = _trip_outcome(
        review_retries=1,
        last_review_feedback="Los criterios describen un estado a certificar",
        fallback_code=str(SafeguardCode.RESEARCH_EXHAUSTED),
        fallback_summary="Safeguard tripped: research_exhausted",
    )
    assert code == str(SafeguardCode.SELF_REVIEW_STALEMATE)
    assert "stalemate" in summary.lower()
    assert "1 retry" in summary
    assert output is not None
    assert "unsatisfiable" in output
    assert "Los criterios describen un estado a certificar" in output


def test_trip_outcome_outside_review_is_fallback() -> None:
    code, _summary, output = _trip_outcome(
        review_retries=0,
        last_review_feedback="",
        fallback_code=str(SafeguardCode.RESEARCH_EXHAUSTED),
        fallback_summary="Safeguard tripped: research_exhausted",
    )
    assert code == str(SafeguardCode.RESEARCH_EXHAUSTED)
    assert output is None


def test_loop_trip_outcome_still_reports_repetitive_loop_outside_review() -> None:
    # regresión: el wrapper de loop repetitivo conserva su contrato
    from agent_runtime.graph import _loop_trip_outcome

    code, summary, output = _loop_trip_outcome(
        review_retries=0, last_review_feedback="", tool="write_file"
    )
    assert code == str(SafeguardCode.REPETITIVE_LOOP)
    assert summary == "Repetitive loop detected on tool 'write_file'"
    assert output is None


# --- e2e: el nodo plan() escala en vez de abortar en el caso real ------------
def test_plan_research_exhausted_in_review_cycle_escalates_legibly() -> None:
    from agent_runtime.graph import AgentDeps
    from agent_runtime.loop_detection import LoopDetector
    from agent_runtime.safeguards import Budgets, SafeguardTracker

    class _M:
        def decide(self, state: dict) -> object:  # noqa: ARG002
            raise AssertionError("no debe llegar a decide — el trip corta antes")

        def review(self, state: dict) -> object:  # noqa: ARG002  # pragma: no cover
            raise AssertionError

    loop = _AgentLoop(AgentDeps(model=_M()), SafeguardTracker(Budgets()), LoopDetector(threshold=3))
    # Fuerza el trip por same-target reads (>= _SAME_TARGET_HARD_LIMIT) con un
    # ciclo de self-review activo (review_retries>0) → stalemate.
    loop.read_counts = {"read_file:Config/App.php": 99}
    out = loop.plan(
        {
            "steps": [],
            "review_retries": 1,
            "last_review_feedback": "El informe reconoce hallazgos; criterios como estado",
            "status": STATUS_RUNNING,
        }
    )
    assert out["status"] == STATUS_NEEDS_HUMAN_REVIEW
    assert out["abort_code"] == str(SafeguardCode.SELF_REVIEW_STALEMATE)
    assert "unsatisfiable" in (out.get("output") or "")
