"""Runtime DAG enforcement on task transitions (Plan 03 task_03_30).

Once a plan is materialised into the Kanban, the dependency graph
lives in ``task_dependencies``. The orchestrator (Plan 02) reads it
to decide which tasks are ready, but the REST surface needs the same
guard so a user can't manually drag a card into ``in_progress`` while
upstream work is still pending.

This module exposes one pure-async helper, ``assert_dependencies_done``,
that the tasks router calls before letting a PUT promote a task to
``in_progress`` / ``awaiting_human_approval`` / ``in_review``. The
router maps ``DependenciesNotDoneError`` to a 422 with the offending
dependency ids so the UI can render the explanation in-place.

We intentionally keep the list of "starts-work" target statuses small:
moving a card to ``ready``, ``blocked``, ``done`` or ``cancelled`` is
free — only transitions that imply *the agent will start spending
budget on it* must wait on the upstream graph.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Task, TaskDependency, TaskStatus

# Target statuses that count as "starting work" and must therefore wait
# on all dependencies being ``done``.
GATED_TARGET_STATUSES: frozenset[str] = frozenset(
    {
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
