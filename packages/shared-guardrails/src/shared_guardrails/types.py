"""Core types for the declarative guardrails engine (Plan 11, Phase A).

The engine evaluates an ordered list of guardrails at four hook points
around an LLM / tool interaction:

  - ``pre_llm``    before the prompt is sent to the model
  - ``post_llm``   after the model produced a response
  - ``pre_tool``   before a tool call the model requested runs
  - ``post_tool``  after a tool produced a result

A guardrail inspects a :class:`GuardrailContext` (the payload + metadata
for the current hook) and returns a :class:`GuardrailResult`. The
pipeline aggregates the results of every guardrail it ran into a single
:class:`PipelineDecision`.

These types are intentionally pure (no DB, no I/O). The host
(api-server / workers) resolves the layered config per (tenant, project)
in a later phase and passes it in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

# The four points in the LLM / tool cycle a guardrail can run at.
HookPoint = Literal["pre_llm", "post_llm", "pre_tool", "post_tool"]

HOOK_POINTS: tuple[HookPoint, ...] = ("pre_llm", "post_llm", "pre_tool", "post_tool")


class Severity(StrEnum):
    """How serious a triggered guardrail is.

    A ``str`` enum so it round-trips cleanly through YAML / JSON config
    and persisted ``guardrail_events`` rows (Phase E) without a custom
    encoder.
    """

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Action(StrEnum):
    """One of the six actions taken when a guardrail triggers.

    The engine surfaces the action on the decision; the host applies its
    side effects (Phase A only carries the decision — the real
    block/redact/escalate wiring lands where the engine is wired in).

      - ``block``                stop the call / drop the payload
      - ``redact``               mask the offending span(s), continue
      - ``warn``                 log + surface a warning, continue
      - ``retry_with_feedback``  re-run the LLM with corrective feedback
      - ``escalate_to_human``    pause for human validation
      - ``transform``            rewrite the payload via a transformer
    """

    BLOCK = "block"
    REDACT = "redact"
    WARN = "warn"
    RETRY_WITH_FEEDBACK = "retry_with_feedback"
    ESCALATE_TO_HUMAN = "escalate_to_human"
    TRANSFORM = "transform"


@dataclass
class GuardrailContext:
    """The payload + metadata a guardrail inspects at one hook point.

    Only the fields relevant to the current ``hook`` are populated; a
    guardrail reads the one(s) it cares about:

      - ``pre_llm``    -> ``prompt``
      - ``post_llm``   -> ``response``
      - ``pre_tool``   -> ``tool_name`` + ``tool_args``
      - ``post_tool``  -> ``tool_name`` + ``tool_result``

    ``metadata`` carries free-form context (tenant_id, project_id,
    agent, model, allowed_tools, running cost, ...) so guardrails that
    need it can read it without widening this dataclass. The engine
    treats it as opaque.
    """

    hook: HookPoint
    prompt: str | None = None
    response: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] = field(default_factory=dict)
    tool_result: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def primary_text(self) -> str:
        """Best-effort text payload for this hook.

        Convenience for text-scanning guardrails (regex / keyword / PII)
        so they don't each re-derive "which field holds the text for
        this hook". Returns an empty string when there is no textual
        payload (e.g. a structured tool result).
        """
        if self.hook == "pre_llm":
            return self.prompt or ""
        if self.hook == "post_llm":
            return self.response or ""
        if self.hook == "pre_tool":
            return self.tool_name or ""
        # post_tool — stringify whatever the tool returned.
        if isinstance(self.tool_result, str):
            return self.tool_result
        return "" if self.tool_result is None else str(self.tool_result)


@dataclass
class GuardrailResult:
    """The outcome of running one guardrail against one context.

    ``suggested_action`` is what the guardrail recommends; the pipeline
    overrides it with the action configured for that guardrail in the
    declarative config when one is set, so config wins over the
    guardrail's built-in default.
    """

    triggered: bool
    severity: Severity = Severity.INFO
    detail: str = ""
    suggested_action: Action | None = None
    # Free-form per-guardrail output (matched spans, redacted text, the
    # schema error, ...). Opaque to the engine; consumed by the host.
    payload: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(cls) -> GuardrailResult:
        """A non-triggered (pass) result."""
        return cls(triggered=False)


@dataclass
class GuardrailOutcome:
    """One guardrail's result enriched with the config that ran it.

    The pipeline records these in order so the host can see exactly
    which guardrail fired, at what type, and with which resolved action.
    """

    type: str
    triggered: bool
    severity: Severity
    detail: str
    action: Action | None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class PipelineDecision:
    """Aggregated result of running every guardrail for one hook.

    ``triggered`` is True if any guardrail fired. ``action`` is the
    single most severe action to apply, chosen by precedence
    (block > escalate > retry > transform > redact > warn). ``outcomes``
    preserves the per-guardrail detail in execution order.
    """

    hook: HookPoint
    triggered: bool
    action: Action | None
    outcomes: list[GuardrailOutcome] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        """Whether the call may proceed.

        Only ``block`` stops the flow outright in Phase A; the other
        actions (warn, redact, transform, retry, escalate) annotate the
        decision but let the host decide. Escalation also gates the
        flow — it requires a human before proceeding.
        """
        return self.action not in (Action.BLOCK, Action.ESCALATE_TO_HUMAN)

    @property
    def triggered_outcomes(self) -> list[GuardrailOutcome]:
        """Just the guardrails that fired, in order."""
        return [o for o in self.outcomes if o.triggered]


# Precedence used to pick the single decisive action when several
# guardrails trigger at the same hook. Lower index = wins.
_ACTION_PRECEDENCE: tuple[Action, ...] = (
    Action.BLOCK,
    Action.ESCALATE_TO_HUMAN,
    Action.RETRY_WITH_FEEDBACK,
    Action.TRANSFORM,
    Action.REDACT,
    Action.WARN,
)


def most_severe_action(actions: list[Action]) -> Action | None:
    """Pick the highest-precedence action from those that fired.

    Returns ``None`` when no actions were supplied (nothing triggered).
    """
    rank = {a: i for i, a in enumerate(_ACTION_PRECEDENCE)}
    decisive: Action | None = None
    best = len(_ACTION_PRECEDENCE)
    for a in actions:
        r = rank.get(a, len(_ACTION_PRECEDENCE))
        if r < best:
            best = r
            decisive = a
    return decisive


__all__ = [
    "HOOK_POINTS",
    "Action",
    "GuardrailContext",
    "GuardrailOutcome",
    "GuardrailResult",
    "HookPoint",
    "PipelineDecision",
    "Severity",
    "most_severe_action",
]
