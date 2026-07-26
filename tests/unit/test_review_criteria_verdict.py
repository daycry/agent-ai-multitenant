"""Veredicto POR CRITERIO (`task_wf_61`).

El reviewer emitía prosa con UN `<failed_criterion>`. Con eso: el humano no
sabía qué criterios pasaron, el `what_to_fix` no tenía diana cuando fallaban
dos, y no había nada medible entre runs (el sistema de evals se quedaba ciego).

El desglose es ADITIVO — el `<verdict>` sigue mandando — así que lo primero que
estos tests fijan es que un reviewer que NO lo emita se comporta exactamente
como antes. Es la propiedad que hace seguro encenderlo.
"""

from __future__ import annotations

import pytest
from api_server.reviewer_bridge import parse_criteria_block, parse_reviewer_output

pytestmark = pytest.mark.unit

_BLOCK = """<criteria>
- [pass] El endpoint devuelve 200 — evidence: test_api.py::test_ok en verde
- [fail] Registra el intento — evidence: no hay ninguna llamada a logger en el diff
</criteria>"""


# ---------------------------------------------------------------------------
# Retrocompatibilidad: sin desglose, todo igual que antes
# ---------------------------------------------------------------------------
def test_a_verdict_without_the_block_behaves_exactly_as_before() -> None:
    verdict = parse_reviewer_output(
        "Todo bien.\n<verdict>approve</verdict>",
    )
    assert verdict.label == "approve"
    assert verdict.criteria == ()


def test_an_old_style_rejection_is_untouched() -> None:
    verdict = parse_reviewer_output(
        "<verdict>reject</verdict>"
        "<rejection><failed_criterion>falta el test</failed_criterion>"
        "<what_to_fix>añádelo</what_to_fix></rejection>"
    )
    assert verdict.failed_criterion == "falta el test"
    assert verdict.what_to_fix == "añádelo"
    assert verdict.criteria == ()


# ---------------------------------------------------------------------------
# El desglose
# ---------------------------------------------------------------------------
def test_each_criterion_carries_its_own_outcome_and_evidence() -> None:
    outcomes = parse_criteria_block(_BLOCK)
    assert [(c.text, c.passed) for c in outcomes] == [
        ("El endpoint devuelve 200", True),
        ("Registra el intento", False),
    ]
    assert outcomes[1].evidence == "no hay ninguna llamada a logger en el diff"


def test_the_breakdown_travels_on_an_approve_too() -> None:
    # Saber QUÉ se comprobó vale tanto como saber qué falló: sin esto,
    # «aprobado» es indistinguible de «aprobado sin mirar».
    verdict = parse_reviewer_output(f"{_BLOCK}\n<verdict>approve</verdict>")
    assert verdict.label == "approve"
    assert len(verdict.criteria) == 2


def test_a_rejection_without_failed_criterion_derives_it_from_the_breakdown() -> None:
    # Antes, un reject sin `<failed_criterion>` dejaba al implementador sin
    # saber QUÉ arreglar. Con el desglose la diana ya está dicha.
    verdict = parse_reviewer_output(f"{_BLOCK}\n<verdict>reject</verdict>")
    assert verdict.failed_criterion == "Registra el intento"


def test_an_explicit_failed_criterion_wins_over_the_derived_one() -> None:
    verdict = parse_reviewer_output(
        f"{_BLOCK}\n<verdict>reject</verdict>"
        "<rejection><failed_criterion>lo que el reviewer diga</failed_criterion></rejection>"
    )
    assert verdict.failed_criterion == "lo que el reviewer diga"


# ---------------------------------------------------------------------------
# Tolerancia — la misma razón por la que el `<verdict>` se parsea con holgura
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "line",
    [
        "- [pass] Hace algo",
        "* [PASS] Hace algo",
        "[pass] Hace algo",
        "  -  [ pass ]  Hace algo  ",
    ],
)
def test_the_marker_survives_formatting_drift(line: str) -> None:
    outcomes = parse_criteria_block(f"<criteria>\n{line}\n</criteria>")
    assert len(outcomes) == 1
    assert outcomes[0].passed is True
    assert outcomes[0].text == "Hace algo"


def test_evidence_after_either_separator_is_kept() -> None:
    # El modelo alterna entre el guión largo y el literal; perder la evidencia
    # por el separador sería tirar justo la parte accionable.
    for line in ("- [fail] X — evidence: falta Y", "- [fail] X evidence: falta Y"):
        outcomes = parse_criteria_block(f"<criteria>\n{line}\n</criteria>")
        assert outcomes[0].evidence == "falta Y"


def test_an_unparseable_line_does_not_discard_the_rest() -> None:
    # Un desglose parcial informa más que ninguno, y el `<verdict>` sigue
    # mandando pase lo que pase aquí.
    block = "<criteria>\nblah blah sin marcador\n- [fail] El que sí\n</criteria>"
    outcomes = parse_criteria_block(block)
    assert [c.text for c in outcomes] == ["El que sí"]


def test_no_block_is_an_empty_breakdown_not_an_error() -> None:
    assert parse_criteria_block("solo prosa") == ()
    assert parse_criteria_block("") == ()


# ---------------------------------------------------------------------------
# El desglose de un APPROVE no puede colarse como «rechazo previo»
# ---------------------------------------------------------------------------
def test_an_approval_event_is_not_prior_rejection_feedback() -> None:
    # El approve escribe ahora su propio `review_comment`. Si el lector de
    # feedback previo no lo filtrara, el implementador recibiría en su preámbulo
    # un bloque VACÍO — «te rechazaron por: (nada)», que confunde más que callar.
    import inspect

    from orchestrator.dispatch import TaskDispatcher

    source = inspect.getsource(TaskDispatcher._read_prior_review_feedback)
    assert 'payload.get("approved")' in source
    assert "not any(entry.values())" in source
