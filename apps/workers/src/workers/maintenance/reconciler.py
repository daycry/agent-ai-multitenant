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
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workers.celery_app import app
from workers.config import Settings, get_settings
from workers.db import worker_engine
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

# V-1 (auditoría de comportamiento 2026-07-25): antigüedad mínima de una
# reclamación de dispatch SIN ejecución antes de devolver la tarea a `ready`.
# Mucho más holgado que `_RECONCILE_STUCK_TASK_MIN_AGE` a propósito: ahí el run ya
# terminó (la fila está y es terminal), aquí puede que el worker todavía no haya
# sacado el mensaje de la cola. 30 min no corre ningún riesgo de pisar una entrega
# en vuelo y sobra para el caso real observado (7 días).
_RECONCILE_ORPHAN_CLAIM_MIN_AGE = timedelta(minutes=30)
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


def is_orphan_claim_candidate(*, is_human_route: bool) -> bool:
    """Whether "no execution row" may be read as "the run never started".

    It may NOT on the human route, and that omission was a real regression
    (adversarial audit 2026-07-25). ``_revert_orphan_claim`` guards on three
    things — still ``in_progress``, old ``started_at``, zero ``executions`` — and
    an ACCEPTED HUMAN TASK satisfies all three BY DESIGN: its auditable trace is
    a ``HumanWorkSession``, never an ``Execution``. The SQL candidate filter does
    not discriminate either.

    So 30 minutes after a person accepted their task, it was reverted to ``ready``
    with ``assigned_agent_id`` and ``started_at`` wiped. Their later submission
    then 409'd (``ready -> in_review`` is illegal) with no way to re-accept, and
    the ``ready`` event dispatched an AI run over the work the person was doing.

    The ``if latest is None: continue`` that task_wf_m1 removed was that class's
    only protection. The inference "no execution ⇒ no run ever started" is sound
    only on the AI route.
    """
    return not is_human_route


async def _is_human_route(db: Any, task: Any) -> bool:
    """Is this task being worked by a PERSON rather than an agent run?

    Two independent signals, either is enough — a task mid-handoff may briefly
    carry one and not the other, and a false "yes" only means the reconciler
    leaves the task alone (the safe direction), whereas a false "no" wipes a
    human's assignment.
    """
    from api_server.db.domain import Agent, HumanTaskAssignment
    from sqlalchemy import func, select

    assigned = getattr(task, "assigned_agent_id", None)
    if assigned is not None:
        agent_type = (
            await db.execute(select(Agent.agent_type).where(Agent.id == assigned))
        ).scalar_one_or_none()
        if str(agent_type or "") == "human":
            return True
    handed_to_person = (
        await db.execute(
            select(func.count())
            .select_from(HumanTaskAssignment)
            .where(HumanTaskAssignment.task_id == task.id)
        )
    ).scalar_one()
    return bool(handed_to_person)


def _orphan_claim_needs_revert(
    started_at: datetime | None,
    *,
    now: datetime,
    min_age: timedelta,
) -> bool:
    """True cuando una tarea `in_progress` SIN NINGUNA ejecución lleva reclamada
    más de ``min_age`` y hay que devolverla a `ready` (caso a2, hallazgo V-1).

    El dispatch reclama la tarea con un UPDATE atómico (`ready`→`in_progress`)
    ANTES de encolar el run; si algo revienta entre el claim y la creación de la
    fila de `executions` sin pasar por ``_revert_to_ready``, la tarea queda
    `in_progress` para siempre: :func:`_stuck_task_needs_reconcile` la descarta
    por diseño (su caso es "el último run es terminal", no "no hay run"), el
    sweeper de ejecuciones rancias no tiene fila que barrer y el reaper de
    huérfanos no tiene contenedor. Y como retiene el DAG, congela el plan entero.

    Decisión pura — sin BD — para poder testear el filtro en aislamiento.
    ``min_age`` es deliberadamente holgado: entre el claim y la creación de la
    ejecución hay una cola de Celery de por medio, y este barrido NUNCA debe
    pisar una entrega en vuelo. Sin ``started_at`` no se puede envejecer la
    reclamación, así que se deja estar."""
    if started_at is None:
        return False
    return started_at <= now - min_age


