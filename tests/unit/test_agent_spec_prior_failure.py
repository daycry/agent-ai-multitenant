"""P0-7: _agent_spec forwards `prior_failure` to the container spec.

Mirrors the prior_review_feedback rail: the orchestrator threads the previous
non-review failure ({status, abort_code, output_tail}); the worker forwards it
so the runtime folds a corrective preamble. Absent → no key (backward-compat).
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from workers.execution import ExecutionRequest, _agent_spec


def _request(failure: dict[str, Any] | None) -> ExecutionRequest:
    return ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(uuid4()),
        agent_id=str(uuid4()),
        task={"id": "t-1", "title": "x", "description": ""},
        model={"kind": "ollama"},
        prior_failure=failure,
    )


def test_prior_failure_forwarded_when_present() -> None:
    failure = {"status": "aborted", "abort_code": "loop_detected", "output_tail": "…"}
    spec = _agent_spec(_request(failure), None)
    assert spec["prior_failure"] == failure


def test_no_prior_failure_key_when_absent() -> None:
    assert "prior_failure" not in _agent_spec(_request(None), None)


def test_roundtrip_preserves_prior_failure() -> None:
    failure = {"status": "failed", "abort_code": None, "output_tail": "t"}
    rebuilt = ExecutionRequest.from_dict(_request(failure).as_dict())
    assert rebuilt.prior_failure == failure
