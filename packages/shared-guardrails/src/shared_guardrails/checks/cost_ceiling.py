"""Cost-ceiling guardrail (Plan 11, Phase B — task_11_09).

Registers the ``cost_ceiling`` guardrail type. It triggers when the cost of a
call (or the accumulated cost so far) exceeds a configured threshold, so the
host can block further spend before it happens.

Hooks
-----
Most useful at ``pre_llm`` (about to spend on a model call — block before the
spend) and ``pre_tool`` (a tool call has a cost). It works at any hook.

Cost source (injected — real pricing is Phase C)
------------------------------------------------
The real per-model pricing catalogue lands in Phase C (task_11_10+). This
guardrail therefore does **not** price anything itself: it reads the cost from
:attr:`GuardrailContext.metadata`, which the host populates. Two values are
honoured (both optional):

  * ``metadata["call_cost"]``        — the cost of *this* call,
  * ``metadata["accumulated_cost"]`` — the running total for the
    task / execution (the budget-style accumulation, section 28.7).

Either crossing its configured ceiling triggers. Costs are treated as a plain
number in a single canonical unit (USD per the plan's canonical-currency
decision); currency conversion is the host's job.

No heavy dependency — pure arithmetic.

The detection is side-effect-free: the engine applies the action; this module
only *suggests* one — configurable, defaulting to ``block`` (the plan's
"cost ceiling aborts expensive executions", human_11_03).
"""

from __future__ import annotations

from typing import Any

from shared_guardrails.checks._common import coerce_action, coerce_severity
from shared_guardrails.exceptions import GuardrailConfigError
from shared_guardrails.registry import register_guardrail
from shared_guardrails.types import Action, GuardrailContext, GuardrailResult, Severity


def _coerce_float(value: Any) -> float | None:
    """Best-effort numeric coercion; ``None`` when not a finite number."""
    if isinstance(value, bool):  # bool is an int subclass — reject it explicitly.
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


class CostCeilingGuardrail:
    """Triggers when a per-call or accumulated cost exceeds a ceiling.

    Config:
      - ``max_cost``            number — required, > 0. The ceiling, in the
        canonical cost unit (USD).
      - ``scope``               str    — ``"call"`` (compare ``call_cost``),
        ``"accumulated"`` (compare ``accumulated_cost``) or ``"both"``
        (trigger if either crosses). Default ``"both"``.
      - ``call_cost_key``       str    — metadata key for this call's cost.
        Default ``"call_cost"``.
      - ``accumulated_cost_key`` str   — metadata key for the running total.
        Default ``"accumulated_cost"``.
      - ``severity``            str    — default ``high``.
      - ``suggested_action``    str    — override the default action. When
        unset the guardrail suggests ``block``.

    The result ``payload`` carries the breaching ``scope``, the observed
    ``cost``, the configured ``max_cost`` and the ``overage``.
    """

    _SCOPES = frozenset({"call", "accumulated", "both"})

    def __init__(self, config: dict[str, Any]) -> None:
        max_cost = _coerce_float(config.get("max_cost"))
        if max_cost is None or max_cost <= 0:
            raise GuardrailConfigError(
                "cost_ceiling guardrail requires a positive numeric 'max_cost'."
            )
        self._max_cost = max_cost
        scope = str(config.get("scope", "both")).lower()
        if scope not in self._SCOPES:
            raise GuardrailConfigError(
                f"cost_ceiling guardrail 'scope' must be one of {sorted(self._SCOPES)}."
            )
        self._scope = scope
        self._call_key = str(config.get("call_cost_key", "call_cost"))
        self._acc_key = str(config.get("accumulated_cost_key", "accumulated_cost"))
        self._severity = coerce_severity(config.get("severity"), default=Severity.HIGH)
        self._suggested_override = coerce_action(config.get("suggested_action"))

    def _suggested_action(self) -> Action:
        if self._suggested_override is not None:
            return self._suggested_override
        return Action.BLOCK

    def check(self, context: GuardrailContext) -> GuardrailResult:
        candidates: list[tuple[str, float]] = []
        if self._scope in ("call", "both"):
            call_cost = _coerce_float(context.metadata.get(self._call_key))
            if call_cost is not None:
                candidates.append(("call", call_cost))
        if self._scope in ("accumulated", "both"):
            acc_cost = _coerce_float(context.metadata.get(self._acc_key))
            if acc_cost is not None:
                candidates.append(("accumulated", acc_cost))

        breaches = [(scope, cost) for scope, cost in candidates if cost > self._max_cost]
        if not breaches:
            return GuardrailResult(triggered=False)

        # Report the worst (largest) breach.
        scope, cost = max(breaches, key=lambda pair: pair[1])
        return GuardrailResult(
            triggered=True,
            severity=self._severity,
            detail=(
                f"{scope.capitalize()} cost {cost:.6g} exceeds ceiling "
                f"{self._max_cost:.6g} (budget_exceeded)."
            ),
            suggested_action=self._suggested_action(),
            payload={
                "scope": scope,
                "cost": cost,
                "max_cost": self._max_cost,
                "overage": cost - self._max_cost,
                "reason": "budget_exceeded",
            },
        )


@register_guardrail("cost_ceiling")
def _build_cost_ceiling(config: dict[str, Any]) -> CostCeilingGuardrail:
    return CostCeilingGuardrail(config)


__all__ = ["CostCeilingGuardrail"]
