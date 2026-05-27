"""Integration tests: checkbox-spawned tasks are plan-scoped
(Plan 06 task_06_34b4 — second contract)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_new_tasks_carry_plan_id() -> None:
    from api_server.task_lifecycle import InMemoryTaskStore, TaskLifecycle

    store = InMemoryTaskStore()
    lc = TaskLifecycle(store=store)
    t1 = lc.create_task_from_checkbox(
        plan_id="plan-A",
        checkbox_id="hc1",
        checkbox_text="thing 1",
        reviewer_comment="fix",
    )
    t2 = lc.create_task_from_checkbox(
        plan_id="plan-A",
        checkbox_id="hc2",
        checkbox_text="thing 2",
        reviewer_comment="fix",
    )

    # Both belong to plan-A.
    assert t1.plan_id == "plan-A"
    assert t2.plan_id == "plan-A"
    # List of plan-A backlog includes both.
    backlog = list(store.list_by_status("plan-A", "backlog"))
    assert {b.id for b in backlog} == {t1.id, t2.id}


def test_tasks_from_different_plans_dont_mix() -> None:
    from api_server.task_lifecycle import InMemoryTaskStore, TaskLifecycle

    store = InMemoryTaskStore()
    lc = TaskLifecycle(store=store)
    lc.create_task_from_checkbox(
        plan_id="plan-A", checkbox_id="x", checkbox_text="x", reviewer_comment="x"
    )
    lc.create_task_from_checkbox(
        plan_id="plan-B", checkbox_id="y", checkbox_text="y", reviewer_comment="y"
    )

    plan_a_backlog = list(store.list_by_status("plan-A", "backlog"))
    plan_b_backlog = list(store.list_by_status("plan-B", "backlog"))
    assert len(plan_a_backlog) == 1
    assert len(plan_b_backlog) == 1
    assert plan_a_backlog[0].plan_id == "plan-A"
    assert plan_b_backlog[0].plan_id == "plan-B"
