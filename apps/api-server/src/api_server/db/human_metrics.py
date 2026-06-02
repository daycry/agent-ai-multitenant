"""Per-user human-agent performance metrics (Plan 16 task_16_10).

Pure aggregation over the two task_16_03 / task_16_05 audit tables — the
:class:`~api_server.db.domain.HumanWorkSession` rows a user produced and the
:class:`~api_server.db.domain.HumanTaskAssignment` rows they were assigned. The
metrics summarise how a concrete User performs at human tasks:

  * **mean acceptance time** — how long, on average, the user takes to accept an
    assignment after it lands on them (``accepted`` assignments only): the gap
    between ``assigned_at`` and the moment the row flipped to ``accepted``
    (``updated_at`` — the only post-creation mutation an assignment row sees);
  * **mean execution time** — how long, on average, a closed work session ran
    (``end_at - start_at``, ``end_at IS NOT NULL`` only);
  * **first-try approval rate** — the fraction of the user's worked tasks that
    were delivered in a SINGLE work session (no re-submission after a peer
    rejection sent the task back for another round); and
  * **mean hours logged** — the average of the (optional) ``hours_logged`` the
    user recorded, when present.

These feed future PM estimates (task_16_13: the planner reuses
:func:`compute_user_metrics` to size human tasks), so the function is a plain,
reusable Domain-Service helper that takes the ``user_id`` + ``tenant_id``
explicitly rather than reaching for request state.

Multi-tenancy (NON-NEGOTIABLE)
------------------------------
Every aggregate is filtered on BOTH ``tenant_id`` (belt-and-braces over RLS) and
``user_id``, so the numbers are strictly per-user AND tenant-scoped. The caller
(``GET /inbox/metrics``) only ever passes its OWN ``principal.user_id``; the work
session ``user_id`` is the same column the inbox submit (task_16_09) stamps.

Empty history
-------------
A user who has never worked a human task yields well-defined zeros for the
counts and ``None`` for every mean / rate (you cannot average over zero rows),
so the UI / planner can render "sin datos aún" rather than a misleading 0.0.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Float, Integer, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import (
    HumanTaskAssignment,
    HumanTaskAssignmentStatus,
    HumanWorkSession,
)


@dataclass(frozen=True, slots=True)
class HumanUserMetrics:
    """Aggregated performance metrics for one User's human-task work.

    All time figures are **seconds** (floats) so the wire format is
    unit-agnostic; the UI formats them. Means / rates are ``None`` when there is
    nothing to average (empty history), distinct from a genuine ``0.0``.
    """

    #: Distinct tasks the user produced at least one work session for.
    tasks_worked: int
    #: Total closed work sessions (end_at set) the user authored.
    work_sessions_completed: int
    #: Assignments that reached ``accepted`` (the acceptance-time sample).
    assignments_accepted: int
    #: Mean assigned_at -> accepted gap, seconds. ``None`` if no accepted rows.
    mean_acceptance_time_seconds: float | None
    #: Mean closed-session duration, seconds. ``None`` if no closed sessions.
    mean_execution_time_seconds: float | None
    #: Fraction (0..1) of worked tasks delivered in one session. ``None`` if no
    #: worked tasks.
    first_try_approval_rate: float | None
    #: Mean of the user's logged hours (when present). ``None`` if none logged.
    mean_hours_logged: float | None


def _as_float(value: float | Decimal | None) -> float | None:
    """Coerce a SQL numeric/interval-seconds result to ``float`` (or ``None``)."""
    if value is None:
        return None
    return float(value)


async def compute_user_metrics(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    user_id: UUID,
) -> HumanUserMetrics:
    """Compute :class:`HumanUserMetrics` for one User within one tenant.

    Three independent aggregate queries (kept separate so each reads cleanly and
    none cross-joins the others into a fan-out):

      1. work sessions — distinct worked tasks, closed-session count, the mean
         execution time (``end_at - start_at``) and the mean logged hours;
      2. accepted assignments — count + the mean acceptance gap
         (``updated_at - assigned_at``);
      3. first-try rate — of the tasks the user worked, the share that needed
         exactly one work session (one delivery, no rejection-and-redo round).

    All filtered on ``tenant_id`` AND ``user_id``.
    """
    # 1) Work-session aggregates: distinct tasks, closed sessions, mean
    #    execution seconds, mean logged hours.
    exec_seconds = func.extract("epoch", HumanWorkSession.end_at - HumanWorkSession.start_at)
    ws_row = (
        await session.execute(
            select(
                func.count(func.distinct(HumanWorkSession.task_id)),
                func.count().filter(HumanWorkSession.end_at.isnot(None)),
                func.avg(exec_seconds).filter(HumanWorkSession.end_at.isnot(None)),
                func.avg(cast(HumanWorkSession.hours_logged, Float)).filter(
                    HumanWorkSession.hours_logged.isnot(None)
                ),
            ).where(
                HumanWorkSession.tenant_id == tenant_id,
                HumanWorkSession.user_id == user_id,
            )
        )
    ).one()
    tasks_worked = int(ws_row[0] or 0)
    work_sessions_completed = int(ws_row[1] or 0)
    mean_execution_time_seconds = _as_float(ws_row[2])
    mean_hours_logged = _as_float(ws_row[3])

    # 2) Acceptance aggregates: accepted-assignment count + mean acceptance gap.
    #    `updated_at` carries the moment the row flipped to `accepted` (the only
    #    post-creation update an assignment receives).
    accept_seconds = func.extract(
        "epoch", HumanTaskAssignment.updated_at - HumanTaskAssignment.assigned_at
    )
    acc_row = (
        await session.execute(
            select(
                func.count(),
                func.avg(accept_seconds),
            ).where(
                HumanTaskAssignment.tenant_id == tenant_id,
                HumanTaskAssignment.assigned_to_user_id == user_id,
                HumanTaskAssignment.status == HumanTaskAssignmentStatus.ACCEPTED.value,
            )
        )
    ).one()
    assignments_accepted = int(acc_row[0] or 0)
    mean_acceptance_time_seconds = _as_float(acc_row[1])

    # 3) First-try approval rate: per worked task, count its work sessions; a
    #    task delivered in exactly one session was approved first-try (no
    #    rejection-and-redo). Rate = first-try tasks / worked tasks.
    per_task = (
        select(
            HumanWorkSession.task_id.label("task_id"),
            func.count().label("n_sessions"),
        )
        .where(
            HumanWorkSession.tenant_id == tenant_id,
            HumanWorkSession.user_id == user_id,
        )
        .group_by(HumanWorkSession.task_id)
        .subquery()
    )
    ft_row = (
        await session.execute(
            select(
                func.count(),
                func.sum(cast(per_task.c.n_sessions == 1, Integer)),
            )
        )
    ).one()
    worked_task_count = int(ft_row[0] or 0)
    first_try_tasks = int(ft_row[1] or 0)
    first_try_approval_rate = first_try_tasks / worked_task_count if worked_task_count else None

    return HumanUserMetrics(
        tasks_worked=tasks_worked,
        work_sessions_completed=work_sessions_completed,
        assignments_accepted=assignments_accepted,
        mean_acceptance_time_seconds=mean_acceptance_time_seconds,
        mean_execution_time_seconds=mean_execution_time_seconds,
        first_try_approval_rate=first_try_approval_rate,
        mean_hours_logged=mean_hours_logged,
    )


__all__ = ["HumanUserMetrics", "compute_user_metrics"]
