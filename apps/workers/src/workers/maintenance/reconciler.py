"""Convergence reconciler — `workers.reconcile_pipeline_state`, every 90s
(audit C3 / P0.6).

The live event path moves a task/plan off a transient state the instant a run
finishes, but an event can be lost (Redis blip, a worker SIGKILLed between the
finalize txn and the publish) — leaving DERIVED state stuck: a task `in_progress`
whose run already finished, an `in_review` task whose review was never dispatched,
or an `in_progress` plan whose tasks are all done. Nothing else reconciles these,
so the DAG silently stalls. This beat is the net: four idempotent best-effort
passes that re-derive the state from the DB and re-emit the events the live path
would have. Age thresholds keep it from racing a worker still post-processing.
Pass (d) — the M4 worktree back-fill — lives in
:mod:`workers.maintenance.worktree_backfill`.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.maintenance.worktree_backfill import _reconcile_unpushed_worktrees

_log = structlog.get_logger("workers.maintenance")

# A task must sit `in_progress` (and its terminal execution must be settled) this
# long before we act, so we never compete with a worker still in its post-run
# processing (worktree commit / tests / deferred event publish).
_RECONCILE_STUCK_TASK_MIN_AGE = timedelta(minutes=5)
# An `in_review` task with an AI reviewer must sit this long with no live/recent
# review run before we re-announce it — avoids double-dispatching a review whose
# `in_review` event the orchestrator is still processing.
_RECONCILE_REVIEW_MIN_AGE = timedelta(minutes=5)
# The reconciler's OWN escalation cap (M5), independent of the ADR 0095-D3 cap that
# only advances when a review execution reaches `_apply_review_verdict`. Two real
# paths leave D3 stuck forever: the Celery broker down (no dispatch → no execution →
# retry_count untouched) and a review worker SIGKILL/OOM (the zombie sweeper closes
# the run but `transition_task_after_run` no-ops on an `in_review` task, so
# retry_count never bumps). Past this age with no live/recent review run, the task
# is escalated to a human (`blocked`) instead of re-announcing indefinitely.
_RECONCILE_REVIEW_MAX_STUCK = timedelta(hours=1)

# Execution statuses that mean the run is OVER — the owning task must no longer be
# `in_progress`. Literal mirror of the terminal ``ExecutionStatus`` members, kept as
# strings so importing this module costs no api_server import. ``running`` and
# ``awaiting_human_approval`` are deliberately absent (a live run / an approval the
# approval branch owns — not the reconciler's concern).
_TERMINAL_EXECUTION_STATUSES = frozenset(
    {"done", "failed", "aborted", "cancelled", "needs_human_review"}
)


def _stuck_task_needs_reconcile(
    latest_exec_status: str | None,
    latest_exec_completed_at: datetime | None,
    *,
    now: datetime,
    min_age: timedelta,
) -> bool:
    """True when an `in_progress` task's LATEST execution is terminal and settled
    long enough that the task should be transitioned off `in_progress` (case a).

    Pure decision — no DB — so the candidate filter is unit-testable in isolation.
    A non-terminal (still `running`/`awaiting_human_approval`) or not-yet-settled
    latest execution is left alone (a worker may still be finishing it)."""
    if latest_exec_status is None or latest_exec_status not in _TERMINAL_EXECUTION_STATUSES:
        return False
    if latest_exec_completed_at is None:
        return False
    return latest_exec_completed_at <= now - min_age


def _orphan_review_needs_reannounce(
    *,
    reviewer_is_ai: bool,
    has_running_execution: bool,
    latest_completed_at: datetime | None,
    now: datetime,
    min_age: timedelta,
) -> bool:
    """True when an `in_review` task with an AI reviewer has NO live review run and
    nothing ran recently, so its `in_review` event should be re-announced (case b).

    Pure decision — no DB. A human reviewer is the peer-review path's concern; a
    running execution means the review is already in flight; a recently-completed
    execution means a run just finished (the implementer that moved it to review, or
    a review whose verdict is being applied) — in both we wait rather than duplicate."""
    if not reviewer_is_ai or has_running_execution:
        return False
    return latest_completed_at is None or latest_completed_at <= now - min_age


def _orphan_review_should_escalate(
    *,
    task_updated_at: datetime,
    now: datetime,
    max_stuck: timedelta,
) -> bool:
    """True when an `in_review` task has sat stuck past the reconciler's own cap (M5).

    Pure decision — no DB. ``Task.updated_at`` (``onupdate=func.now()``, untouched by a
    re-announce) tells how long the task has been degenerate without real progress.
    Past ``max_stuck`` the reconciler escalates to a human (``blocked``) rather than
    re-announcing the lost review forever — this is the cap the ADR 0095-D3 verdict
    path can't reach when the broker is down or a review worker was SIGKILL-ed."""
    return task_updated_at <= now - max_stuck


