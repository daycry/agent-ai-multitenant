"""Integration tests: failed human checkbox → new plan-scoped task
(Plan 06 task_06_34b4 — primary contract)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_checkbox_failure_creates_task_with_text_and_comment() -> None:
    from api_server.task_lifecycle import InMemoryTaskStore, TaskLifecycle

    store = InMemoryTaskStore()
    lc = TaskLifecycle(store=store)
    new_task = lc.create_task_from_checkbox(
        plan_id="plan-1",
        checkbox_id="human_06_01",
        checkbox_text="Ciclo end-to-end de un plan con repo git",
        reviewer_comment="Falla el push tras la primera task",
    )

    assert new_task.plan_id == "plan-1"
    assert new_task.title.startswith("Ciclo end-to-end")
    assert "Falla el push" in new_task.description
    assert new_task.status == "backlog"
    assert new_task.parent_checkbox_id == "human_06_01"
    assert new_task.is_free_task is False


def test_creation_recorded_in_audit_log() -> None:
    from api_server.task_lifecycle import InMemoryTaskStore, TaskLifecycle

    store = InMemoryTaskStore()
    lc = TaskLifecycle(store=store)
    task = lc.create_task_from_checkbox(
        plan_id="plan-1",
        checkbox_id="human_06_02",
        checkbox_text="Cache works",
        reviewer_comment="No volvió a usar la cache en el segundo run",
    )

    events = lc.history(task.id)
    creations = [e for e in events if e.kind == "creation"]
    assert len(creations) == 1
    assert creations[0].payload["from_checkbox"] == "human_06_02"
    assert creations[0].payload["is_free_task"] is False
