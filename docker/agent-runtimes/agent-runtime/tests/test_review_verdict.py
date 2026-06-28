"""self-review verdict parsing — AUTHORITATIVE gate (ADR 0087).

Refactor (2026-06-27): the self-review is now an AUTHORITATIVE gate (operator
decision). The verdict is THREE-state:

  * passed=True            → the output is certified, the task is DONE.
  * passed=False           → an EXPLICIT rejection; retry with feedback.
  * inconclusive=True      → the verdict could not be determined reliably
                             (no structured verdict + ambiguous prose, or
                             malformed tool args). The loop ESCALATES to a
                             human instead of passing (fail-open) or aborting.

Canonical parse order in ``_review_from``: structured tool-call (submit_verdict)
> embedded JSON > conservative prose markers (the documented last-resort
safety-net). Prose with NEITHER an explicit pass NOR an explicit fail signal is
INCONCLUSIVE — it never silently passes (that was the old fail-open polarity).

The auth/JWT postmortem lesson is PRESERVED: domain words ("rechaza", "falla")
must NOT be read as a rejection — but under the authoritative gate such ambiguous
prose is INCONCLUSIVE (escalate), not a pass.
"""

from __future__ import annotations

from types import SimpleNamespace

from agent_runtime.providers import _parse_verdict, _review_from


# --- _parse_verdict: three-state prose/JSON parsing ---------------------------
def test_json_verdict_is_honored() -> None:
    assert _parse_verdict('{"passed": true, "feedback": "ok"}') == (True, "ok")
    passed, _ = _parse_verdict('{"passed": false, "feedback": "bug"}')
    assert passed is False


def test_explicit_approval_prose_passes() -> None:
    approval = "La implementación satisface los criterios de aceptación. Buen trabajo."
    assert _parse_verdict(approval)[0] is True
    assert _parse_verdict("The output meets every acceptance criterion.")[0] is True


def test_explicit_rejection_prose_fails() -> None:
    # Clear negative-VERDICT phrases → explicit fail (NOT inconclusive).
    assert _parse_verdict("Veredicto: no aprobado, falta el criterio 2.")[0] is False
    assert _parse_verdict("Resultado: no supera la revisión.")[0] is False
    assert _parse_verdict("This output does not satisfy the task.")[0] is False
    falta = "passed: false — falta el endpoint de refresh"
    assert _parse_verdict(falta)[0] is False


def test_bare_negated_criterion_prose_is_inconclusive() -> None:
    # F33: the bare "no cumple" / "no satisface" markers are gone — they were
    # ambiguous (an approving review can carry them). With no unequivocal verdict
    # signal the parse is INCONCLUSIVE (None), never a default fail, and the
    # negation guard stops "no satisface los criterios" flipping to a pass.
    assert _parse_verdict("La salida no cumple el criterio 2.")[0] is None
    assert _parse_verdict("Resultado: no satisface los criterios de aceptación.")[0] is None
    assert _parse_verdict("El output no cumple ninguna mala práctica.")[0] is None


def test_ambiguous_prose_is_inconclusive() -> None:
    # No explicit pass NOR fail signal → inconclusive (None), NOT a silent pass.
    assert _parse_verdict("He revisado el output y aquí están mis notas.")[0] is None
    assert _parse_verdict("The reviewer looked at the code.")[0] is None


def test_auth_domain_prose_is_inconclusive_not_rejection() -> None:
    # Regression (2026-06-27): domain words (rechaza/falla/fallo) must NOT be
    # read as a rejection. Under the authoritative gate they are INCONCLUSIVE
    # (escalate), never an explicit fail.
    auth = "El filtro rechaza tokens inválidos y maneja el fallo de auth."
    assert _parse_verdict(auth)[0] is None
    assert _parse_verdict("La implementación no falla ante tokens expirados.")[0] is None
    assert _parse_verdict("JWT completada; rechazo de tokens revocados OK.")[0] is None


def test_bare_fail_word_is_not_a_rejection() -> None:
    # Contains "fail" but is approving — must NOT be an explicit rejection.
    approving = "The implementation does not fail to meet any requirement."
    assert _parse_verdict(approving)[0] is not False


# --- _review_from: canonical order + ReviewResponse(inconclusive) -------------
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
    assert r.passed is True and r.inconclusive is False and r.feedback == "ok"
    r2 = _review_from(
        _resp(tool_calls=[_call("submit_verdict", passed=False, feedback="falta test")]), model="m"
    )
    assert r2.passed is False and r2.inconclusive is False and r2.feedback == "falta test"


def test_review_tool_call_missing_passed_is_inconclusive() -> None:
    # The structured path is fail-CLOSED too: a submit_verdict call that omits/
    # malforms `passed` does NOT default to pass — it escalates.
    r = _review_from(_resp(tool_calls=[_call("submit_verdict", feedback="hmm")]), model="m")
    assert r.passed is False and r.inconclusive is True


def test_review_prefers_tool_call_over_prose() -> None:
    # Canonical order: a submit_verdict tool-call wins over any prose content.
    resp = _resp(
        tool_calls=[_call("submit_verdict", passed=False)],
        content="satisface los criterios",
    )
    r = _review_from(resp, model="m")
    assert r.passed is False and r.inconclusive is False


def test_review_falls_back_to_prose_without_tool_call() -> None:
    assert _review_from(_resp(content="satisface los criterios"), model="m").passed is True
    rej = _review_from(_resp(content="Veredicto: no aprobado, falta el test."), model="m")
    assert rej.passed is False and rej.inconclusive is False
    amb = _review_from(_resp(content="he mirado el código"), model="m")
    assert amb.passed is False and amb.inconclusive is True
