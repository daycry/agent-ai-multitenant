"""Applying the six guardrail actions to a context (Plan 11, task_11_03).

When the pipeline produces a :class:`~shared_guardrails.types.PipelineDecision`
with a decisive :class:`~shared_guardrails.types.Action`, *something* has to
act on it. The engine stays pure — it never sends a prompt, calls a tool, or
opens a human-review ticket — so this module turns the decision into a single
**typed outcome** the host (api-server / worker) can switch on:

  - ``block``                -> :class:`BlockOutcome`  (carries a typed
                                :class:`GuardrailBlocked` the host can raise)
  - ``redact`` / mask        -> :class:`RedactOutcome` (the offending span(s)
                                replaced in a COPY of the payload)
  - ``warn``                 -> :class:`WarnOutcome`    (an event to log/emit,
                                the call proceeds)
  - ``retry_with_feedback``  -> :class:`RetryOutcome`   (feedback to append +
                                a bounded retry budget)
  - ``escalate_to_human``    -> :class:`EscalateOutcome`(a human-review signal
                                for the existing approval/escalation flow)
  - ``transform``            -> :class:`TransformOutcome`(a configured rewrite
                                applied to a COPY of the payload)

Binding rules:
  * ``redact`` and ``transform`` never mutate the caller's original context.
    They build a *copy* (:meth:`GuardrailContext` is a dataclass; we deep-copy
    the mutable fields) and report both the original and the modified payload.
  * ``retry_with_feedback`` respects a max-retries bound: once the budget is
    spent the action degrades to a block (you cannot loop forever).
  * The pipeline itself only *stops* on ``block`` / ``escalate`` (see
    :attr:`PipelineDecision.allowed`); the other actions annotate and let the
    host proceed. :func:`apply_action` mirrors that in ``outcome.proceed``.

This module is pure and dependency-free: no DB, no I/O, no LLM. The host wires
the side effects (raise the block, re-run the LLM, open the review ticket).
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any

from shared_guardrails.exceptions import GuardrailError
from shared_guardrails.types import (
    Action,
    GuardrailContext,
    GuardrailOutcome,
    HookPoint,
    PipelineDecision,
    Severity,
)

# Default retry budget for ``retry_with_feedback`` when the config does not
# pin one. Kept small: a guardrail feedback loop is corrective, not a search.
DEFAULT_MAX_RETRIES = 2


class GuardrailBlocked(GuardrailError):  # noqa: N818 - "Blocked" reads cleaner than "Error"
    """Raised (by the host) when a ``block`` action aborts the call.

    The engine does not raise this itself — it hands the host a
    :class:`BlockOutcome` whose :meth:`BlockOutcome.as_exception` builds this
    so the host can ``raise`` it at the call site it controls. Carries the
    hook, severity, reason, and the per-guardrail outcomes for audit.
    """

    def __init__(
        self,
        *,
        hook: HookPoint,
        reason: str,
        severity: Severity = Severity.HIGH,
        outcomes: list[GuardrailOutcome] | None = None,
    ) -> None:
        super().__init__(f"Guardrail blocked the {hook} call: {reason}")
        self.hook = hook
        self.reason = reason
        self.severity = severity
        self.outcomes: list[GuardrailOutcome] = outcomes or []


@dataclass(frozen=True)
class _BaseOutcome:
    """Common shape of every applied-action outcome.

    ``action`` is the decisive action this outcome realizes; ``hook`` is the
    point it was applied at; ``proceed`` says whether the host may continue the
    call after handling this outcome (False for block / escalate).
    """

    action: Action
    hook: HookPoint
    proceed: bool
    reason: str = ""
    severity: Severity = Severity.INFO


@dataclass(frozen=True)
class BlockOutcome(_BaseOutcome):
    """``block`` — abort the call. ``proceed`` is always False."""

    blocked_outcomes: list[GuardrailOutcome] = field(default_factory=list)

    def as_exception(self) -> GuardrailBlocked:
        """Build the typed exception the host raises to abort the call."""
        return GuardrailBlocked(
            hook=self.hook,
            reason=self.reason,
            severity=self.severity,
            outcomes=list(self.blocked_outcomes),
        )


@dataclass(frozen=True)
class RedactOutcome(_BaseOutcome):
    """``redact`` — offending spans masked in a COPY of the context.

    ``context`` is the new (masked) context; the original passed in is left
    untouched. ``spans`` is the list of literal substrings that were masked,
    ``mask`` the replacement marker used.
    """

    context: GuardrailContext = field(default_factory=lambda: GuardrailContext(hook="pre_llm"))
    spans: list[str] = field(default_factory=list)
    mask: str = ""


@dataclass(frozen=True)
class WarnOutcome(_BaseOutcome):
    """``warn`` — an event to record/emit; the call proceeds."""

    event: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetryOutcome(_BaseOutcome):
    """``retry_with_feedback`` — re-run the LLM with feedback appended.

    ``feedback`` is the corrective text the host appends to the prompt before
    re-running. ``attempt`` is the retry count this outcome represents (the
    failed attempt that triggered it), ``max_retries`` the bound. ``exhausted``
    is True once the budget is spent — the host must then treat it as a block
    (``proceed`` is False in that case).
    """

    feedback: str = ""
    attempt: int = 0
    max_retries: int = DEFAULT_MAX_RETRIES
    exhausted: bool = False


@dataclass(frozen=True)
class EscalateOutcome(_BaseOutcome):
    """``escalate_to_human`` — raise a human-review signal.

    ``signal`` is a dict the host feeds to the existing approval / escalation
    flow (api-server approvals): it names the hook, the reason, the severity,
    and carries the triggering guardrail outcomes. ``proceed`` is False — the
    call waits for a human decision.
    """

    signal: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TransformOutcome(_BaseOutcome):
    """``transform`` — a configured rewrite applied to a COPY of the context.

    ``context`` is the rewritten context (original untouched). ``transformer``
    names the transform that ran, for audit.
    """

    context: GuardrailContext = field(default_factory=lambda: GuardrailContext(hook="pre_llm"))
    transformer: str = ""


# A transformer takes the text payload of a context and returns the rewritten
# text. Registered by name; the ``transform`` action names which one to run.
Transformer = Callable[[str], str]


def _copy_context(context: GuardrailContext) -> GuardrailContext:
    """A safe copy whose mutable fields are independent of the original."""
    return replace(
        context,
        tool_args=copy.deepcopy(context.tool_args),
        tool_result=copy.deepcopy(context.tool_result),
        metadata=copy.deepcopy(context.metadata),
    )


def _set_primary_text(context: GuardrailContext, text: str) -> None:
    """Write ``text`` back to the field that holds this hook's payload.

    Mirrors :meth:`GuardrailContext.primary_text`. ``pre_tool`` keys off the
    tool *name*, which redaction/transform never rewrites, so it is a no-op
    there; ``post_tool`` only rewrites a string result.
    """
    if context.hook == "pre_llm":
        context.prompt = text
    elif context.hook == "post_llm":
        context.response = text
    elif context.hook == "post_tool" and isinstance(context.tool_result, str):
        context.tool_result = text


def _gather_spans(outcomes: list[GuardrailOutcome]) -> list[str]:
    """Collect the literal substrings the triggering guardrails matched.

    Built-in text guardrails report matches under ``payload['matches']``
    (regex) or ``payload['matched']`` (keyword). We mask exactly those so we
    never blank out more than the guardrail flagged. Order-preserving,
    de-duplicated, longest-first so overlapping spans mask cleanly.
    """
    spans: list[str] = []
    for o in outcomes:
        if not o.triggered:
            continue
        for keyname in ("matches", "matched", "spans"):
            raw = o.payload.get(keyname)
            if isinstance(raw, list):
                spans.extend(str(s) for s in raw if s)
    # De-dup preserving order, then mask longest spans first.
    seen: set[str] = set()
    unique: list[str] = []
    for s in spans:
        if s not in seen:
            seen.add(s)
            unique.append(s)
    unique.sort(key=len, reverse=True)
    return unique


def apply_block(decision: PipelineDecision) -> BlockOutcome:
    """Realize a ``block`` decision: abort the call."""
    triggered = decision.triggered_outcomes
    reason = triggered[0].detail if triggered else "blocked by guardrail"
    severity = _max_severity(triggered)
    return BlockOutcome(
        action=Action.BLOCK,
        hook=decision.hook,
        proceed=False,
        reason=reason,
        severity=severity,
        blocked_outcomes=list(triggered),
    )


def apply_redact(
    decision: PipelineDecision,
    context: GuardrailContext,
    *,
    mask: str = "[REDACTED]",
) -> RedactOutcome:
    """Realize a ``redact`` decision: mask the offending spans in a COPY.

    The original ``context`` is never mutated. The call proceeds with the
    masked copy.
    """
    spans = _gather_spans(decision.triggered_outcomes)
    masked = _copy_context(context)
    text = masked.primary_text()
    redacted = text
    for span in spans:
        if span:
            redacted = redacted.replace(span, mask)
    _set_primary_text(masked, redacted)
    triggered = decision.triggered_outcomes
    return RedactOutcome(
        action=Action.REDACT,
        hook=decision.hook,
        proceed=True,
        reason=triggered[0].detail if triggered else "redacted by guardrail",
        severity=_max_severity(triggered),
        context=masked,
        spans=spans,
        mask=mask,
    )


def apply_warn(decision: PipelineDecision) -> WarnOutcome:
    """Realize a ``warn`` decision: record an event, allow the call."""
    triggered = decision.triggered_outcomes
    severity = _max_severity(triggered)
    event = {
        "hook": decision.hook,
        "severity": severity.value,
        "guardrails": [
            {"type": o.type, "detail": o.detail, "severity": o.severity.value} for o in triggered
        ],
    }
    return WarnOutcome(
        action=Action.WARN,
        hook=decision.hook,
        proceed=True,
        reason=triggered[0].detail if triggered else "warning from guardrail",
        severity=severity,
        event=event,
    )


def apply_retry(
    decision: PipelineDecision,
    *,
    attempt: int = 0,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> RetryOutcome:
    """Realize a ``retry_with_feedback`` decision with a bounded budget.

    ``attempt`` is how many retries have already been spent. While
    ``attempt < max_retries`` the host should append ``feedback`` and re-run
    the LLM (``proceed`` is False — the current response is not accepted as-is,
    a new attempt is required). Once the budget is exhausted the outcome is
    ``exhausted`` and the host must stop retrying (treat as a block).
    """
    triggered = decision.triggered_outcomes
    feedback = " ".join(o.detail for o in triggered if o.detail).strip()
    exhausted = attempt >= max_retries
    return RetryOutcome(
        action=Action.RETRY_WITH_FEEDBACK,
        hook=decision.hook,
        proceed=False,
        reason="retry budget exhausted" if exhausted else (feedback or "retry requested"),
        severity=_max_severity(triggered),
        feedback=feedback,
        attempt=attempt,
        max_retries=max_retries,
        exhausted=exhausted,
    )


def apply_escalate(decision: PipelineDecision) -> EscalateOutcome:
    """Realize an ``escalate_to_human`` decision: raise a review signal.

    The ``signal`` is shaped for the existing approval / escalation flow: it
    names the hook, reason, severity, and the triggering guardrails. The call
    does not proceed until a human decides.
    """
    triggered = decision.triggered_outcomes
    severity = _max_severity(triggered)
    signal = {
        "kind": "guardrail_escalation",
        "hook": decision.hook,
        "severity": severity.value,
        "reason": triggered[0].detail if triggered else "escalated by guardrail",
        "guardrails": [
            {"type": o.type, "detail": o.detail, "severity": o.severity.value} for o in triggered
        ],
    }
    return EscalateOutcome(
        action=Action.ESCALATE_TO_HUMAN,
        hook=decision.hook,
        proceed=False,
        reason=signal["reason"] if isinstance(signal["reason"], str) else "",
        severity=severity,
        signal=signal,
    )


def apply_transform(
    decision: PipelineDecision,
    context: GuardrailContext,
    transformer: Transformer,
    *,
    name: str = "",
) -> TransformOutcome:
    """Realize a ``transform`` decision: rewrite the payload in a COPY.

    ``transformer`` is applied to this hook's text payload; the original
    ``context`` is never mutated. The call proceeds with the rewritten copy.
    """
    rewritten = _copy_context(context)
    new_text = transformer(rewritten.primary_text())
    _set_primary_text(rewritten, new_text)
    triggered = decision.triggered_outcomes
    return TransformOutcome(
        action=Action.TRANSFORM,
        hook=decision.hook,
        proceed=True,
        reason=triggered[0].detail if triggered else "transformed by guardrail",
        severity=_max_severity(triggered),
        context=rewritten,
        transformer=name,
    )


def _max_severity(outcomes: list[GuardrailOutcome]) -> Severity:
    """Highest severity among the triggering guardrails (INFO if none)."""
    order = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
    rank = {s: i for i, s in enumerate(order)}
    best = Severity.INFO
    for o in outcomes:
        if rank[o.severity] > rank[best]:
            best = o.severity
    return best


# The applied-action outcome union. The host switches on the concrete type.
AppliedAction = (
    BlockOutcome | RedactOutcome | WarnOutcome | RetryOutcome | EscalateOutcome | TransformOutcome
)


def apply_action(
    decision: PipelineDecision,
    context: GuardrailContext,
    *,
    mask: str = "[REDACTED]",
    transformer: Transformer | None = None,
    transformer_name: str = "",
    attempt: int = 0,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> AppliedAction | None:
    """Dispatch the decision's decisive action to its handler.

    Returns the typed outcome for the action, or ``None`` when nothing
    triggered. ``transformer`` must be supplied when the decisive action is
    ``transform`` (the engine does not own a transformer registry — the host
    provides the configured rewrite).
    """
    action = decision.action
    if action is None:
        return None
    # Side-effect-free actions need only the decision; dispatch by table to
    # keep the branch count low (and the mapping exhaustive over the enum).
    simple: dict[Action, Callable[[], AppliedAction]] = {
        Action.BLOCK: lambda: apply_block(decision),
        Action.REDACT: lambda: apply_redact(decision, context, mask=mask),
        Action.WARN: lambda: apply_warn(decision),
        Action.RETRY_WITH_FEEDBACK: lambda: apply_retry(
            decision, attempt=attempt, max_retries=max_retries
        ),
        Action.ESCALATE_TO_HUMAN: lambda: apply_escalate(decision),
    }
    handler = simple.get(action)
    if handler is not None:
        return handler()
    # transform — the only action that needs a caller-supplied rewrite.
    if transformer is None:
        raise GuardrailError("transform action requires a transformer callable to be supplied.")
    return apply_transform(decision, context, transformer, name=transformer_name)


__all__ = [
    "DEFAULT_MAX_RETRIES",
    "AppliedAction",
    "BlockOutcome",
    "EscalateOutcome",
    "GuardrailBlocked",
    "RedactOutcome",
    "RetryOutcome",
    "TransformOutcome",
    "Transformer",
    "WarnOutcome",
    "apply_action",
    "apply_block",
    "apply_escalate",
    "apply_redact",
    "apply_retry",
    "apply_transform",
    "apply_warn",
]
