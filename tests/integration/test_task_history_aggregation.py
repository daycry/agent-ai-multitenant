"""Integration tests: history aggregates N retries of the same task
(Plan 06 task_06_34b6 — second contract)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_n_retries_have_n_reviews_and_n_transitions() -> None:
    from api_server.task_lifecycle import (
        InMemoryTaskStore,
        ReviewComment,
        TaskLifecycle,
        TaskRecord,
    )

    store = InMemoryTaskStore()
    store.save(
        TaskRecord(
            id="t", plan_id="p", title="x", description="x", status="in_review", max_retries=5
        )
    )
    lc = TaskLifecycle(store=store)
    comment = ReviewComment(failed_criterion="x", testreport_evidence="y", what_to_fix="z")
    for _ in range(3):
        task = store.get("t")
        task.status = "in_review"
        store.save(task)
        lc.reject_review("t", comment=comment)

    history = lc.history("t")
    reviews = [e for e in history if e.kind == "review_comment"]
    transitions = [e for e in history if e.kind == "transition"]
    assert len(reviews) == 3
    # Each rejection emits one transition; total 3.
    assert len(transitions) == 3
