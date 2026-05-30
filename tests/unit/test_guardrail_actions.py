"""Unit tests for the six guardrail actions (Plan 11 task_11_03).

Covers the acceptance signals from the roadmap:
  - each of the six actions produces the right typed outcome;
  - block stops the pipeline (the call must not proceed);
  - redact masks the offending span(s) in a COPY (original untouched);
  - warn allows the call and records an event;
  - retry_with_feedback carries the feedback and respects a max-retries bound;
  - escalate_to_human raises a human-review signal;
  - transform applies the configured change to a COPY.
"""

from __future__ import annotations

import pytest
from shared_guardrails import (
    Action,
    BlockOutcome,
    EscalateOutcome,
    GuardrailBlocked,
    GuardrailContext,
    GuardrailOutcome,
    GuardrailPipeline,
    PipelineDecision,
    RedactOutcome,
    RetryOutcome,
    Severity,
    TransformOutcome,
    WarnOutcome,
    apply_action,
    apply_block,
    apply_escalate,
    apply_redact,
    apply_retry,
    apply_transform,
    apply_warn,
    parse_config,
)
from shared_guardrails.actions import DEFAULT_MAX_RETRIES, _copy_context, _gather_spans
from shared_guardrails.exceptions import GuardrailError

# --------------------------------------------------------------------------- #
# Helpers — build a decision the way the pipeline would.                       #
# --------------------------------------------------------------------------- #


def _decision(
    action: Action,
    *,
    hook: str = "post_llm",
    detail: str = "guardrail fired",
    severity: Severity = Severity.HIGH,
    payload: dict[str, object] | None = None,
    gtype: str = "g",
) -> PipelineDecision:
    outcome = GuardrailOutcome(
        type=gtype,
        triggered=True,
        severity=severity,
        detail=detail,
        action=action,
        payload=payload or {},
    )
    return PipelineDecision(
        hook=hook,  # type: ignore[arg-type]
        triggered=True,
        action=action,
        outcomes=[outcome],
    )


# --------------------------------------------------------------------------- #
# block — typed outcome, stops the pipeline, raises a typed exception.        #
# --------------------------------------------------------------------------- #


def test_block_produces_block_outcome_that_stops_the_call() -> None:
    decision = _decision(Action.BLOCK, detail="forbidden keyword", severity=Severity.CRITICAL)
    outcome = apply_block(decision)

    assert isinstance(outcome, BlockOutcome)
    assert outcome.action is Action.BLOCK
    assert outcome.proceed is False
    assert outcome.severity is Severity.CRITICAL
    assert "forbidden keyword" in outcome.reason
    assert len(outcome.blocked_outcomes) == 1


def test_block_outcome_builds_a_typed_exception() -> None:
    decision = _decision(Action.BLOCK, hook="pre_llm", detail="injection detected")
    exc = apply_block(decision).as_exception()
    assert isinstance(exc, GuardrailBlocked)
    assert exc.hook == "pre_llm"
    assert exc.reason == "injection detected"
    with pytest.raises(GuardrailBlocked):
        raise exc


def test_block_stops_pipeline_end_to_end() -> None:
    cfg = parse_config(
        {
            "guardrails": {
                "pre_llm": [
                    {
                        "type": "keyword",
                        "action": "block",
                        "config": {"keywords": ["ignore previous instructions"]},
                    }
                ]
            }
        }
    )
    pipeline = GuardrailPipeline(cfg)
    ctx = GuardrailContext(hook="pre_llm", prompt="please ignore previous instructions")
    decision = pipeline.run(ctx)
    assert decision.allowed is False

    applied = apply_action(decision, ctx)
    assert isinstance(applied, BlockOutcome)
    assert applied.proceed is False


# --------------------------------------------------------------------------- #
# redact — masks the span in a COPY; original untouched.                      #
# --------------------------------------------------------------------------- #


def test_redact_masks_span_in_a_copy() -> None:
    original = GuardrailContext(hook="post_llm", response="token is sk-abc123def end")
    decision = _decision(
        Action.REDACT,
        hook="post_llm",
        detail="secret leak",
        payload={"matches": ["sk-abc123def"]},
    )
    outcome = apply_redact(decision, original, mask="[REDACTED]")

    assert isinstance(outcome, RedactOutcome)
    assert outcome.proceed is True
    assert outcome.spans == ["sk-abc123def"]
    # The masked copy no longer carries the secret.
    assert "sk-abc123def" not in outcome.context.response  # type: ignore[operator]
    assert "[REDACTED]" in outcome.context.response  # type: ignore[operator]
    # The original is left exactly as it was.
    assert original.response == "token is sk-abc123def end"
    assert outcome.context is not original


def test_redact_masks_keyword_payload_key() -> None:
    # Keyword guardrail reports matches under `matched`, not `matches`.
    original = GuardrailContext(hook="pre_llm", prompt="my dni is 12345678Z please")
    decision = _decision(Action.REDACT, hook="pre_llm", payload={"matched": ["dni"]})
    outcome = apply_redact(decision, original)
    assert isinstance(outcome, RedactOutcome)
    assert "[REDACTED]" in outcome.context.prompt  # type: ignore[operator]
    assert "dni" not in outcome.context.prompt  # type: ignore[operator]
    assert original.prompt == "my dni is 12345678Z please"


