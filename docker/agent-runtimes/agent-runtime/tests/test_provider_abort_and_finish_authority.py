"""Provider-error → clean abort + finish_status authority (F25/P1.5, F27, P2.2).

Three audit fixes on the agent loop, driven through ``run_agent`` end to end:

  * F25/P1.5 — a provider error that survived Phase-1's retry+timeout (a typed
    ``LLMError`` re-raised by ``providers._run_with_retry``) is caught in the
    graph nodes (`plan`'s decide, `self_review`'s review) and ends the run
    cleanly ABORTED with a ``provider_*`` code — NOT a container crash that the
    worker would double to a hard ``failed`` (losing all progress). A NON-provider
    error (a real bug) still propagates.
  * F27 — the escalation/provider codes live on ``SafeguardCode`` (one source of
    truth); the persisted string values are unchanged.
  * P2.2 (ADR 0087 addendum D1) — a review PASS may NOT turn the agent's OWN
    admission of failure (``finish_status`` failed/partial) into ``done``; it is
    escalated to ``needs_human_review`` (``agent_reported_failure``).
"""

from __future__ import annotations

from typing import Any

import pytest
from agent_runtime.graph import AgentDeps, run_agent
from agent_runtime.model import (
    DecisionKind,
    ModelDecision,
    ModelResponse,
    ReviewResponse,
)
from agent_runtime.providers import ProviderTimeout
from agent_runtime.safeguards import SafeguardCode
from agent_runtime.state import (
    STATUS_ABORTED,
    STATUS_DONE,
    STATUS_NEEDS_HUMAN_REVIEW,
    AgentTask,
)
from shared_llm import ProviderError, RateLimitError

_TASK: AgentTask = {"id": "t1", "title": "T", "description": "d"}


def _finish(*, finish_status: str | None = None) -> ModelResponse:
    return ModelResponse(
        decision=ModelDecision(
            kind=DecisionKind.FINISH, output="el entregable", finish_status=finish_status
        )
    )


class _FakeModel:
    """A ModelClient that finishes immediately, with injectable failures.

    ``decide_exc`` / ``review_exc`` make the corresponding call raise (simulating
    the typed error Phase-1 re-raises); otherwise ``decide`` finishes with
    ``finish_status`` and ``review`` returns ``review``.
    """

    def __init__(
        self,
        *,
        decide_exc: BaseException | None = None,
        review_exc: BaseException | None = None,
        finish_status: str | None = None,
        review: ReviewResponse | None = None,
    ) -> None:
        self._decide_exc = decide_exc
        self._review_exc = review_exc
        self._finish_status = finish_status
        self._review = review or ReviewResponse(passed=True)
        self.review_calls = 0

    def decide(self, state: dict[str, Any]) -> ModelResponse:  # noqa: ARG002
        if self._decide_exc is not None:
            raise self._decide_exc
        return _finish(finish_status=self._finish_status)

    def review(self, state: dict[str, Any]) -> ReviewResponse:  # noqa: ARG002
        self.review_calls += 1
        if self._review_exc is not None:
            raise self._review_exc
        return self._review


def _run(model: _FakeModel) -> Any:
    return run_agent(AgentDeps(model=model), _TASK)  # type: ignore[arg-type]


# --- F25/P1.5: decide() raises a provider error → clean abort ------------------
def test_decide_provider_timeout_aborts_cleanly() -> None:
    result = _run(_FakeModel(decide_exc=ProviderTimeout("LLM call exceeded 900s budget")))
    assert result.status == STATUS_ABORTED
    assert result.abort_code == SafeguardCode.PROVIDER_TIMEOUT == "provider_timeout"


def test_decide_provider_error_aborts_cleanly() -> None:
    result = _run(_FakeModel(decide_exc=RateLimitError("429 slow down")))
    assert result.status == STATUS_ABORTED
    assert result.abort_code == SafeguardCode.PROVIDER_ERROR == "provider_error"
    # The run never reached the review — it aborted in plan().
    # (steps still carry the aborted plan node, so progress is preserved.)
    assert any(s.get("status") == "aborted" for s in result.steps)


def test_decide_5xx_provider_error_aborts_cleanly() -> None:
    result = _run(_FakeModel(decide_exc=ProviderError("upstream 503", status_code=503)))
    assert result.status == STATUS_ABORTED
    assert result.abort_code == "provider_error"


# --- F25/P1.5: review() raises a provider error → clean abort ------------------
def test_review_provider_error_aborts_cleanly() -> None:
    model = _FakeModel(review_exc=ProviderTimeout("review hung"))
    result = _run(model)
    assert model.review_calls == 1  # decide finished, review was attempted
    assert result.status == STATUS_ABORTED
    assert result.abort_code == "provider_timeout"
    # finalize ran before self_review, so the deliverable is preserved on abort.
    assert result.output == "el entregable"


# --- F25 boundary: a NON-provider error is a real bug → it propagates ----------
def test_non_provider_error_propagates() -> None:
    # A KeyError is NOT an LLMError: the except must not swallow real bugs.
    with pytest.raises(KeyError):
        _run(_FakeModel(decide_exc=KeyError("boom")))


# --- P2.2: review PASS cannot override a self-reported failure -----------------
def test_review_pass_with_finish_status_failed_escalates() -> None:
    result = _run(_FakeModel(finish_status="failed", review=ReviewResponse(passed=True)))
    assert result.status == STATUS_NEEDS_HUMAN_REVIEW
    assert result.abort_code == SafeguardCode.AGENT_REPORTED_FAILURE == "agent_reported_failure"
    # The self-reported status rides through to the result for the UI.
    assert result.finish_status == "failed"


def test_review_pass_with_finish_status_partial_escalates() -> None:
    result = _run(_FakeModel(finish_status="partial", review=ReviewResponse(passed=True)))
    assert result.status == STATUS_NEEDS_HUMAN_REVIEW
    assert result.abort_code == "agent_reported_failure"


def test_review_pass_with_finish_status_success_is_done() -> None:
    # The happy path is unchanged: an admitted SUCCESS + a review pass → done.
    result = _run(_FakeModel(finish_status="success", review=ReviewResponse(passed=True)))
    assert result.status == STATUS_DONE
    assert result.abort_code is None


def test_review_pass_with_no_finish_status_is_done() -> None:
    # The claude_sdk prose path reports no status → a pass is still a clean done.
    result = _run(_FakeModel(finish_status=None, review=ReviewResponse(passed=True)))
    assert result.status == STATUS_DONE
