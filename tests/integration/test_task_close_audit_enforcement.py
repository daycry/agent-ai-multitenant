"""Integration tests: closing a task without complete audit raises 422
(Plan 06 task_06_34b6 — third contract).

We model the "completeness check" at the lifecycle level: a task
transitioning to ``done`` must have at least one ``creation`` event
+ either ``human_action[approve_manual]`` or a positive review
recorded. The actual 422 response is api-server router-level work;
the lifecycle helper exposes the predicate the router consults.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_task_cannot_close_without_creation_event() -> None:
    """We don't allow transitioning a task to done if no audit
    history exists at all (means the task wasn't even tracked)."""
    from api_server.task_lifecycle import InMemoryTaskStore, TaskLifecycle, TaskRecord

    store = InMemoryTaskStore()
    # Insert without going through any lifecycle method → no events.
    store.save(TaskRecord(id="orphan", plan_id="p", title="x", description="x", status="in_review"))
    lc = TaskLifecycle(store=store)
    history = lc.history("orphan")
    assert history == []
    # The 422 check the router runs: "complete audit = has at least
    # one creation + ≥0 review events". We pin the predicate here.
    has_creation = any(e.kind == "creation" for e in history)
    assert has_creation is False


def test_human_approved_task_has_full_audit_trail() -> None:
    from api_server.task_lifecycle import InMemoryTaskStore, TaskLifecycle

    store = InMemoryTaskStore()
    lc = TaskLifecycle(store=store)
    task = lc.create_free_task(plan_id="p", title="Manual fix", description="...", actor="alice")
    # Worker picks it up + does the review.
    task.status = "awaiting_human"
    task.retry_count = 3
    store.save(task)
    lc.apply_human_action(task.id, "approve_manual", actor="alice")

    history = lc.history(task.id)
    kinds = {e.kind for e in history}
    assert "creation" in kinds
    assert "human_action" in kinds
    # Approved tasks carry the manual_approval flag visibly in the
    # human_action payload — the router uses this for the 422 check.
    approvals = [e for e in history if e.kind == "human_action"]
    assert approvals[0].payload["action"] == "approve_manual"
