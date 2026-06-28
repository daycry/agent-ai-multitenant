"""_agent_spec forwards the review context to the container for a REVIEW run
(audit cluster C1 / F51).

The orchestrator builds `review_context`, but until this fix the worker never
forwarded it into the AGENT_TASK_SPEC, so the reviewer ran blind and every
reviewed task was defensively rejected.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from workers.execution import ExecutionRequest, _agent_spec


def _review_request(review: bool, review_context: dict[str, Any] | None = None) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=str(uuid4()),
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "ollama"},
        review=review,
        review_context=review_context,
    )


def test_review_context_forwarded_when_review() -> None:
    ctx = {"acceptance_criteria": "c", "implementer_output": "o", "test_report": ""}
    spec = _agent_spec(_review_request(True, ctx), None)
    assert spec["review"] is True
    assert spec["review_context"] == ctx


def test_review_flag_set_even_without_context() -> None:
    spec = _agent_spec(_review_request(True, None), None)
    assert spec["review"] is True
    assert "review_context" not in spec


def test_no_review_keys_for_normal_run() -> None:
    spec = _agent_spec(_review_request(False), None)
    assert "review" not in spec
    assert "review_context" not in spec
