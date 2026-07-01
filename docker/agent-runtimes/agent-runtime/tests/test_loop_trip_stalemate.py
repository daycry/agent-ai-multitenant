"""A repetitive-loop trip that happens DURING a self-review retry cycle is
reported as a LEGIBLE ``self_review_stalemate`` (with the reviewer's persistent
feedback), not the opaque ``repetitive_loop_detected`` — so the operator sees the
real cause (often a contradictory / unsatisfiable acceptance spec). Systemic fix
2026-07-01 for the CI4 "Implementar controladores" block.
"""

from __future__ import annotations

from agent_runtime.graph import _loop_trip_outcome
from agent_runtime.safeguards import SafeguardCode


def test_outside_review_cycle_stays_repetitive_loop() -> None:
    code, summary, output = _loop_trip_outcome(
        review_retries=0, last_review_feedback="", tool="write_file"
    )
    assert code == str(SafeguardCode.REPETITIVE_LOOP)
    assert summary == "Repetitive loop detected on tool 'write_file'"
    assert output is None


def test_in_review_cycle_reports_stalemate_with_feedback() -> None:
    code, summary, output = _loop_trip_outcome(
        review_retries=2,
        last_review_feedback="El contrato §2.2 exige {message, meta}, no ResponseTrait",
        tool="write_file",
    )
    assert code == str(SafeguardCode.SELF_REVIEW_STALEMATE)
    assert "stalemate" in summary.lower()
    assert "2 retries" in summary
    assert "El contrato §2.2 exige" in summary
    # The escalation output carries the persistent reviewer feedback for the operator.
    assert output is not None
    assert "El contrato §2.2 exige {message, meta}, no ResponseTrait" in output
    assert "insatisfacibles" in output


def test_in_review_cycle_without_feedback_has_no_output_override() -> None:
    code, summary, output = _loop_trip_outcome(
        review_retries=1, last_review_feedback="", tool="edit_file"
    )
    assert code == str(SafeguardCode.SELF_REVIEW_STALEMATE)
    assert "1 retry" in summary  # singular
    assert output is None