async def _reconcile_stuck_tasks(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Any,
    *,
    now: datetime,
    min_age: timedelta,
) -> int:
    """Case (a): transition tasks stuck `in_progress` whose last run is terminal.

    Reuses ``workers.execution.transition_task_after_run`` (the SAME dag_01 policy
    the worker applies: done→in_review/done, cancelled→cancelled, else→blocked) and
    re-emits the resulting ``task.status_changed`` so the board + the orchestrator
    converge. Per-task transaction + the `in_progress` guard inside
    ``transition_task_after_run`` make it idempotent and safe against a worker that
    wins the race. Returns how many tasks were transitioned."""
    from api_server.db.domain import Execution, Task, TaskStatus
    from api_server.events import publish_task_status_changed
    from sqlalchemy import select

    from workers.execution import transition_task_after_run

    cutoff = now - min_age
    async with sessionmaker() as db:
        candidate_ids = list(
            (
                await db.execute(
                    select(Task.id).where(
                        Task.status == TaskStatus.IN_PROGRESS.value,
                        Task.started_at < cutoff,
                    )
                )
            ).scalars()
        )
    reconciled = 0
    for task_id in candidate_ids:
        event: tuple[Any, str, str] | None = None
        async with sessionmaker() as db, db.begin():
            latest = (
                (
                    await db.execute(
                        select(Execution)
                        .where(Execution.task_id == task_id)
                        .order_by(Execution.created_at.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if latest is None or not _stuck_task_needs_reconcile(
                latest.status, latest.completed_at, now=now, min_age=min_age
            ):
                continue
            event = await transition_task_after_run(db, task_id, latest.status)
        if event is not None:
            task_obj, old, new = event
            await publish_task_status_changed(redis, task_obj, old_status=old, new_status=new)
            _log.info(
                "maintenance.reconcile_pipeline_state.stuck_task_reconciled",
                task_id=str(task_id),
                old_status=old,
                new_status=new,
            )
            reconciled += 1
    return reconciled


async def _reconcile_orphan_reviews(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Any,
    *,
    now: datetime,
    min_age: timedelta,
    max_stuck: timedelta = _RECONCILE_REVIEW_MAX_STUCK,
) -> int:
    """Case (b): re-announce `in_review` for AI-reviewed tasks whose review is lost,
    OR escalate to a human when it has been stuck too long (M5 cap).

    An `in_review` task with an AI ``reviewer_agent_id``, no `running` execution and
    no recently-finished run had its review dispatch lost (the `in_review` event
    never reached the orchestrator). Re-publishing ``task.status_changed`` with
    ``new_status=in_review`` makes ``orchestrator._on_task_in_review`` re-dispatch the
    review. Best-effort and idempotent — the orchestrator re-checks live state and
    no-ops on a stale re-announce.

    But re-announcing forever is a loop when nothing will ever advance the ADR
    0095-D3 verdict cap (broker down / review worker SIGKILL-ed). So past
    ``max_stuck`` (measured on ``Task.updated_at``) we escalate to ``blocked`` with an
    audit event instead of re-announcing — the reconciler's own, verdict-independent
    cap. Returns how many tasks were re-announced OR escalated."""
    from api_server.db.domain import (
        Agent,
        AgentType,
        Execution,
        ExecutionStatus,
        Task,
        TaskStatus,
    )
    from api_server.db.task_audit_repo import append_audit_event
    from api_server.events import publish_task_status_changed
    from api_server.task_state_machine import transition_task_status
    from sqlalchemy import func, select

    cutoff = now - min_age
    async with sessionmaker() as db:
        candidates = list(
            (
                await db.execute(
                    select(
                        Task.id,
                        Task.tenant_id,
                        Task.project_id,
                        Task.reviewer_agent_id,
                        Task.updated_at,
                    ).where(
                        Task.status == TaskStatus.IN_REVIEW.value,
                        Task.reviewer_agent_id.isnot(None),
                        Task.updated_at < cutoff,
                    )
                )
            ).all()
        )
    reannounced = 0
    for row in candidates:
        async with sessionmaker() as db:
            reviewer = await db.get(Agent, row.reviewer_agent_id)
            reviewer_is_ai = reviewer is not None and reviewer.agent_type != AgentType.HUMAN.value
            running = (
                (
                    await db.execute(
                        select(Execution.id)
                        .where(
                            Execution.task_id == row.id,
                            Execution.status == ExecutionStatus.RUNNING.value,
                        )
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            latest_completed = (
                await db.execute(
                    select(func.max(Execution.completed_at)).where(Execution.task_id == row.id)
                )
            ).scalar_one_or_none()
        if not _orphan_review_needs_reannounce(
            reviewer_is_ai=reviewer_is_ai,
            has_running_execution=running is not None,
            latest_completed_at=latest_completed,
            now=now,
            min_age=min_age,
        ):
            continue
        # M5 cap: stuck past the ceiling with no live/recent review → escalate to a
        # human instead of re-announcing forever (the D3 verdict cap never fires here).
        if _orphan_review_should_escalate(
            task_updated_at=row.updated_at, now=now, max_stuck=max_stuck
        ):
            async with sessionmaker() as db, db.begin():
                task = await db.get(Task, row.id)
                # Idempotency: only escalate if still in_review (the live path may
                # have moved it since the candidate SELECT).
                if task is None or task.status != TaskStatus.IN_REVIEW.value:
                    continue
                transition_task_status(task, TaskStatus.BLOCKED.value)
                await append_audit_event(
                    db,
                    tenant_id=row.tenant_id,
                    task_id=row.id,
                    kind="review_comment",
                    actor="reconciler",
                    payload={"escalated": True, "reason": "review_stuck_reconcile_cap"},
                )
            task_ref = Task(id=row.id, tenant_id=row.tenant_id, project_id=row.project_id)
            await publish_task_status_changed(
                redis,
                task_ref,
                old_status=TaskStatus.IN_REVIEW.value,
                new_status=TaskStatus.BLOCKED.value,
            )
            _log.warning(
                "maintenance.reconcile_pipeline_state.review_escalated_stuck",
                task_id=str(row.id),
            )
            reannounced += 1
            continue
        # A transient Task is just the value carrier the publisher reads
        # (id/tenant/project) — same pattern the dispatcher uses.
        task_ref = Task(id=row.id, tenant_id=row.tenant_id, project_id=row.project_id)
        await publish_task_status_changed(
            redis,
            task_ref,
            old_status=TaskStatus.IN_REVIEW.value,
            new_status=TaskStatus.IN_REVIEW.value,
        )
        _log.info(
            "maintenance.reconcile_pipeline_state.review_reannounced",
            task_id=str(row.id),
        )
        reannounced += 1
    return reannounced


async def _reconcile_complete_plans(sessionmaker: async_sessionmaker[AsyncSession]) -> int:
    """Case (c): flip `in_progress` plans whose tasks are ALL terminal to
    `pending_human_validation` AND auto-start their review-runtime.

    Mirrors ``orchestrator._on_task_done`` exactly — the SAME plan state machine
    (``transition_to_pending_human_validation``) + the SAME atomic ``WHERE
    status=in_progress`` guard — so the reconciler never diverges and can never
    double-transition a plan the live path already moved. Returns how many plans
    transitioned.

    Convergence GAP fix: the live ``done`` path auto-starts the review-runtime
    (``_on_task_done`` → ``compose_review_runtime``); when that event is LOST only
    the reconciler moves the plan, and until now it stopped at the transition —
    leaving the plan stalled in ``pending_human_validation`` with NO review_session
    (the reviewer URLs 404, human validation never arms). On a winning transition we
    now fire the SAME shared autostart (``_autostart_review_runtime``), idempotent
    and best-effort, so the two paths converge."""
    from api_server.db.domain import Plan, PlanStatus, Task, TaskDependency
    from api_server.plan_progress import (
        PlanStatus as PlanStatusLiteral,  # el StrEnum del dominio ya se llama PlanStatus aquí
    )
    from api_server.plan_progress import (
        TaskSnapshot,
        transition_to_blocked,
        transition_to_pending_human_validation,
    )
    from sqlalchemy import select, update

    async with sessionmaker() as db:
        plan_rows = list(
            (
                await db.execute(
                    select(Plan.id, Plan.tenant_id).where(
                        Plan.status == PlanStatus.IN_PROGRESS.value
                    )
                )
            ).all()
        )
    transitioned = 0
    for prow in plan_rows:
        won = False
        async with sessionmaker() as db, db.begin():
            task_rows = list(
                (
                    await db.execute(
                        select(Task.id, Task.status).where(
                            Task.plan_id == prow.id,
                            Task.tenant_id == prow.tenant_id,
                        )
                    )
                ).all()
            )
            if not task_rows:
                continue
            plan = await db.get(Plan, prow.id)
            if plan is None:
                continue
            # prod-06 A1: cargar dependencias para el cierre transitivo del
            # escalado a blocked (un backlog atascado tras un blocked/cancelled).
            dep_rows = list(
                (
                    await db.execute(
                        select(TaskDependency.task_id, TaskDependency.depends_on_task_id).where(
                            TaskDependency.task_id.in_([r.id for r in task_rows])
                        )
                    )
                ).all()
            )
            deps_by_task: dict[str, list[str]] = {}
            for dr in dep_rows:
                deps_by_task.setdefault(str(dr.task_id), []).append(str(dr.depends_on_task_id))
            snapshots = [
                TaskSnapshot(
                    id=str(r.id),
                    status=r.status,
                    depends_on=tuple(deps_by_task.get(str(r.id), ())),
                )
                for r in task_rows
            ]
            # La columna es `str`; el Literal refleja el StrEnum del dominio 1:1
            # (mypy-total 2026-07-08) — cast, no conversión.
            plan_status = cast(PlanStatusLiteral, plan.status)
            result = transition_to_pending_human_validation(plan_status, snapshots)
            # prod-06 A1: safety-net del escalado a blocked cuando el evento
            # `_on_task_done` del orchestrator se perdió — el mismo camino que el
            # dispatch, aquí como red del beat (un plan atascado NO se queda
            # in_progress para siempre sin señal al operador).
            if not result.transitioned:
                result = transition_to_blocked(plan_status, snapshots)
            if not result.transitioned:
                continue
            won_id = (
                await db.execute(
                    update(Plan)
                    .where(
                        Plan.id == prow.id,
                        Plan.tenant_id == prow.tenant_id,
                        Plan.status == PlanStatus.IN_PROGRESS.value,
                    )
                    .values(status=result.new_status)
                    .returning(Plan.id)
                )
            ).scalar_one_or_none()
            if won_id is not None:
                _log.info(
                    "maintenance.reconcile_pipeline_state.plan_transitioned",
                    plan_id=str(prow.id),
                    new_status=result.new_status,
                )
                transitioned += 1
                # El autostart del review-runtime solo aplica al camino
                # pending_human_validation, NO a blocked.
                won = result.new_status == "pending_human_validation"
        # GAP fix: build + enqueue the review-runtime autostart in a SEPARATE read
        # session AFTER the transition txn commits (broker I/O must never hold a DB
        # txn open; a build/enqueue failure must never touch the committed move).
        if won:
            await _autostart_review_runtime(sessionmaker, plan_id=prow.id, tenant_id=prow.tenant_id)
    return transitioned


async def _autostart_review_runtime(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    plan_id: Any,
    tenant_id: Any,
) -> None:
    """Best-effort: build + enqueue the review-runtime autostart for a plan the
    reconciler just moved to ``pending_human_validation`` (convergence GAP fix).

    Delegates to ``api_server.review_autostart.build_review_autostart_request`` — the
    SINGLE source of truth shared with ``orchestrator._on_task_done`` — so the live
    path and the reconciler can never diverge. IDEMPOTENT: the builder returns
    ``None`` when an active (``running``/``suspended``) review session already exists
    for the plan, so a double pass (live + reconciler, or two reconciler passes) never
    spawns a second runtime. Wrapped so a bad row / a broker blip NEVER breaks the
    reconciler pass or the already-committed transition; the autostart simply retries
    on a later pass / the operator."""
    from api_server.db.domain import Plan
    from api_server.review_autostart import build_review_autostart_request

    try:
        async with sessionmaker() as db:
            plan = await db.get(Plan, plan_id)
            if plan is None:
                return
            request = await build_review_autostart_request(db, plan=plan, tenant_id=tenant_id)
        if request is None:
            return
        await asyncio.to_thread(_send_compose_review_runtime, request)
        _log.info(
            "maintenance.reconcile_pipeline_state.review_runtime_autostarted",
            plan_id=str(plan_id),
        )
    except Exception as exc:  # never break the reconciler pass / the committed move
        _log.warning(
            "maintenance.reconcile_pipeline_state.review_autostart_failed",
            plan_id=str(plan_id),
            error=str(exc),
        )


def _send_compose_review_runtime(request: dict[str, Any]) -> None:
    """Blocking broker enqueue of ``workers.compose_review_runtime`` (runs in a
    thread). Uses the worker's own Celery ``app`` to PRODUCE the task by name onto
    the ``review`` lane — the same task/queue the orchestrator autostart uses."""
    from api_server.review_autostart import COMPOSE_REVIEW_RUNTIME_TASK, REVIEW_QUEUE

    app.send_task(
        COMPOSE_REVIEW_RUNTIME_TASK,
        kwargs={"request": request},
        queue=REVIEW_QUEUE,
    )


@app.task(name="workers.reconcile_pipeline_state")  # type: ignore[untyped-decorator]
def reconcile_pipeline_state() -> dict[str, Any]:
    """Convergence safety net (audit C3 / P0.6): reconcile DERIVED pipeline state
    the live event path can miss.

    Four idempotent best-effort passes (a/b/c/d — see the module comment). A pass
    failure is isolated and logged; it never tumbles the beat. Every 90s."""
    return asyncio.run(_reconcile_pipeline_state_async(get_settings()))


async def _reconcile_pipeline_state_async(
    settings: Settings,
    *,
    redis: Any | None = None,
    now: datetime | None = None,
    stuck_task_min_age: timedelta = _RECONCILE_STUCK_TASK_MIN_AGE,
    review_min_age: timedelta = _RECONCILE_REVIEW_MIN_AGE,
) -> dict[str, int]:
    """Async core — owns the engine + redis lifecycle. ``redis`` / ``now`` /
    thresholds are injectable so the integration test drives it deterministically.

    Each pass is wrapped so an exception in one (a bad row, a broker blip) is
    logged and the others still run — best-effort, never crash beat."""
    from redis.asyncio import Redis

    moment = now or datetime.now(UTC)
    engine = create_async_engine(settings.database_url)
    own_redis = redis is None
    redis_client = redis if redis is not None else Redis.from_url(settings.events_redis_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    result: dict[str, int] = {
        "stuck_tasks": 0,
        "orphan_reviews": 0,
        "completed_plans": 0,
        "pushed_worktrees": 0,
    }
    try:
        try:
            result["stuck_tasks"] = await _reconcile_stuck_tasks(
                sessionmaker, redis_client, now=moment, min_age=stuck_task_min_age
            )
        except Exception as exc:
            _log.warning("maintenance.reconcile_pipeline_state.stuck_tasks_error", error=str(exc))
        try:
            result["orphan_reviews"] = await _reconcile_orphan_reviews(
                sessionmaker, redis_client, now=moment, min_age=review_min_age
            )
        except Exception as exc:
            _log.warning(
                "maintenance.reconcile_pipeline_state.orphan_reviews_error", error=str(exc)
            )
        try:
            result["completed_plans"] = await _reconcile_complete_plans(sessionmaker)
        except Exception as exc:
            _log.warning(
                "maintenance.reconcile_pipeline_state.completed_plans_error", error=str(exc)
            )
        try:
            result["pushed_worktrees"] = await _reconcile_unpushed_worktrees(
                settings, sessionmaker, redis_client, now=moment, min_age=stuck_task_min_age
            )
        except Exception as exc:
            _log.warning(
                "maintenance.reconcile_pipeline_state.unpushed_worktrees_error", error=str(exc)
            )
    finally:
        await engine.dispose()
        if own_redis:
            with contextlib.suppress(Exception):
                await redis_client.aclose()

    _log.info("maintenance.reconcile_pipeline_state.done", **result)
    return result