def _execution_belongs_to_claim(
    execution_created_at: datetime, *, started_at: datetime | None
) -> bool:
    """True si la ejecución se creó EN o DESPUÉS de la reclamación actual de la tarea.

    Auditoría 2026-09-01 (A-05). El caso (a) tomaba la ÚLTIMA ejecución de la
    tarea sin preguntarse de qué reclamación era. Una tarea re-reclamada
    (rechazo → backlog → ready → in_progress) conserva las filas de la vuelta
    anterior; si el run nuevo aún no ha creado la suya —cola de Celery con
    retraso, worker reiniciado— la «última» es la vieja, terminal y asentada, y
    el reconciler transicionaba la tarea con el veredicto de un run ajeno
    (`done` viejo → `in_review` sin trabajo nuevo; `failed` viejo → `blocked`).

    La regla es una sola y vive aquí para que (a) y (a2) la lean igual: el
    dispatch fija `started_at` con el `now()` de BD al reclamar y el worker crea
    la fila de `executions` después, con el mismo reloj, así que una fila
    anterior a `started_at` es, por construcción, de otra reclamación. La
    igualdad cuenta como «después». Sin `started_at` no hay con qué comparar y
    se conserva el comportamiento previo (toda ejecución cuenta)."""
    if started_at is None:
        return True
    return execution_created_at >= started_at


_RECONCILE_PLAN_PR_MIN_AGE = timedelta(minutes=10)
_RECONCILE_PLAN_PR_MAX_AGE = timedelta(days=7)


def _plan_needs_pr_retry(
    *,
    status: str,
    pr_url: str | None,
    pr_error: str | None,
    updated_at: datetime | None,
    now: datetime,
    min_age: timedelta,
    max_age: timedelta,
) -> bool:
    """True cuando un plan `completed` sigue sin resultado de auto-PR y hay que
    reencolar ``workers.open_plan_pr`` (caso e, `task_cv_14`).

    Auditoría 2026-09-01 (D-01): el auto-PR se encolaba UNA vez al validar el plan
    y nadie volvía a mirar. Si el broker no estaba, o el worker murió con la task
    en la mano, el plan quedaba `completed` sin `pr_url` ni `pr_error` para
    siempre, y como el cierre no se repite, nadie lo reintentaba.

    Decisión pura. Tres cosas que NO se reencolan: un plan con URL (el PR existe),
    un plan con `pr_error` (el fallo ya es visible en la ficha —P6— y lo reintenta
    el operador; reencolar a ciegas cada barrido convertiría un remoto caído en
    una tormenta) y un plan más viejo que ``max_age`` (un plan cerrado hace meses
    sin PR es un hecho histórico, no un encolado perdido)."""
    if status != "completed" or pr_url or pr_error or updated_at is None:
        return False
    return now - max_age <= updated_at <= now - min_age


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


