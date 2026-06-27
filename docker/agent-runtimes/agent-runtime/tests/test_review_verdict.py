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
    # Only clear negative-VERDICT phrases fail (conservative markers).
    assert _parse_verdict("La salida no cumple el criterio 2.")[0] is False
    assert _parse_verdict("Resultado: no satisface los criterios de aceptación.")[0] is False
    assert _parse_verdict("This output does not satisfy the task.")[0] is False
    assert _parse_verdict("passed: false — falta el endpoint de refresh")[0] is False


def test_bare_fail_word_is_not_a_rejection() -> None:
    # Contains "fail" but is approving — must NOT be read as a rejection.
    assert _parse_verdict("The implementation does not fail to meet any requirement.")[0] is True


def test_auth_domain_prose_passes() -> None:
    # Regression (2026-06-27): an APPROVING review of auth/JWT code is full of
    # domain words (rechaza/falla/fallo) that must NOT be read as a rejection —
    # this is exactly why only the JWT task aborted while specs/migrations passed.
    assert _parse_verdict("El filtro rechaza tokens inválidos y maneja el fallo de auth.")[0]
    assert _parse_verdict("La implementación no falla ante tokens expirados. Correcta.")[0]
    assert _parse_verdict("JWT completada; rechazo de tokens revocados OK.")[0] is True


# --- ADR 0086: the verdict travels as a `submit_verdict` tool call ------------
from types import SimpleNamespace  # noqa: E402

from agent_runtime.providers import _review_from  # noqa: E402


def _resp(*, tool_calls=None, content: str = ""):  # type: ignore[no-untyped-def]
    return SimpleNamespace(
        tool_calls=tool_calls or [],
        content=content,
        model="m",
        usage=SimpleNamespace(input_tokens=1, output_tokens=2, cost_usd=0.0),
    )


def _call(name: str, **args):  # type: ignore[no-untyped-def]
    return SimpleNamespace(name=name, arguments=args)


def test_review_reads_submit_verdict_tool_call() -> None:
    r = _review_from(
        _resp(tool_calls=[_call("submit_verdict", passed=True, feedback="ok")]), model="m"
    )
    assert r.passed is True and r.feedback == "ok"
    r2 = _review_from(
        _resp(tool_calls=[_call("submit_verdict", passed=False, feedback="falta test")]), model="m"
    )
    assert r2.passed is False and r2.feedback == "falta test"


def test_review_falls_back_to_prose_without_tool_call() -> None:
    # No tool call → prose fallback (the claude_sdk safety net).
    assert _review_from(_resp(content="satisface los criterios"), model="m").passed is True
    assert _review_from(_resp(content="no cumple el criterio 2"), model="m").passed is False
