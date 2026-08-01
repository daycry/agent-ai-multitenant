"""prod-03 task_prod03_09 — la política de fallo del motor, tal como la decidió
el ADR 0102 D5 (`accepted`): **`on_error` por check, con default `block` para los
guardrails `locked` de plataforma y `warn` para el resto**.

El ADR se aprobó y la mitad se implementó: `on_error: block|warn` existe, se
valida y `pipeline.run` lo aplica. Lo que faltaba era justo la parte que hace que
el default signifique algo:

  * el default era `warn` para TODOS, `locked` incluido — o sea que el
    guardrail que la plataforma declara inviolable fallaba **en abierto**, que es
    literalmente lo que la opción (c) del ADR se eligió para evitar. Un candado
    que se abre solo cuando el check revienta no es un candado;
  * y un check que dice `available: False` —`content_safety` sin clasificador,
    que es su estado por defecto hoy— pasaba **en silencio**: `triggered=False`,
    ningún outcome disparado, ninguna fila en `guardrail_events`. La plataforma
    creía tener una capa que no estaba corriendo.

Los dos casos son el mismo: *el check NO emitió veredicto*. Se tratan con la
misma política, que es lo que pide el plan.

Sigue habiendo una asimetría deliberada: `on_error` explícito del operador GANA
al default. Un `locked` con `on_error: warn` escrito a mano se queda en warn — la
plataforma puede elegir observar antes de bloquear (mitigación nº1 de riesgos del
plan: «arrancar los checks no-locked en warn, observar una semana y subir a
block con datos»).
"""

from __future__ import annotations

from typing import Any

from shared_guardrails.config import GuardrailSpec, parse_config
from shared_guardrails.pipeline import GuardrailPipeline
from shared_guardrails.registry import GuardrailRegistry
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult, Severity


class _Boom:
    """Un check que revienta: el modelo caído, el regex que se atraganta."""

    def check(self, context: GuardrailContext) -> GuardrailResult:
        raise RuntimeError("modelo caído")


class _Unavailable:
    """Espejo de `content_safety` sin clasificador: NO finge un veredicto seguro."""

    def check(self, context: GuardrailContext) -> GuardrailResult:
        return GuardrailResult(
            triggered=False,
            severity=Severity.LOW,
            detail="Content-safety guardrail unavailable: no guard-model classifier configured.",
            suggested_action=None,
            payload={"available": False, "reason": "no_classifier"},
        )


class _Fine:
    """Un check disponible y contento: no dispara y no reporta indisponibilidad."""

    def check(self, context: GuardrailContext) -> GuardrailResult:
        return GuardrailResult(triggered=False, payload={"available": True})


def _registry() -> GuardrailRegistry:
    registry = GuardrailRegistry()
    registry.register("boom", lambda config: _Boom())
    registry.register("unavailable", lambda config: _Unavailable())
    registry.register("fine", lambda config: _Fine())
    return registry


def _run(entry: dict[str, Any]) -> Any:
    pipeline = GuardrailPipeline(parse_config({"pre_tool": [entry]}), registry=_registry())
    return pipeline.run(GuardrailContext(hook="pre_tool", tool_name="http_post"))


# ---------------------------------------------------------------------------
# El default depende de `locked` (ADR 0102 D5)
# ---------------------------------------------------------------------------


def test_a_locked_guardrail_defaults_to_fail_closed() -> None:
    """Sin `on_error` escrito, un `locked` que revienta BLOQUEA."""
    decision = _run({"type": "boom", "locked": True})

    assert decision.triggered is True
    assert decision.action == Action.BLOCK
    assert decision.outcomes[0].payload["on_error"] == "block"


def test_an_unlocked_guardrail_defaults_to_fail_open_but_not_to_silence() -> None:
    """Sin `on_error` escrito, un check normal que revienta NO bloquea…

    …pero SÍ deja rastro: el outcome dispara con acción `warn`. Es lo que el plan
    pide literalmente («convirtiendo la excepción en un `GuardrailOutcome`
    triggered con esa acción») y lo que el criterio de aceptación del ADR 0102
    llama «uno no-locked produce warn». Fail-open y silencio no son lo mismo:
    `record_pipeline_decision` solo persiste los outcomes disparados.
    """
    decision = _run({"type": "boom"})

    assert decision.action == Action.WARN
    assert decision.allowed is True
    assert "modelo caído" in decision.outcomes[0].detail
    assert decision.outcomes[0].payload["on_error"] == "warn"


