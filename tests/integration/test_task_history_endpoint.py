"""Integration tests: GET /api/v1/tasks/{id}/history shape
(Plan 06 task_06_34b6 — primary contract)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_history_returns_events_in_chronological_order() -> None:
    from api_server.task_lifecycle import (
        InMemoryTaskStore,
        ReviewComment,
        TaskLifecycle,
        TaskRecord,
    )

    store = InMemoryTaskStore()
    store.save(TaskRecord(id="t", plan_id="p", title="x", description="x", status="in_review"))
    lc = TaskLifecycle(store=store)
    lc.reject_review(
        "t",
        comment=ReviewComment(failed_criterion="x", testreport_evidence="y", what_to_fix="z"),
    )

    history = lc.history("t")
    assert len(history) >= 2
    # Chronologically ordered.
    for i in range(1, len(history)):
        assert history[i].at >= history[i - 1].at


def test_history_includes_all_event_kinds() -> None:
    """Creation, review_comment, transition, human_action — all four
    appear in the history of a task that went through the full cycle."""
    from api_server.task_lifecycle import (
        InMemoryTaskStore,
        ReviewComment,
        TaskLifecycle,
    )

    store = InMemoryTaskStore()
    lc = TaskLifecycle(store=store)

    # 1. Creation from checkbox.
    task = lc.create_task_from_checkbox(
        plan_id="p", checkbox_id="hc", checkbox_text="x", reviewer_comment="x"
    )
    # 2. Set status manually to in_review (simulating worker pickup).
    task.status = "in_review"
    store.save(task)
    # 3. Reject (review_comment + transition).
    lc.reject_review(
        task.id,
        comment=ReviewComment(failed_criterion="a", testreport_evidence="b", what_to_fix="c"),
    )
    # 4. Hit max retries → escalation. Force by setting retry_count.
    t = store.get(task.id)
    t.retry_count = 3
    t.status = "backlog"
    store.save(t)
    lc.escalate_if_exhausted(t)
    # 5. Human action.
    lc.apply_human_action(task.id, "approve_manual", actor="alice")

    kinds = {e.kind for e in lc.history(task.id)}
    assert "creation" in kinds
    assert "review_comment" in kinds
    assert "transition" in kinds
    assert "human_action" in kinds
