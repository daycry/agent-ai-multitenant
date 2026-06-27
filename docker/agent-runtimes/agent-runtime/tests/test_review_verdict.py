"""self-review verdict parsing (regression 2026-06-27).

A run wrote its full deliverable and finished, but the prose self-review
("la implementación satisface los criterios") was mis-parsed as a failure
because the old logic REQUIRED the word pass/approve — looping the run to
`max_review_retries_exceeded`. Now a prose review PASSES unless it carries an
EXPLICIT rejection signal.
"""

from __future__ import annotations

from agent_runtime.providers import _parse_verdict


def test_json_verdict_is_honored() -> None:
    assert _parse_verdict('{"passed": true, "feedback": "ok"}')[0] is True
    assert _parse_verdict('{"passed": false, "feedback": "bug"}')[0] is False


def test_approving_prose_passes() -> None:
    passed, _ = _parse_verdict(
        "La implementación satisface los criterios de aceptación. Buen trabajo."
    )
    assert passed is True
    assert _parse_verdict("The output meets every acceptance criterion.")[0] is True


def test_explicit_rejection_fails() -> None:
    assert _parse_verdict("La salida no cumple el criterio 2.")[0] is False
    assert _parse_verdict("This output does not satisfy the task.")[0] is False
    assert _parse_verdict("Rechazada: faltan los tests.")[0] is False
    assert _parse_verdict("La entrega está incompleta.")[0] is False


def test_bare_fail_word_is_not_a_rejection() -> None:
    # Contains "fail" but is approving — must NOT be read as a rejection.
    assert _parse_verdict("The implementation does not fail to meet any requirement.")[0] is True