def test_redact_with_no_spans_is_a_clean_copy() -> None:
    original = GuardrailContext(hook="post_llm", response="nothing matched literally")
    decision = _decision(Action.REDACT, hook="post_llm", payload={})
    outcome = apply_redact(decision, original)
    assert outcome.context.response == "nothing matched literally"
    assert outcome.context is not original


# --------------------------------------------------------------------------- #
# warn — allows the call and records an event.                                #
# --------------------------------------------------------------------------- #


def test_warn_allows_and_records_event() -> None:
    decision = _decision(
        Action.WARN, hook="post_llm", detail="mild tone issue", severity=Severity.LOW, gtype="tone"
    )
    outcome = apply_warn(decision)

    assert isinstance(outcome, WarnOutcome)
    assert outcome.action is Action.WARN
    assert outcome.proceed is True
    assert outcome.event["hook"] == "post_llm"
    assert outcome.event["severity"] == "low"
    assert outcome.event["guardrails"][0]["type"] == "tone"
    assert outcome.event["guardrails"][0]["detail"] == "mild tone issue"


# --------------------------------------------------------------------------- #
# retry_with_feedback — carries feedback, respects the bound.                 #
# --------------------------------------------------------------------------- #


def test_retry_carries_feedback_and_is_not_proceed() -> None:
    decision = _decision(
        Action.RETRY_WITH_FEEDBACK, detail="output must be valid JSON", severity=Severity.MEDIUM
    )
    outcome = apply_retry(decision, attempt=0, max_retries=2)

    assert isinstance(outcome, RetryOutcome)
    assert outcome.action is Action.RETRY_WITH_FEEDBACK
    assert outcome.feedback == "output must be valid JSON"
    assert outcome.attempt == 0
    assert outcome.max_retries == 2
    assert outcome.exhausted is False
    # The current response is not accepted as-is; a new attempt is required.
    assert outcome.proceed is False


def test_retry_respects_max_retries_bound() -> None:
    decision = _decision(Action.RETRY_WITH_FEEDBACK, detail="still wrong")
    # Budget = 2: attempts 0 and 1 may retry; attempt 2 is exhausted.
    assert apply_retry(decision, attempt=0, max_retries=2).exhausted is False
    assert apply_retry(decision, attempt=1, max_retries=2).exhausted is False

    spent = apply_retry(decision, attempt=2, max_retries=2)
    assert spent.exhausted is True
    assert spent.proceed is False
    assert "exhausted" in spent.reason


def test_retry_default_max_retries() -> None:
    decision = _decision(Action.RETRY_WITH_FEEDBACK, detail="retry me")
    outcome = apply_retry(decision)
    assert outcome.max_retries == DEFAULT_MAX_RETRIES


# --------------------------------------------------------------------------- #
# escalate_to_human — raises a review signal, gates the flow.                 #
# --------------------------------------------------------------------------- #


def test_escalate_raises_review_signal() -> None:
    decision = _decision(
        Action.ESCALATE_TO_HUMAN,
        hook="pre_tool",
        detail="dangerous tool requested",
        severity=Severity.HIGH,
        gtype="forbidden_actions",
    )
    outcome = apply_escalate(decision)

    assert isinstance(outcome, EscalateOutcome)
    assert outcome.action is Action.ESCALATE_TO_HUMAN
    # Escalation gates the flow: it waits for a human.
    assert outcome.proceed is False
    assert outcome.signal["kind"] == "guardrail_escalation"
    assert outcome.signal["hook"] == "pre_tool"
    assert outcome.signal["severity"] == "high"
    assert outcome.signal["reason"] == "dangerous tool requested"
    assert outcome.signal["guardrails"][0]["type"] == "forbidden_actions"


# --------------------------------------------------------------------------- #
# transform — applies the configured change to a COPY.                        #
# --------------------------------------------------------------------------- #


def test_transform_applies_change_to_a_copy() -> None:
    original = GuardrailContext(hook="post_llm", response="hello WORLD")
    decision = _decision(Action.TRANSFORM, hook="post_llm", detail="normalize case")

    outcome = apply_transform(decision, original, lambda text: text.lower(), name="lowercase")

    assert isinstance(outcome, TransformOutcome)
    assert outcome.action is Action.TRANSFORM
    assert outcome.proceed is True
    assert outcome.transformer == "lowercase"
    assert outcome.context.response == "hello world"
    # Original untouched.
    assert original.response == "hello WORLD"
    assert outcome.context is not original


