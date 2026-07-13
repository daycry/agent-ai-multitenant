"""ADR 0080 — sesiones de navegador: ciclo de vida y gate de aprobación humana.

El operador firmó **validación humana POR SESIÓN**: una sesión pedida por el
córtex NO navega hasta que el owner la aprueba explícitamente. Esta es la
máquina de estados que lo garantiza (lógica pura, sin BD):

    pending_approval ──approve──▶ approved ──start──▶ running ──▶ done | failed
            └────────reject──────▶ rejected

Reglas duras que se fijan aquí:

  * de `pending_approval` solo se sale por decisión HUMANA (aprobar/rechazar);
  * nada se ejecuta sin pasar por `approved` (no hay atajo a `running`);
  * una sesión rechazada o terminal es INMUTABLE (no se puede "re-aprobar");
  * la petición se valida ANTES de pedir aprobación: al owner nunca se le pone
    delante una sesión que el runtime rechazaría igualmente.
"""

from __future__ import annotations

import pytest
from api_server.cortex.browse import (
    BROWSE_TERMINAL,
    BrowseSessionState,
    BrowseTransitionError,
    approve,
    fail,
    finish,
    reject,
    start,
    validate_browse_request,
)

pytestmark = pytest.mark.unit

_STEPS = [
    {"action": "goto", "url": "https://example.com/login"},
    {"action": "fill", "selector": "#user", "value": "owner"},
    {"action": "click", "selector": "button[type=submit]"},
    {"action": "extract", "selector": "main"},
]


def _pending() -> BrowseSessionState:
    return BrowseSessionState(status="pending_approval")


def test_a_session_starts_pending_a_human() -> None:
    assert _pending().status == "pending_approval"


def test_running_requires_a_human_approval_first() -> None:
    """El atajo prohibido: pending → running. Sin humano no se navega."""
    with pytest.raises(BrowseTransitionError):
        start(_pending())


def test_the_owner_approves_and_only_then_it_can_run() -> None:
    approved = approve(_pending())
    assert approved.status == "approved"
    running = start(approved)
    assert running.status == "running"
    done = finish(running)
    assert done.status == "done"
    assert done.status in BROWSE_TERMINAL


def test_a_rejected_session_is_immutable() -> None:
    rejected = reject(_pending(), reason="no me fío de ese sitio")
    assert rejected.status == "rejected"
    assert rejected.error == "no me fío de ese sitio"
    for move in (approve, start):
        with pytest.raises(BrowseTransitionError):
            move(rejected)


def test_a_finished_session_cannot_be_relaunched() -> None:
    done = finish(start(approve(_pending())))
    with pytest.raises(BrowseTransitionError):
        start(done)
    with pytest.raises(BrowseTransitionError):
        approve(done)


def test_a_failed_run_is_terminal_and_keeps_its_reason() -> None:
    failed = fail(start(approve(_pending())), error="chromium petó")
    assert failed.status == "failed"
    assert failed.error == "chromium petó"
    assert failed.status in BROWSE_TERMINAL


def test_the_request_is_validated_before_asking_a_human() -> None:
    """Un guion inadmisible se rechaza en la petición: no se molesta al owner
    con algo que el runtime tiraría de todas formas (mismo catálogo cerrado y
    mismo anti-SSRF que aplica el browser-runtime)."""
    with pytest.raises(ValueError):
        validate_browse_request(goal="robar", steps=[{"action": "eval_js", "script": "x"}])
    with pytest.raises(ValueError):
        validate_browse_request(
            goal="ssrf", steps=[{"action": "goto", "url": "http://169.254.169.254/"}]
        )
    with pytest.raises(ValueError):
        validate_browse_request(goal="", steps=_STEPS)


def test_a_valid_request_returns_the_normalised_plan() -> None:
    plan = validate_browse_request(goal="entrar y leer el panel", steps=_STEPS)
    assert plan["goal"] == "entrar y leer el panel"
    assert [s["action"] for s in plan["steps"]] == ["goto", "fill", "click", "extract"]


def test_the_human_sees_what_will_be_typed_but_it_never_leaves_again() -> None:
    """El owner aprueba a ciegas si no ve el valor: el plan que se le muestra SÍ
    lleva el `value` (es lo que autoriza). Lo que no vuelve del runtime es el
    valor tecleado — ese contrato lo fija el browser-runtime, no este plan."""
    plan = validate_browse_request(goal="login", steps=_STEPS)
    fill_step = next(s for s in plan["steps"] if s["action"] == "fill")
    assert fill_step["value"] == "owner"