async def _revert_orphan_claim(
    db: AsyncSession,
    task_id: Any,
    *,
    now: datetime,
    min_age: timedelta,
) -> tuple[Any, str, str] | None:
    """Devuelve a `ready` una tarea reclamada cuya ejecución nunca se creó (V-1).

    Espejo de ``orchestrator._revert_to_ready``: limpia la asignación y el
    `started_at` para que el siguiente dispatch la trate como nueva. Devuelve la
    terna ``(task, old, new)`` para que el caller publique el evento, o ``None``
    si la tarea ya no cumple (otro camino ganó la carrera).

    Tres guardas, todas dentro de la MISMA transacción con la fila bloqueada:
    la tarea sigue `in_progress`, la reclamación es lo bastante vieja
    (:func:`_orphan_claim_needs_revert` con el umbral holgado de V-1) y sigue sin
    haber ninguna ejecución. Entre el SELECT de candidatos y este punto un
    dispatch puede haber creado su fila; en ese caso no se toca nada.

    La transición va por ``transition_task_status`` (la puerta de la máquina de
    estados §7.2, donde `in_progress → ready` es legal), nunca por asignación
    cruda de ``.status`` — lo vigila ``test_state_mutation_guard``."""
    from api_server.db.domain import Execution, Task, TaskStatus
    from api_server.task_state_machine import transition_task_status
    from sqlalchemy import select

    task = (
        await db.execute(select(Task).where(Task.id == task_id).with_for_update())
    ).scalar_one_or_none()
    if task is None or task.status != TaskStatus.IN_PROGRESS.value:
        return None
    # La guarda que faltaba: una tarea de la RUTA HUMANA no es nunca una
    # reclamación huérfana. Ver `is_orphan_claim_candidate`.
    if not is_orphan_claim_candidate(is_human_route=await _is_human_route(db, task)):
        return None
    # El umbral de la reclamación huérfana es MÁS HOLGADO que el del caso (a): el
    # filtro SQL de candidatos usa el de (a), así que aquí se re-filtra.
    if not _orphan_claim_needs_revert(task.started_at, now=now, min_age=min_age):
        return None
    # Re-check bajo el lock: si mientras tanto apareció una ejecución DE ESTA
    # reclamación, el run está vivo y esta tarea NO es una reclamación huérfana.
    # Las filas de reclamaciones anteriores no cuentan (A-05): la misma regla
    # que aplica el caso (a), `_execution_belongs_to_claim`.
    latest_created_at = (
        await db.execute(
            select(Execution.created_at)
            .where(Execution.task_id == task_id)
            .order_by(Execution.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if latest_created_at is not None and _execution_belongs_to_claim(
        latest_created_at, started_at=task.started_at
    ):
        return None
    old = task.status
    transition_task_status(task, TaskStatus.READY.value)
    task.assigned_agent_id = None
    task.started_at = None
    task.claim_id = None  # `task_cv_13`: el mensaje de esta reclamación ya no es vigente
    task.updated_at = now
    await db.flush()
    return (task, old, TaskStatus.READY.value)


async def _reconcile_stuck_tasks(
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Any,
    *,
    now: datetime,
    min_age: timedelta,
) -> int:
    """Case (a): transition tasks stuck `in_progress` whose last run is terminal,
    AND (a2) revert tasks stuck `in_progress` with NO run at all.

    Reuses ``workers.execution.transition_task_after_run`` (the SAME dag_01 policy
    the worker applies: done→in_review/done, cancelled→cancelled, else→blocked) and
    re-emits the resulting ``task.status_changed`` so the board + the orchestrator
    converge. Per-task transaction + the `in_progress` guard inside
    ``transition_task_after_run`` make it idempotent and safe against a worker that
    wins the race.

    Caso (a2), hallazgo V-1: una tarea reclamada por el dispatch cuya ejecución
    nunca se creó no tiene run terminal que consultar, así que (a) la descartaba y
    quedaba `in_progress` PARA SIEMPRE, reteniendo el DAG y congelando su plan.
    Pasado ``_RECONCILE_ORPHAN_CLAIM_MIN_AGE`` se devuelve a `ready` — que es lo
    que ``orchestrator._revert_to_ready`` habría hecho si el dispatch hubiera
    fallado limpiamente — para que el ciclo la vuelva a repartir.

    Returns how many tasks were transitioned (ambos casos)."""
    from api_server.db.domain import Execution, Task, TaskStatus
    from api_server.events import publish_task_status_changed
    from sqlalchemy import select

    from workers.execution import transition_task_after_run

    cutoff = now - min_age
    async with sessionmaker() as db:
        candidates = list(
            (
                await db.execute(
                    select(Task.id, Task.started_at).where(
                        Task.status == TaskStatus.IN_PROGRESS.value,
                        Task.started_at < cutoff,
                    )
                )
            ).all()
        )
    reconciled = 0
    for task_id, started_at in candidates:
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
            if latest is None or not _execution_belongs_to_claim(
                latest.created_at, started_at=started_at
            ):
                # (a2) V-1: reclamación huérfana. Se relee la tarea DENTRO de la
                # transacción y se re-verifica `in_progress` + ausencia de run,
                # para no pisar un dispatch que haya ganado la carrera entre el
                # SELECT de candidatos y este punto. Una ejecución ANTERIOR a la
                # reclamación (A-05) es de otra vuelta de la tarea: no es el run
                # de este claim y no puede decidir su destino.
                event = await _revert_orphan_claim(
                    db, task_id, now=now, min_age=_RECONCILE_ORPHAN_CLAIM_MIN_AGE
                )
            elif not _stuck_task_needs_reconcile(
                latest.status, latest.completed_at, now=now, min_age=min_age
            ):
                continue
            else:
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


async def _reconcile_plans_without_pr(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    now: datetime,
    min_age: timedelta = _RECONCILE_PLAN_PR_MIN_AGE,
    max_age: timedelta = _RECONCILE_PLAN_PR_MAX_AGE,
    enqueue: Any | None = None,
) -> int:
    """Caso (e), `task_cv_14`: reencola el auto-PR de los planes `completed` que
    siguen sin `pr_url` ni `pr_error` (ver :func:`_plan_needs_pr_retry`).

    Pide el MISMO PR que el cierre por veredicto (``auto_pr_request``, fuente
    única) por la misma vía (``enqueue_open_plan_pr``). Un reencolado que falla
    (broker caído) no cuenta y se vuelve a intentar en el siguiente barrido; uno
    cuya task falle deja ahora `pr_error` (``_persist_task_failure``), así que
    no puede repetirse indefinidamente. Devuelve cuántos se reencolaron."""
    from api_server.celery_client import auto_pr_request, enqueue_open_plan_pr
    from api_server.db.domain import Plan, PlanStatus
    from sqlalchemy import select

    send = enqueue if enqueue is not None else enqueue_open_plan_pr
    async with sessionmaker() as db:
        rows = list(
            (
                await db.execute(
                    select(
                        Plan.id,
                        Plan.project_id,
                        Plan.title,
                        Plan.status,
                        Plan.pr_url,
                        Plan.pr_error,
                        Plan.updated_at,
                    ).where(
                        Plan.status == PlanStatus.COMPLETED.value,
                        Plan.pr_url.is_(None),
                        Plan.pr_error.is_(None),
                        Plan.deleted_at.is_(None),
                        Plan.updated_at <= now - min_age,
                        Plan.updated_at >= now - max_age,
                    )
                )
            ).all()
        )
    retried = 0
    for row in rows:
        if not _plan_needs_pr_retry(
            status=str(row.status),
            pr_url=row.pr_url,
            pr_error=row.pr_error,
            updated_at=row.updated_at,
            now=now,
            min_age=min_age,
            max_age=max_age,
        ):
            continue
        title, body = auto_pr_request(row.id, row.title)
        if await send(row.project_id, row.id, title=title, body=body):
            _log.warning(
                "maintenance.reconcile_pipeline_state.plan_pr_reenqueued", plan_id=str(row.id)
            )
            retried += 1
    return retried


async def _reconcile_complete_plans(
    sessionmaker: async_sessionmaker[AsyncSession], redis: Any | None = None
) -> int:
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
        decide_plan_closure,
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
            # `task_wf_58`: la MISMA función que usa el dispatch. Antes eran dos
            # copias de la secuencia con un comentario prometiendo que coincidían.
            result = decide_plan_closure(plan_status, snapshots)
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
                # task_wf_32: la red de seguridad también anuncia. Sin esto, un
                # plan que llega a `pending_human_validation` por el beat (y no
                # por el orchestrator) se movería sin que el tablero se entere —
                # justo el caso en el que el evento del orchestrator se perdió.
                await _announce_plan_move(
                    redis,
                    plan=plan,
                    old_status=PlanStatus.IN_PROGRESS.value,
                    new_status=result.new_status,
                )
                # El autostart del review-runtime solo aplica al camino
                # pending_human_validation, NO a blocked.
                won = result.new_status == "pending_human_validation"
        # GAP fix: build + enqueue the review-runtime autostart in a SEPARATE read
        # session AFTER the transition txn commits (broker I/O must never hold a DB
        # txn open; a build/enqueue failure must never touch the committed move).
        if won:
            await _autostart_review_runtime(sessionmaker, plan_id=prow.id, tenant_id=prow.tenant_id)
    return transitioned


async def _reconcile_unblocked_plans(
    sessionmaker: async_sessionmaker[AsyncSession], redis: Any | None = None
) -> int:
    """Red de seguridad del hallazgo #2: revierte planes ``blocked`` cuyo snapshot
    de tareas YA no justifica el bloqueo, sin exigir un segundo click humano.

    Espejo exacto de :func:`_reconcile_complete_plans` (mismos snapshots con
    dependencias, mismo guard atómico ``WHERE status='blocked'``) pero con la
    transición INVERSA ``transition_from_blocked``. Cubre las vías que desatascan
    una tarea/plan sin re-evaluar el plan (un evento perdido, o un desbloqueo por
    un camino que no llamó ``reactivate_plan_if_unstuck``). Sin ping-pong:
    ``transition_from_blocked`` es la negación EXACTA de ``transition_to_blocked``
    sobre el mismo snapshot, así que un plan genuinamente atascado se queda
    ``blocked``; y un snapshot TODO-terminal se salta (C-1: es el bloqueo C8 F40
    de review expirada, no un bloqueo por snapshot — revertirlo re-armaría el
    autostart de review en bucle). Al ganar, el beat de promoción DAG (que filtra
    ``in_progress``) re-anuncia las tareas ``ready`` en su siguiente pasada.
    Devuelve cuántos planes revirtió."""
    from api_server.db.domain import Plan, PlanStatus, Task, TaskDependency
    from api_server.plan_progress import PlanStatus as PlanStatusLiteral
    from api_server.plan_progress import (
        TaskSnapshot,
        has_open_tasks,
        transition_from_blocked,
    )
    from sqlalchemy import select, update

    async with sessionmaker() as db:
        plan_rows = list(
            (
                await db.execute(
                    select(Plan.id, Plan.tenant_id).where(Plan.status == PlanStatus.BLOCKED.value)
                )
            ).all()
        )
    reverted = 0
    for prow in plan_rows:
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
            # C-1 (auditoría 2026-07-10): un snapshot TODO-terminal no puede venir
            # del escalado por snapshot (exige ≥1 tarea blocked) — es la firma del
            # bloqueo C8 F40 (review expirada). Revertirlo aquí re-promocionaría el
            # plan y re-armaría el autostart de review en bucle de 48 h; ese
            # bloqueo lo levanta el humano. El caso legítimo «borré la última tarea
            # blocked y solo quedan done» ya lo revierte síncronamente el router.
            if not has_open_tasks(snapshots):
                continue
            result = transition_from_blocked(cast(PlanStatusLiteral, plan.status), snapshots)
            if not result.transitioned:
                continue
            won_id = (
                await db.execute(
                    update(Plan)
                    .where(
                        Plan.id == prow.id,
                        Plan.tenant_id == prow.tenant_id,
                        Plan.status == PlanStatus.BLOCKED.value,
                    )
                    .values(status=result.new_status)
                    .returning(Plan.id)
                )
            ).scalar_one_or_none()
            if won_id is not None:
                _log.info(
                    "maintenance.reconcile_pipeline_state.plan_unblocked",
                    plan_id=str(prow.id),
                    new_status=result.new_status,
                )
                reverted += 1
                await _announce_plan_move(
                    redis,
                    plan=plan,
                    old_status=PlanStatus.BLOCKED.value,
                    new_status=result.new_status,
                )
                # M-1 (auditoría 2026-07-10): notificar la reversión — en esta vía
                # (la red async) nadie se entera de otro modo: no hubo gesto humano.
                # Best-effort tras el commit del guard atómico; espejo del
                # `review_escalated` de review_runtimes.
                await _notify_plan_unblocked(
                    tenant_id=str(prow.tenant_id),
                    plan_id=str(prow.id),
                    plan_name=plan.title or "",
                )
    return reverted


async def _announce_plan_move(
    redis: Any | None, *, plan: Any, old_status: str, new_status: str
) -> None:
    """Anuncia al tablero gerencial una transición ganada por el beat
    (`task_wf_32`). Best-effort y con `redis` opcional: los tests del núcleo
    llaman a los reconciliadores sin bus, y quedarse sin anunciar no puede
    impedir la reconciliación — que es el trabajo de verdad."""
    if redis is None:
        return
    from api_server.events import publish_plan_status_changed

    await publish_plan_status_changed(
        redis,
        plan_id=str(plan.id),
        tenant_id=str(plan.tenant_id),
        project_id=str(plan.project_id),
        old_status=old_status,
        new_status=new_status,
        title=plan.title or "",
    )


async def _notify_plan_unblocked(*, tenant_id: str, plan_id: str, plan_name: str) -> None:
    """Encola el evento ``plan_unblocked`` al dispatcher (M-1). Best-effort: un
    fallo de import/broker se loguea y nunca tumba la pasada del reconciler."""
    try:
        from api_server.celery_client import enqueue_event_dispatch
    except ImportError:  # pragma: no cover - api_server siempre presente en workers
        return
    try:
        await enqueue_event_dispatch(
            {
                "event_type": "plan_unblocked",
                "tenant_id": tenant_id,
                "context": {"plan_name": plan_name, "plan_id": plan_id},
            }
        )
    except Exception as exc:  # - la notificación nunca rompe la red
        _log.warning("maintenance.plan_unblocked_notify_failed", plan_id=plan_id, error=str(exc))


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
    engine = worker_engine(settings)
    own_redis = redis is None
    redis_client = redis if redis is not None else Redis.from_url(settings.events_redis_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    result: dict[str, int] = {
        "stuck_tasks": 0,
        "orphan_reviews": 0,
        "completed_plans": 0,
        "unblocked_plans": 0,
        "pushed_worktrees": 0,
        "retried_plan_prs": 0,
        "tenant_ghost_children": 0,
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
        # unblocked ANTES que completed: un plan blocked cuyo snapshot ya no
        # justifica el bloqueo (p.ej. todas las tareas terminaron) revierte a
        # in_progress y, en la MISMA pasada, _reconcile_complete_plans lo lleva a
        # pending_human_validation — sin esperar al siguiente beat.
        try:
            result["unblocked_plans"] = await _reconcile_unblocked_plans(sessionmaker, redis)
        except Exception as exc:
            _log.warning(
                "maintenance.reconcile_pipeline_state.unblocked_plans_error", error=str(exc)
            )
        try:
            result["completed_plans"] = await _reconcile_complete_plans(sessionmaker, redis)
        except Exception as exc:
            _log.warning(
                "maintenance.reconcile_pipeline_state.completed_plans_error", error=str(exc)
            )
        try:
            result["retried_plan_prs"] = await _reconcile_plans_without_pr(sessionmaker, now=moment)
        except Exception as exc:
            _log.warning("maintenance.reconcile_pipeline_state.plan_prs_error", error=str(exc))
        try:
            result["pushed_worktrees"] = await _reconcile_unpushed_worktrees(
                settings, sessionmaker, redis_client, now=moment, min_age=stuck_task_min_age
            )
        except Exception as exc:
            _log.warning(
                "maintenance.reconcile_pipeline_state.unpushed_worktrees_error", error=str(exc)
            )
        # G-04/P1-08: vigilancia (solo WARNING, nunca borra) de hijos de tenants
        # inexistentes — divergencias que solo un restore/borrado manual crea.
        try:
            from workers.maintenance.integrity import check_tenant_children

            async with sessionmaker() as session:
                ghosts = await check_tenant_children(session)
            result["tenant_ghost_children"] = sum(ghosts.values())
            if ghosts:
                _log.warning("maintenance.tenant_integrity.ghost_children", **ghosts)
        except Exception as exc:
            _log.warning(
                "maintenance.reconcile_pipeline_state.tenant_integrity_error", error=str(exc)
            )
    finally:
        await engine.dispose()
        if own_redis:
            with contextlib.suppress(Exception):
                await redis_client.aclose()

    _log.info("maintenance.reconcile_pipeline_state.done", **result)
    return result
