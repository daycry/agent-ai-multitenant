"""Integration tests: review-exhaustion escalation (Plan 06 task_06_34b2).

F43: the escalation target is the canonical ``blocked`` state (not the orphan
``awaiting_human`` that existed in no enum / state-machine table) — consistent
with ``reviewer_bridge`` and CLAUDE.md ppio 7.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_third_rejection_escalates() -> None:
    from api_server.task_lifecycle import (
        InMemoryTaskStore,
        ReviewComment,
        TaskLifecycle,
        TaskRecord,
    )

    store = InMemoryTaskStore()
    store.save(
        TaskRecord(
            id="t1",
            plan_id="plan-1",
            title="implement",
            description="...",
            status="in_review",
            max_retries=3,
        )
    )

    notifications: list[tuple[str, int]] = []

    class Notifier:
        def notify_escalation(self, task, history):
            notifications.append((task.id, task.retry_count))

    lc = TaskLifecycle(store=store, notifier=Notifier())

    comment = ReviewComment(failed_criterion="x", testreport_evidence="y", what_to_fix="z")
    for _ in range(3):
        # Flip back to in_review between rejections (mocks the worker's
        # pick-up of the backlog'd task).
        task = store.get("t1")
        task.status = "in_review"
        store.save(task)
        lc.reject_review("t1", comment=comment)

    task = store.get("t1")
    # F43: escalation lands on `blocked`, not the orphan `awaiting_human`.
    assert task.status == "blocked"
    assert task.retry_count == 3
    assert notifications == [("t1", 3)]


def test_below_max_retries_stays_in_backlog() -> None:
    from api_server.task_lifecycle import (
        InMemoryTaskStore,
        ReviewComment,
        TaskLifecycle,
        TaskRecord,
    )

    store = InMemoryTaskStore()
    store.save(
        TaskRecord(
            id="t1",
            plan_id="plan-1",
            title="x",
            description="x",
            status="in_review",
            max_retries=3,
        )
    )
    lc = TaskLifecycle(store=store)
    lc.reject_review(
        "t1",
        comment=ReviewComment(failed_criterion="x", testreport_evidence="y", what_to_fix="z"),
    )
    assert store.get("t1").status == "backlog"


def test_human_actions_reset_or_close_task() -> None:
    """The four buttons on the escalated panel — each one transitions
    the task into a different terminal/restart state."""
    from api_server.task_lifecycle import (
        InMemoryTaskStore,
        TaskLifecycle,
        TaskRecord,
    )

    def fresh_lc():
        store = InMemoryTaskStore()
        store.save(
            TaskRecord(
                id="t",
                plan_id="p",
                title="x",
                description="x",
                status="blocked",  # F43: escalated tasks live in `blocked`
                retry_count=3,
            )
        )
        return TaskLifecycle(store=store), store

    # approve_manual → done + manual_approval=True
    lc, _store = fresh_lc()
    t = lc.apply_human_action("t", "approve_manual", actor="alice")
    assert t.status == "done" and t.manual_approval

    # reassign_with_guidance → backlog + retry_count=0
    lc, _store = fresh_lc()
    t = lc.apply_human_action(
        "t", "reassign_with_guidance", actor="alice", guidance="try X instead"
    )
    assert t.status == "backlog" and t.retry_count == 0

    # block_with_reason → blocked
    lc, _store = fresh_lc()
    t = lc.apply_human_action("t", "block_with_reason", actor="alice", reason="waiting on API")
    assert t.status == "blocked"

    # cancel → cancelled
    lc, _store = fresh_lc()
    t = lc.apply_human_action("t", "cancel", actor="alice")
    assert t.status == "cancelled"


def test_human_actions_recorded_in_audit_log() -> None:
    from api_server.task_lifecycle import (
        InMemoryTaskStore,
        TaskLifecycle,
        TaskRecord,
    )

    store = InMemoryTaskStore()
    store.save(
        TaskRecord(
            id="t",
            plan_id="p",
            title="x",
            description="x",
            status="blocked",  # F43: escalated tasks live in `blocked`
            retry_count=3,
        )
    )
    lc = TaskLifecycle(store=store)
    lc.apply_human_action("t", "block_with_reason", actor="alice@team.test", reason="external dep")

    history = lc.history("t")
    actions = [e for e in history if e.kind == "human_action"]
    assert len(actions) == 1
    assert actions[0].actor == "alice@team.test"
    assert actions[0].payload["reason"] == "external dep"
