"""The declarative guardrails pipeline (Plan 11, Phase A, task_11_01).

A :class:`GuardrailPipeline` is built from a declarative
:class:`PipelineConfig` and a :class:`GuardrailRegistry`. It
materializes each configured guardrail once (via the registry factory)
at build time, then runs the guardrails for a given hook in declared
order against a :class:`GuardrailContext`, aggregating their results into
a single :class:`PipelineDecision`.

The engine is pure: it does not apply side effects (block / redact /
escalate) — it reports the decision. The host wires that in later.
"""

from __future__ import annotations

from collections.abc import Mapping

from shared_guardrails.config import GuardrailSpec, PipelineConfig, load_config, parse_config
from shared_guardrails.registry import Guardrail, GuardrailRegistry, default_registry
from shared_guardrails.types import (
    Action,
    GuardrailContext,
    GuardrailOutcome,
    GuardrailResult,
    HookPoint,
    PipelineDecision,
    Severity,
    most_severe_action,
)


class _BoundGuardrail:
    """A built guardrail paired with the spec that configured it."""

    __slots__ = ("guardrail", "spec")

    def __init__(self, guardrail: Guardrail, spec: GuardrailSpec) -> None:
        self.guardrail = guardrail
        self.spec = spec


class GuardrailPipeline:
    """Runs the guardrails configured for each hook against a context."""

    def __init__(
        self,
        config: PipelineConfig,
        *,
        registry: GuardrailRegistry | None = None,
    ) -> None:
        self._config = config
        self._registry = registry if registry is not None else default_registry
        # Build every guardrail up front so an unknown type errors at
        # construction (fail fast), not on the hot path. Preserves the
        # declared order per hook.
        self._bound: dict[HookPoint, list[_BoundGuardrail]] = {}
        for hook, specs in config.hooks.items():
            self._bound[hook] = [
                _BoundGuardrail(self._registry.build(spec.type, spec.config), spec)
                for spec in specs
            ]

    @classmethod
    def from_yaml(
        cls,
        yaml_text: str,
        *,
        registry: GuardrailRegistry | None = None,
    ) -> GuardrailPipeline:
        """Build a pipeline straight from a YAML config string."""
        return cls(load_config(yaml_text), registry=registry)

    @classmethod
    def from_dict(
        cls,
        source: Mapping[str, object] | None,
        *,
        registry: GuardrailRegistry | None = None,
    ) -> GuardrailPipeline:
        """Build a pipeline from an already-parsed config dict."""
        return cls(parse_config(dict(source) if source is not None else None), registry=registry)

    @property
    def config(self) -> PipelineConfig:
        return self._config

    def run(self, context: GuardrailContext) -> PipelineDecision:
        """Run every guardrail configured for ``context.hook``, in order.

        Each guardrail is evaluated; its result is recorded as a
        :class:`GuardrailOutcome` with the resolved action (the spec's
        configured ``action`` wins over the guardrail's
        ``suggested_action``). The decisive action is the highest-
        precedence action among the guardrails that triggered.
        """
        bound = self._bound.get(context.hook, [])
        outcomes: list[GuardrailOutcome] = []
        triggered_actions: list[Action] = []

        for item in bound:
            # ADR 0102 D5: un check que REVIENTA nunca tumba el pipeline. La
            # política sale de `effective_on_error`: "warn" es fail-open
            # (outcome no disparado con el error como detalle) y "block" es
            # fail-closed (el fallo dispara con acción block). El default
            # depende de `locked`, no es "warn" para todos.
            on_error = item.spec.effective_on_error
            try:
                result = item.guardrail.check(context)
            except Exception as exc:
                outcome, action = self._no_verdict(
                    item,
                    on_error=on_error,
                    detail=f"check crashed ({type(exc).__name__}: {exc})",
                    payload={},
                )
                outcomes.append(outcome)
                if action is not None:
                    triggered_actions.append(action)
                continue
            if _is_unavailable(result):
                # El check corrió pero se declara SIN veredicto (p. ej.
                # `content_safety` sin clasificador, que es su estado por
                # defecto). Antes pasaba en silencio: `triggered=False`, ningún
                # evento persistido, y la plataforma creyendo tener una capa que
                # no estaba corriendo. Misma política que el crash: no emitir
                # veredicto es no emitir veredicto.
                outcome, action = self._no_verdict(
                    item,
                    on_error=on_error,
                    detail=result.detail or "guardrail unavailable",
                    payload=dict(result.payload),
                )
                outcomes.append(outcome)
                if action is not None:
                    triggered_actions.append(action)
                continue
            # Config action overrides the guardrail's own suggestion.
            action = item.spec.action if item.spec.action is not None else result.suggested_action
            outcomes.append(
                GuardrailOutcome(
                    type=item.spec.type,
                    triggered=result.triggered,
                    severity=result.severity,
                    detail=result.detail,
                    action=action if result.triggered else None,
                    payload=result.payload,
                )
            )
            if result.triggered and action is not None:
                triggered_actions.append(action)

        any_triggered = any(o.triggered for o in outcomes)
        decisive = most_severe_action(triggered_actions)
        return PipelineDecision(
            hook=context.hook,
            triggered=any_triggered,
            action=decisive,
            outcomes=outcomes,
        )

    @staticmethod
    def _no_verdict(
        item: _BoundGuardrail,
        *,
        on_error: str,
        detail: str,
        payload: dict[str, object],
    ) -> tuple[GuardrailOutcome, Action | None]:
        """El outcome de un check que no emitió veredicto (ADR 0102 D5).

        Cubre los dos modos: el check reventó, o se declaró indisponible. Con
        ``block`` cuenta como disparo (fail-closed); con ``warn`` también
        DISPARA, pero con acción ``warn``: la diferencia entre fail-open y
        silencio es que el fail-open deja rastro. ``record_pipeline_decision``
        solo persiste los outcomes disparados, así que sin esto el operador no
        podría enterarse nunca de que su capa lleva una semana sin correr.

        Un ``action:`` declarado en la config gana en las dos ramas: si el
        operador dijo «cuando este check hable, escala», eso vale también cuando
        lo que dice es «no puedo hablar».
        """
        fail_closed = on_error == "block"
        default_action = Action.BLOCK if fail_closed else Action.WARN
        action = item.spec.action if item.spec.action is not None else default_action
        return (
            GuardrailOutcome(
                type=item.spec.type,
                triggered=True,
                severity=Severity.HIGH if fail_closed else Severity.LOW,
                detail=detail,
                action=action,
                payload={**payload, "on_error": on_error},
            ),
            action,
        )


def _is_unavailable(result: GuardrailResult) -> bool:
    """El check corrió pero se declaró SIN clasificador / sin veredicto.

    El contrato es el que ya usa ``content_safety``: ``triggered=False`` con
    ``payload["available"] is False`` — nunca finge un veredicto seguro. Un
    check que dispara no entra por aquí: si tiene veredicto, manda el veredicto.
    """
    return not result.triggered and result.payload.get("available") is False


__all__ = ["GuardrailPipeline"]
