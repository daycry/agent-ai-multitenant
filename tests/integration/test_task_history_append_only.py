"""Integration tests: history is append-only (Plan 06 task_06_34b6 — fourth contract)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_terminal_task_rejects_status_change() -> None:
    from api_server.task_lifecycle import InMemoryTaskStore, TaskLifecycle

    store = InMemoryTaskStore()
    lc = TaskLifecycle(store=store)
    t = lc.create_free_task(plan_id="p", title="x", description="x")
    t.status = "done"
    store.save(t)

    # Now try to reopen.
    t = store.get(t.id)
    t.status = "in_progress"
    with pytest.raises(ValueError, match="closed"):
        store.save(t)


def test_history_events_are_not_mutable_after_emission() -> None:
    """Audit events are immutable: store doesn't expose any method
    to modify a past event, only append new ones. We pin that
    discipline by treating AuditEvent as frozen."""
    from dataclasses import FrozenInstanceError

    from api_server.task_lifecycle import (
        InMemoryTaskStore,
        TaskLifecycle,
    )

    store = InMemoryTaskStore()
    lc = TaskLifecycle(store=store)
    task = lc.create_free_task(plan_id="p", title="x", description="x")
    history = lc.history(task.id)
    with pytest.raises(FrozenInstanceError):
        history[0].kind = "tampered"  # type: ignore[misc]
