"""DAG promotion — promote plan tasks from ``backlog`` to ``ready``.

prod-06 task_prod06_dag_02. A plan's tasks are materialised in ``backlog`` and
only become dispatchable once they reach ``ready`` **and** the orchestrator
receives the ``task.status_changed`` (new_status=ready) event its dispatcher
consumes. Two gaps this closes:

  - **Root tasks** (zero dependencies) were never promoted: the DB trigger
    ``fn_compute_task_ready`` (migration 0009) only promotes a task's
    *dependents* when it reaches ``done``, so a freshly-started plan never left
    ``backlog`` — nothing dispatched without a human moving cards by hand.
  - The trigger flips ``status`` in the DB but **cannot publish the event** the
    dispatcher consumes, so even trigger-promoted tasks could strand in ``ready``
    with no dispatch.

:func:`promote_ready_tasks` is the single promotion primitive, called at plan
start (roots), on each task ``done`` (newly-eligible siblings) and from a
periodic beat (safety net). It:

  1. flips every eligible ``backlog`` task of the plan to ``ready`` (a task is
     eligible when it has no dependency that is not yet ``done`` — roots qualify
     vacuously); then
  2. returns every ``ready`` task of the plan that has **no execution row yet**
     — the undispatched ones the caller must announce with a ready event.

Returning the *undispatched* set (not only the just-flipped ones) means a task
the trigger flipped without an event is still announced, and the operation stays
idempotent: once a task has been dispatched (an ``executions`` row exists) it is
never re-announced. A per-plan transaction-scoped advisory lock serialises
concurrent promoters (start vs. beat vs. on-done) so a task is never announced
twice in the same instant.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import exists, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from api_server.db.domain import Execution, Task, TaskDependency, TaskStatus
from api_server.events import publish_task_status_changed

_BACKLOG = TaskStatus.BACKLOG.value
_READY = TaskStatus.READY.value
_DONE = TaskStatus.DONE.value


async def promote_ready_tasks(session: AsyncSession, plan_id: UUID) -> list[Task]:
    """Promote eligible ``backlog`` tasks of ``plan_id`` to ``ready``.

    Returns the plan's ``ready`` tasks that have no execution row yet — the
    undispatched set the caller announces with a ready event. The caller owns the
    transaction (this only flushes); publish AFTER the commit via
    :func:`announce_ready_tasks`.
    """
    # Per-plan advisory lock (transaction-scoped) so concurrent promoters
    # (start-execution, the on-done handler and the beat) never double-announce.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:plan, 0))"),
        {"plan": str(plan_id)},
    )

    # 1. Flip eligible backlog -> ready. A task is eligible when NO dependency of
    #    it is still un-done; a root (no dependency rows) is eligible vacuously.
    dep_task = aliased(Task)
    unmet_dependency = (
        select(TaskDependency.task_id)
        .join(dep_task, dep_task.id == TaskDependency.depends_on_task_id)
        .where(TaskDependency.task_id == Task.id, dep_task.status != _DONE)
    )
    eligible = (
        (
            await session.execute(
                select(Task.id).where(
                    Task.plan_id == plan_id,
                    Task.status == _BACKLOG,
                    ~exists(unmet_dependency),
                )
            )
        )
        .scalars()
        .all()
    )
    if eligible:
        await session.execute(update(Task).where(Task.id.in_(eligible)).values(status=_READY))

    # 2. Collect every ready task of the plan that has not been dispatched yet
    #    (no executions row). Idempotent: a dispatched/running task is skipped.
    undispatched = (
        (
            await session.execute(
                select(Task).where(
                    Task.plan_id == plan_id,
                    Task.status == _READY,
                    ~exists(select(Execution.id).where(Execution.task_id == Task.id)),
                )
            )
        )
        .scalars()
        .all()
    )
    return list(undispatched)


async def announce_ready_tasks(redis: object, tasks: list[Task]) -> None:
    """Publish the ready ``task.status_changed`` event for each task so the
    orchestrator dispatcher picks it up. Best-effort (``publish_*`` swallows its
    own broker errors); call AFTER the promoting transaction has committed."""
    for task in tasks:
        await publish_task_status_changed(
            redis,  # type: ignore[arg-type]
            task,
            old_status=_BACKLOG,
            new_status=_READY,
        )


__all__ = ["announce_ready_tasks", "promote_ready_tasks"]
