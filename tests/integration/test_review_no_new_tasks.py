"""Integration test: auto-review NEVER creates sibling tasks
(Plan 06 task_06_34b1 — auto_06_34b1_b)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_rejection_only_comments_and_transitions() -> None:
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
        )
    )
    lc = TaskLifecycle(store=store)

    initial_count = sum(1 for _ in store.list_by_status("plan-1", "backlog"))
    lc.reject_review(
        "t1",
        comment=ReviewComment(failed_criterion="x", testreport_evidence="y", what_to_fix="z"),
    )
    final_count = sum(1 for _ in store.list_by_status("plan-1", "backlog"))
    # Same task is back in backlog — count went 0 → 1, but no NEW task was
    # created (only the existing t1 transitioned).
    assert final_count - initial_count == 1
    assert store.get("t1") is not None
