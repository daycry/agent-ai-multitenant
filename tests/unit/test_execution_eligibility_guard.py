"""R5: a re-delivered run_execution must NOT launch a runtime for a task the
operator has since moved out of the launchable state.

Celery `acks_late` re-delivers an in-flight message after a worker restart (the
recovery for R1's hang). Without a guard, `conduct_execution` re-creates an
execution and launches a container even though the task is now `blocked` /
`cancelled` — the "phantom docker" the operator saw. The eligibility check is a
pure function so its policy is pinned offline; the end-to-end no-op is covered
by an integration test.
"""

from __future__ import annotations

from api_server.db.domain import TaskStatus
from workers.execution import _task_is_launchable


def test_implementer_launchable_only_when_in_progress() -> None:
    assert _task_is_launchable(TaskStatus.IN_PROGRESS.value, is_review=False) is True
    for status in (
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
        TaskStatus.DONE,
        TaskStatus.BACKLOG,
        TaskStatus.IN_REVIEW,
    ):
        assert _task_is_launchable(status.value, is_review=False) is False, status


def test_reviewer_launchable_only_when_in_review() -> None:
    assert _task_is_launchable(TaskStatus.IN_REVIEW.value, is_review=True) is True
    for status in (
        TaskStatus.BLOCKED,
        TaskStatus.CANCELLED,
        TaskStatus.DONE,
        TaskStatus.IN_PROGRESS,
    ):
        assert _task_is_launchable(status.value, is_review=True) is False, status
