"""Integration tests: plan cannot complete while spawned tasks are open
(Plan 06 task_06_34b4 — third contract).

The check itself lives in the orchestrator (it queries the task store
for the plan's open tasks). Here we pin the *query* side: a plan
with spawned tasks in ``backlog`` returns a non-empty list.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_plan_has_open_tasks_after_checkbox_fail() -> None:
    from api_server.task_lifecycle import InMemoryTaskStore, TaskLifecycle

    store = InMemoryTaskStore()
    lc = TaskLifecycle(store=store)
    lc.create_task_from_checkbox(
        plan_id="plan-X",
        checkbox_id="human_06_03",
        checkbox_text="aux services isolated",
        reviewer_comment="ran into shared DB",
    )

    open_tasks = [
        t
        for status in ("backlog", "in_progress", "in_review", "awaiting_human_approval")
        for t in store.list_by_status("plan-X", status)
    ]
    assert len(open_tasks) == 1


def test_plan_has_no_open_tasks_once_spawned_tasks_done() -> None:
    from api_server.task_lifecycle import (
        InMemoryTaskStore,
        TaskLifecycle,
    )

    store = InMemoryTaskStore()
    lc = TaskLifecycle(store=store)
    t = lc.create_task_from_checkbox(
        plan_id="plan-X",
        checkbox_id="human_06_03",
        checkbox_text="aux services isolated",
        reviewer_comment="ran into shared DB",
    )
    # Mark done.
    t.status = "done"
    store.save(t)

    open_tasks = [
        t
        for status in ("backlog", "in_progress", "in_review", "awaiting_human_approval")
        for t in store.list_by_status("plan-X", status)
    ]
    assert open_tasks == []
