"""Integration tests: plan → pending_human_validation transition
(Plan 06 task_06_36)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _task(status: str, cost: float = 0.0, tid: str = "t") -> object:
    from api_server.plan_progress import TaskSnapshot

    return TaskSnapshot(id=tid, status=status, cost_eur=cost)


def test_all_done_tasks_transitions() -> None:
    from api_server.plan_progress import transition_to_pending_human_validation

    tasks = [_task("done", 100.0, "t1"), _task("done", 50.0, "t2")]
    result = transition_to_pending_human_validation("in_progress", tasks)

    assert result.transitioned is True
    assert result.new_status == "pending_human_validation"


def test_open_tasks_block_transition() -> None:
    from api_server.plan_progress import transition_to_pending_human_validation

    tasks = [_task("done", tid="t1"), _task("backlog", tid="t2")]
    result = transition_to_pending_human_validation("in_progress", tasks)
    assert result.transitioned is False
    assert "1 task(s) still open" in (result.reason or "")


def test_in_review_blocks_transition() -> None:
    """A task in `in_review` (the worker is reviewing it) is NOT done
    yet — the plan must wait."""
    from api_server.plan_progress import transition_to_pending_human_validation

    tasks = [_task("done", tid="t1"), _task("in_review", tid="t2")]
    result = transition_to_pending_human_validation("in_progress", tasks)
    assert result.transitioned is False


def test_cancelled_tasks_dont_block() -> None:
    """A cancelled task is excluded from the open count."""
    from api_server.plan_progress import transition_to_pending_human_validation

    tasks = [_task("done", tid="t1"), _task("cancelled", tid="t2")]
    result = transition_to_pending_human_validation("in_progress", tasks)
    assert result.transitioned is True


def test_non_in_progress_plan_doesnt_transition() -> None:
    """The transition only fires when the plan is `in_progress`."""
    from api_server.plan_progress import transition_to_pending_human_validation

    result = transition_to_pending_human_validation("pending_approval", [_task("done")])
    assert result.transitioned is False


def test_awaiting_human_approval_blocks_transition() -> None:
    # An open task (parked for human approval) keeps the plan out of
    # pending_human_validation. F43: the canonical status is `awaiting_human_approval`
    # (the orphan `awaiting_human` never existed in any enum / state machine).
    from api_server.plan_progress import transition_to_pending_human_validation

    tasks = [_task("done", tid="t1"), _task("awaiting_human_approval", tid="t2")]
    result = transition_to_pending_human_validation("in_progress", tasks)
    assert result.transitioned is False
