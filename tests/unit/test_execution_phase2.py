"""Phase-2 audit fixes for the worker execution pipeline (E-execution unit).

Pure / session-isolable slices of the conduct_execution fixes:

  - P2.1 / F11: ``transition_task_after_run('cancelled')`` -> ``cancelled`` (was
    wrongly folded into the ``blocked`` else-branch).
  - P1.1 / F16: ``_assemble_result`` recovers a dropped ``execution.finished`` line
    from the COMPLETE captured logs before declaring a clean exit a failure.
  - P1.3 / F19: ``Settings.container_timeout_with_grace_for_kind`` gives the
    container a grace margin ABOVE the loop's internal wall-clock budget.

The transition helper is exercised with a fake session/task (no DB); the DB-bound
paths are covered by ``tests/integration/test_execution_task_transition.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from api_server.db.domain import TaskStatus
from workers.config import Settings
from workers.execution import (
    _assemble_result,
    _scan_logs_for_terminal,
    transition_task_after_run,
)


class _FakeSession:
    """Minimal async ``session.get`` stub returning a single seeded task."""

    def __init__(self, task: Any) -> None:
        self._task = task

    async def get(self, _model: Any, _key: Any) -> Any:
        return self._task


def _task(status: str, *, reviewer_agent_id: Any = None) -> SimpleNamespace:
    return SimpleNamespace(status=status, reviewer_agent_id=reviewer_agent_id, completed_at=None)


# --------------------------------------------------------------------------- P2.1


@pytest.mark.asyncio
async def test_transition_cancelled_maps_to_cancelled() -> None:
    """F11: an operator cancel lands the task in ``cancelled``, not ``blocked``."""
    task = _task(TaskStatus.IN_PROGRESS.value)
    ev = await transition_task_after_run(_FakeSession(task), uuid4(), "cancelled")
    assert task.status == TaskStatus.CANCELLED.value
    assert ev is not None
    assert ev[1] == TaskStatus.IN_PROGRESS.value
    assert ev[2] == TaskStatus.CANCELLED.value


@pytest.mark.asyncio
async def test_transition_failed_still_blocks() -> None:
    """Regression guard: the new cancelled branch must not change ``failed``."""
    task = _task(TaskStatus.IN_PROGRESS.value)
    ev = await transition_task_after_run(_FakeSession(task), uuid4(), "failed")
    assert task.status == TaskStatus.BLOCKED.value
    assert ev is not None and ev[2] == TaskStatus.BLOCKED.value


@pytest.mark.asyncio
async def test_transition_done_with_reviewer_goes_to_review() -> None:
    task = _task(TaskStatus.IN_PROGRESS.value, reviewer_agent_id=uuid4())
    ev = await transition_task_after_run(_FakeSession(task), uuid4(), "done")
    assert task.status == TaskStatus.IN_REVIEW.value
    assert ev is not None and ev[2] == TaskStatus.IN_REVIEW.value


@pytest.mark.asyncio
async def test_transition_cancelled_guarded_when_not_in_progress() -> None:
    """A task already moved off in_progress is left alone (idempotent guard)."""
    task = _task(TaskStatus.CANCELLED.value)
    ev = await transition_task_after_run(_FakeSession(task), uuid4(), "cancelled")
    assert ev is None
    assert task.status == TaskStatus.CANCELLED.value


# --------------------------------------------------------------------------- P1.1


_FINISHED_LINE = (
    '{"event": "execution.finished", "result": {"status": "done", '
    '"output": "wrote files", "iterations": 4, "finish_status": "success", '
    '"usage": {"total_tokens": 12, "cost_usd": 0.1, "tool_calls": 2, "model_calls": 3}}}'
)


def test_scan_logs_recovers_finished_result() -> None:
    finished, error = _scan_logs_for_terminal("some free text\n" + _FINISHED_LINE + "\nmore noise")
    assert error is None
    assert finished is not None
    assert finished["status"] == "done"
    assert finished["finish_status"] == "success"


def test_scan_logs_recovers_error() -> None:
    finished, error = _scan_logs_for_terminal('{"event": "execution.error", "error": "boom"}')
    assert finished is None
    assert error == "boom"


def test_assemble_result_recovers_finished_from_logs() -> None:
    """F16: a clean exit (0, no timeout) with NO live result line recovers the
    dropped ``execution.finished`` from the full captured logs."""
    result = _assemble_result(
        None,
        [],
        timed_out=False,
        exit_code=0,
        runtime_error=None,
        logs=_FINISHED_LINE,
    )
    assert result.status == "done"
    assert result.output == "wrote files"
    assert result.iterations == 4
    assert result.finish_status == "success"
    assert result.usage["total_tokens"] == 12


def test_assemble_result_recovers_error_from_logs() -> None:
    """A recovered ``execution.error`` line surfaces as the failure detail rather
    than the generic 'exited 0 with no result'."""
    result = _assemble_result(
        None,
        [],
        timed_out=False,
        exit_code=0,
        runtime_error=None,
        logs='{"event": "execution.error", "error": "kaboom"}',
    )
    assert result.status == "failed"
    assert "kaboom" in (result.output or "")


def test_assemble_result_no_recovery_on_nonzero_exit() -> None:
    """A crash (non-zero exit) keeps the hard-failure path — recovery is only for a
    clean exit whose terminal line was lost on the wire."""
    result = _assemble_result(
        None,
        [],
        timed_out=False,
        exit_code=137,
        runtime_error=None,
        logs=_FINISHED_LINE,
    )
    assert result.status == "failed"
    assert "exited 137" in (result.output or "")


def test_assemble_result_no_recovery_on_timeout() -> None:
    result = _assemble_result(
        None,
        [],
        timed_out=True,
        exit_code=0,
        runtime_error=None,
        logs=_FINISHED_LINE,
    )
    assert result.status == "failed"
    assert "timed out" in (result.output or "")


def test_assemble_result_live_result_wins_over_logs() -> None:
    """When the live stream DID capture the result, it is authoritative — the log
    re-scan is only a fallback."""
    live = {"status": "done", "output": "live", "iterations": 1, "usage": {}}
    result = _assemble_result(
        live, [], timed_out=False, exit_code=0, runtime_error=None, logs=_FINISHED_LINE
    )
    assert result.output == "live"


# --------------------------------------------------------------------------- P1.3


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = {
        "container_run_timeout_s": 600,
        "container_run_timeout_claude_sdk_s": 7200,
        "container_grace_s": 120,
    }
    base.update(kw)
    return Settings(**base)


def test_container_grace_above_internal_budget() -> None:
    """F19: the container's hard kill timeout exceeds the loop's internal wall-clock
    budget by exactly the grace, so the clean internal abort fires first."""
    s = _settings()
    assert s.container_timeout_with_grace_for_kind(None) == 720
    assert s.container_timeout_with_grace_for_kind("ollama") == 720
    assert s.container_timeout_with_grace_for_kind("claude_sdk") == 7320
    # The internal budget (the bare per-kind timeout) is strictly below the
    # container's hard kill for every kind.
    for kind in (None, "ollama", "claude_sdk"):
        assert s.container_timeout_for_kind(kind) < s.container_timeout_with_grace_for_kind(kind)


def test_container_grace_is_tunable() -> None:
    s = _settings(container_grace_s=300)
    assert s.container_timeout_with_grace_for_kind(None) == 900
    assert s.container_timeout_with_grace_for_kind("claude_sdk") == 7500