def test_an_explicit_on_error_beats_the_locked_default() -> None:
    """El operador puede observar un `locked` antes de bloquear con él."""
    decision = _run({"type": "boom", "locked": True, "on_error": "warn"})

    assert decision.action == Action.WARN
    assert decision.allowed is True
    assert decision.outcomes[0].payload["on_error"] == "warn"


def test_an_explicit_block_on_an_unlocked_guardrail_still_blocks() -> None:
    """La otra dirección: subir a fail-closed un check que no es de plataforma."""
    decision = _run({"type": "boom", "on_error": "block"})

    assert decision.action == Action.BLOCK


def test_the_effective_policy_is_readable_without_running_anything() -> None:
    """La política efectiva es consultable — la UI de capas la enseña."""
    assert GuardrailSpec(type="x").effective_on_error == "warn"
    assert GuardrailSpec(type="x", locked=True).effective_on_error == "block"
    assert GuardrailSpec(type="x", locked=True, on_error="warn").effective_on_error == "warn"
    assert GuardrailSpec(type="x", on_error="block").effective_on_error == "block"


def test_an_unset_on_error_does_not_travel_in_to_dict() -> None:
    """`to_dict` transporta la config al sandbox (ADR 0102 D3).

    Serializar el default calculado congelaría la política del emisor: el
    receptor recalcula el mismo default desde `locked`, que sí viaja. Solo viaja
    lo que el operador escribió.
    """
    assert "on_error" not in GuardrailSpec(type="x", locked=True).to_dict()
    assert GuardrailSpec(type="x", locked=True, on_error="warn").to_dict()["on_error"] == "warn"

    # Y el roundtrip conserva la política efectiva a los dos lados.
    spec = GuardrailSpec(type="x", locked=True)
    reparsed = parse_config({"pre_tool": [spec.to_dict()]}).specs_for("pre_tool")[0]
    assert reparsed.effective_on_error == "block"


# ---------------------------------------------------------------------------
# `available: False` se trata con la MISMA política
# ---------------------------------------------------------------------------


def test_an_unavailable_locked_check_blocks_instead_of_passing_silently() -> None:
    """`content_safety` sin clasificador, declarado `locked` → no pasa nada.

    Éste es el agujero que el plan describe: hoy la ausencia de clasificador es
    el estado POR DEFECTO, así que una plataforma que cree tener content-safety
    obligatorio no lo tiene.
    """
    decision = _run({"type": "unavailable", "locked": True})

    assert decision.triggered is True
    assert decision.action == Action.BLOCK
    outcome = decision.outcomes[0]
    assert outcome.payload["available"] is False
    assert outcome.payload["on_error"] == "block"
    assert "unavailable" in outcome.detail.lower()


def test_an_unavailable_unlocked_check_is_recorded_but_does_not_block() -> None:
    """Fail-open, pero NO en silencio: el outcome dispara con acción `warn`.

    `record_pipeline_decision` solo persiste los outcomes disparados, así que
    esto es la diferencia entre que el dashboard enseñe «content_safety lleva una
    semana sin clasificador» y que no lo enseñe nunca.
    """
    decision = _run({"type": "unavailable"})

    assert decision.triggered is True
    assert decision.action == Action.WARN
    assert decision.outcomes[0].payload["available"] is False


def test_an_available_check_that_does_not_fire_stays_quiet() -> None:
    """La guarda de la guarda: lo normal no puede volverse ruido."""
    decision = _run({"type": "fine"})

    assert decision.triggered is False
    assert decision.action is None
    assert decision.outcomes[0].triggered is False


def test_the_unavailable_action_respects_an_explicit_action_override() -> None:
    """Un `action:` declarado gana también en la vía de indisponibilidad.

    Si el operador dijo «cuando este check hable, escala a humano», eso vale
    igual cuando lo que dice es «no puedo hablar».
    """
    decision = _run({"type": "unavailable", "locked": True, "action": "escalate_to_human"})

    assert decision.action == Action.ESCALATE_TO_HUMAN
