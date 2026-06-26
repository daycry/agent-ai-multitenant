"""Runtime DAG enforcement on task transitions (Plan 03 task_03_30).

Once a plan is materialised into the Kanban, the dependency graph
lives in ``task_dependencies``. The orchestrator (Plan 02) reads it
to decide which tasks are ready, but the REST surface needs the same
guard so a user can't manually drag a card into ``in_progress`` while
upstream work is still pending.

This module exposes one pure-async helper, ``assert_dependencies_done``,
that the tasks router calls before letting a PUT promote a task to a
gated status. The router maps ``DependenciesNotDoneError`` to a 422 with
the offending dependency ids so the UI can render the explanation in-place.

Gated targets are the ones that imply the task is being moved *forward*
through its DAG order: ``ready`` (eligible to start), ``in_progress``,
``awaiting_human_approval`` and ``in_review``. A user must not be able to
hand-promote a card into ``ready`` (let alone start it) while upstream
work is still pending — the Kanban shows a padlock on such cards and the
server refuses the move. Moving a card *back/aside* to ``backlog``,
``blocked``, ``done`` or ``cancelled`` stays free (no DAG precondition).

Note the automatic promotion ``backlog -> ready`` (the ``fn_compute_task_ready``
DB trigger / ``promote_ready_tasks``) bypasses this REST guard entirely and
only ever fires once all dependencies are ``done``, so gating ``ready`` here
constrains *manual* moves without touching the autonomous DAG cascade.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Task, TaskDependency, TaskStatus

# Target statuses that move a task FORWARD through its DAG order and must
# therefore wait on all dependencies being ``done``. ``ready`` is included so
# a card can't be hand-dragged past a pending dependency (the autonomous
# backlog->ready promotion bypasses this guard — see module docstring).
GATED_TARGET_STATUSES: frozenset[str] = frozenset(
    {
        TaskStatus.READY.value,
        TaskStatus.IN_PROGRESS.value,
        TaskStatus.AWAITING_HUMAN_APPROVAL.value,
        TaskStatus.IN_REVIEW.value,
    }
)


@dataclass(frozen=True)
class PendingDependency:
    """One upstream task that is not yet ``done``."""

    task_id: UUID
    status: str


class DependenciesNotDoneError(ValueError):
    """Raised when a gated transition is requested while one or more
    upstream task dependencies are still pending.

    ``pending`` lists every blocking dependency (task_id + its current
    status) so the router can return a full audit in a single 422.
    """

    def __init__(self, pending: Iterable[PendingDependency]):
        self.pending = list(pending)
        super().__init__(
            "dependencies not done: " + ", ".join(f"{p.task_id}={p.status}" for p in self.pending)
        )


async def assert_dependencies_done(
    session: AsyncSession,
    task_id: UUID,
    target_status: str,
) -> None:
    """Validate that ``task_id`` can move to ``target_status``.

    Does nothing for non-gated statuses. For gated ones, loads every
    upstream dependency and raises ``DependenciesNotDoneError`` if any
    is not ``done``. The session is the tenant-scoped one used by the
    router, so RLS still applies to the lookup.
    """
    if target_status not in GATED_TARGET_STATUSES:
        return
    result = await session.execute(
        select(Task.id, Task.status)
        .join(TaskDependency, TaskDependency.depends_on_task_id == Task.id)
        .where(TaskDependency.task_id == task_id)
    )
    pending: list[PendingDependency] = []
    for dep_id, dep_status in result.all():
        if dep_status != TaskStatus.DONE.value:
            pending.append(PendingDependency(task_id=dep_id, status=dep_status))
    if pending:
        raise DependenciesNotDoneError(pending)


__all__ = [
    "GATED_TARGET_STATUSES",
    "DependenciesNotDoneError",
    "PendingDependency",
    "assert_dependencies_done",
]
