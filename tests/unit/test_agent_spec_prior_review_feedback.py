"""_agent_spec forwards the AI reviewer's prior feedback to the container for a
RE-DISPATCHED implementer run (A2 / inter-run feedback).

A task the reviewer rejected loops in_review → backlog → ready and is re-routed to
the implementer. The orchestrator threads the reviewer's prior rejection payloads as
`prior_review_feedback`; the worker must forward them into the AGENT_TASK_SPEC so the
runtime can tell the implementer what to fix. Absent → no key (backward-compat).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from workers.execution import ExecutionRequest, _agent_spec


def _request(prior: list[dict[str, Any]] | None) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=str(uuid4()),
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "ollama"},
        prior_review_feedback=prior,
    )


def test_prior_review_feedback_forwarded_when_present() -> None:
    feedback = [
        {
            "failed_criterion": "missing regression test",
            "what_to_fix": "add a test for the empty-list case",
            "testreport_evidence": "pytest: 0 collected",
        }
    ]
    spec = _agent_spec(_request(feedback), None)
    assert spec["prior_review_feedback"] == feedback


def test_no_prior_review_feedback_key_when_absent() -> None:
    spec = _agent_spec(_request(None), None)
    assert "prior_review_feedback" not in spec


def test_empty_list_is_still_forwarded() -> None:
    # `None` is the "no prior rejection" sentinel; an explicit `[]` (should not occur
    # in practice — the orchestrator omits the key) is forwarded verbatim, mirroring
    # the `allowed_tools` None-vs-[] distinction the spec keeps elsewhere.
    spec = _agent_spec(_request([]), None)
    assert spec["prior_review_feedback"] == []


def test_roundtrip_preserves_prior_review_feedback() -> None:
    feedback = [{"failed_criterion": "c", "what_to_fix": "f", "testreport_evidence": "e"}]
    rebuilt = ExecutionRequest.from_dict(_request(feedback).as_dict())
    assert rebuilt.prior_review_feedback == feedback
