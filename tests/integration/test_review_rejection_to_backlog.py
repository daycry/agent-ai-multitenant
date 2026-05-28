"""Integration tests: auto-reviewer rejection sends task back to backlog
(Plan 06 task_06_34b1)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def _store_with_task(task_id: str = "t1") -> object:
    from api_server.task_lifecycle import InMemoryTaskStore, TaskRecord

    store = InMemoryTaskStore()
    store.save(
        TaskRecord(
            id=task_id,
            plan_id="plan-1",
            title="implement X",
            description="...",
            status="in_review",
        )
    )
    return store


def test_rejection_returns_task_to_backlog_with_comment() -> None:
    from api_server.task_lifecycle import ReviewComment, TaskLifecycle

    store = _store_with_task()
    lc = TaskLifecycle(store=store)

    comment = ReviewComment(
        failed_criterion="auto_06_01_a",
        testreport_evidence="exit_code=1",
        what_to_fix="add the missing import",
    )
    task = lc.reject_review("t1", comment=comment)

    assert task.status == "backlog"
    assert task.retry_count == 1

    history = lc.history("t1")
    kinds = [e.kind for e in history]
    assert "review_comment" in kinds
    assert "transition" in kinds


def test_rejection_increments_retry_count() -> None:
    from api_server.task_lifecycle import ReviewComment, TaskLifecycle

    store = _store_with_task()
    lc = TaskLifecycle(store=store)
    comment = ReviewComment(failed_criterion="x", testreport_evidence="y", what_to_fix="z")
    lc.reject_review("t1", comment=comment)
    # Task is in backlog now; flip it back to in_review and reject again.
    t = store.get("t1")
    t.status = "in_review"
    store.save(t)
    lc.reject_review("t1", comment=comment)

    assert store.get("t1").retry_count == 2


def test_reviewer_does_not_create_new_tasks() -> None:
    """task_06_34b1 contract: rejection only adds a comment + transitions;
    it does NOT spawn a sibling task. We verify by counting tasks."""
    from api_server.task_lifecycle import ReviewComment, TaskLifecycle

    store = _store_with_task()
    lc = TaskLifecycle(store=store)
    lc.reject_review(
        "t1",
        comment=ReviewComment(failed_criterion="x", testreport_evidence="y", what_to_fix="z"),
    )

    # Same task count.
    assert len(list(store.list_by_status("plan-1", "backlog"))) == 1
    assert store.get("t1") is not None


def test_review_no_new_tasks() -> None:
    """Alias test for the plan's auto_06_34b1_b id."""
    test_reviewer_does_not_create_new_tasks()