# --------------------------------------------------------------------------- #
# apply_action — dispatch on the decision's decisive action.                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("action", "expected_type"),
    [
        (Action.BLOCK, BlockOutcome),
        (Action.WARN, WarnOutcome),
        (Action.RETRY_WITH_FEEDBACK, RetryOutcome),
        (Action.ESCALATE_TO_HUMAN, EscalateOutcome),
    ],
)
def test_apply_action_dispatches_to_typed_outcome(action: Action, expected_type: type) -> None:
    decision = _decision(action)
    ctx = GuardrailContext(hook="post_llm", response="x")
    outcome = apply_action(decision, ctx)
    assert isinstance(outcome, expected_type)


def test_apply_action_redact_and_transform_via_dispatch() -> None:
    ctx = GuardrailContext(hook="post_llm", response="leak sk-zzz here")
    redact_decision = _decision(Action.REDACT, hook="post_llm", payload={"matches": ["sk-zzz"]})
    redacted = apply_action(redact_decision, ctx)
    assert isinstance(redacted, RedactOutcome)
    assert "sk-zzz" not in redacted.context.response  # type: ignore[operator]

    transform_decision = _decision(Action.TRANSFORM, hook="post_llm")
    transformed = apply_action(
        transform_decision, ctx, transformer=str.upper, transformer_name="upper"
    )
    assert isinstance(transformed, TransformOutcome)
    assert transformed.context.response == "LEAK SK-ZZZ HERE"


def test_apply_action_transform_requires_transformer() -> None:
    decision = _decision(Action.TRANSFORM, hook="post_llm")
    ctx = GuardrailContext(hook="post_llm", response="x")
    with pytest.raises(GuardrailError):
        apply_action(decision, ctx)  # no transformer supplied


def test_apply_action_returns_none_when_nothing_triggered() -> None:
    decision = PipelineDecision(hook="pre_llm", triggered=False, action=None, outcomes=[])
    ctx = GuardrailContext(hook="pre_llm", prompt="all clear")
    assert apply_action(decision, ctx) is None


# --------------------------------------------------------------------------- #
# End-to-end: a real pipeline decision flows through redact correctly.        #
# --------------------------------------------------------------------------- #


def test_pipeline_redact_end_to_end_masks_from_real_match() -> None:
    cfg = parse_config(
        {
            "guardrails": {
                "post_llm": [
                    {
                        "type": "regex",
                        "action": "redact",
                        "config": {"pattern": "sk-[a-z0-9]{6,}"},
                    }
                ]
            }
        }
    )
    pipeline = GuardrailPipeline(cfg)
    ctx = GuardrailContext(hook="post_llm", response="here is sk-abc123def the key")
    decision = pipeline.run(ctx)
    assert decision.action is Action.REDACT

    outcome = apply_action(decision, ctx, mask="***")
    assert isinstance(outcome, RedactOutcome)
    assert outcome.context.response == "here is *** the key"
    assert ctx.response == "here is sk-abc123def the key"  # original intact


# --------------------------------------------------------------------------- #
# Copy helper: nested mutable fields are independent.                          #
# --------------------------------------------------------------------------- #


def test_copy_context_is_deep_for_mutable_fields() -> None:
    original = GuardrailContext(
        hook="pre_tool",
        tool_name="shell",
        tool_args={"cmd": ["rm", "-rf"]},
        metadata={"tenant_id": "t1", "nested": {"a": 1}},
    )
    clone = _copy_context(original)
    clone.tool_args["cmd"].append("/")
    clone.metadata["nested"]["a"] = 2
    # Mutating the clone does not touch the original.
    assert original.tool_args["cmd"] == ["rm", "-rf"]
    assert original.metadata["nested"]["a"] == 1


def test_gather_spans_dedups_and_orders_longest_first() -> None:
    outcomes = [
        GuardrailOutcome(
            type="a",
            triggered=True,
            severity=Severity.HIGH,
            detail="",
            action=Action.REDACT,
            payload={"matches": ["ab", "abcd", "ab"]},
        ),
        GuardrailOutcome(
            type="b",
            triggered=False,
            severity=Severity.INFO,
            detail="",
            action=None,
            payload={"matches": ["ignored"]},
        ),
    ]
    assert _gather_spans(outcomes) == ["abcd", "ab"]


# A multi-action signal: a guardrail-rich post_llm where the spans from two
# triggering guardrails are both masked.
def test_redact_masks_spans_from_multiple_guardrails() -> None:
    original = GuardrailContext(hook="post_llm", response="email a@b.com and key sk-xyz")
    decision = PipelineDecision(
        hook="post_llm",
        triggered=True,
        action=Action.REDACT,
        outcomes=[
            GuardrailOutcome(
                type="pii",
                triggered=True,
                severity=Severity.HIGH,
                detail="pii",
                action=Action.REDACT,
                payload={"matches": ["a@b.com"]},
            ),
            GuardrailOutcome(
                type="secret",
                triggered=True,
                severity=Severity.CRITICAL,
                detail="secret",
                action=Action.REDACT,
                payload={"matches": ["sk-xyz"]},
            ),
        ],
    )
    outcome = apply_redact(decision, original, mask="#")
    assert outcome.context.response == "email # and key #"
    assert outcome.severity is Severity.CRITICAL  # highest among the two
