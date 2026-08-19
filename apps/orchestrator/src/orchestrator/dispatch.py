"""Task dispatch — the orchestrator's real event handler (task_02_31).

Fase A gave the orchestrator a consumer loop with a no-op handler.
This is the handler that makes it dispatch: when a task reaches
`ready`, `TaskDispatcher`:

  1. picks an agent with the project's assignment policy (task_02_03);
  2. moves the task to `in_progress` and records the assignee;
  3. enqueues `workers.run_execution` — the worker conducts the run
     (task_02_30) from there.

The DB sessionmaker and the Celery app are injected so the integration
tests can point them at the throwaway test stack; `build_dispatch_
handler` builds them from `Settings` for the running service.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

import structlog
from api_server.agent_persona import effective_prompt_hash, resolve_agent_persona
from api_server.agent_skills_enforcement import resolve_agent_skill_prompt_fragments
from api_server.agent_tools_enforcement import (
    combine_tool_allowlists,
    extend_allowlist_with_project_mcp,
    merge_tool_specs,
    resolve_agent_tool_names,
    resolve_project_mcp_tool_names,
    serialize_agent_tool_specs,
    serialize_project_mcp_tool_specs,
)
from api_server.budgets import budget_pause_block, resolve_execution_budgets
from api_server.chat.sync_to_kanban import PLAN_TASK_SPEC_ID_KEY
from api_server.db.agent_prompt_version_repo import latest_prompt_version_number
from api_server.db.domain import (
    Agent,
    AgentType,
    Execution,
    HumanAgentConfig,
    HumanTaskAssignment,
    HumanTaskAssignmentStatus,
    Plan,
    Project,
    Task,
    TaskDependency,
    TaskStatus,
    Team,
    TeamMember,
)
from api_server.db.models import TaskAuditEvent
from api_server.db.plan_comment import PlanComment
from api_server.db.platform_settings import (
    config_needs_default_model,
    get_default_execution_budgets,
    get_default_model_config,
    get_execution_budget_ceiling_multiplier,
    resolve_model_config_chain,
)
from api_server.events import publish_plan_status_changed, publish_task_status_changed
from api_server.mcp_oauth_flow import serialise_servers_for_run
from api_server.plan_progress import (
    PlanStatus,
    TaskSnapshot,
    decide_plan_closure,
)
from api_server.review_autostart import (
    COMPOSE_REVIEW_RUNTIME_TASK as _COMPOSE_REVIEW_RUNTIME_TASK,
)
from api_server.review_autostart import (
    REVIEW_QUEUE as _REVIEW_QUEUE,
)
from api_server.review_autostart import (
    build_review_autostart_request,
)
from api_server.task_state_machine import transition_task_status
from celery import Celery
from redis.asyncio import Redis
from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from orchestrator.assignment import (
    AssignmentPolicy,
    Candidate,
    RoundRobin,
    TaskRequirement,
    assign_load_balanced,
    assign_manual,
    assign_skill_match,
)
from orchestrator.config import Settings
from orchestrator.consumer import EventHandler, TransientHandlerError
from orchestrator.events import EVENT_TASK_CREATED, EVENT_TASK_STATUS_CHANGED, TaskEvent

_log = structlog.get_logger("orchestrator.dispatch")


def _is_transient_db_error(exc: BaseException) -> bool:
    """True for a DB error that is a TRANSIENT connectivity blip (a dropped /
    reset connection), not a deterministic programming/integrity fault.

    A transient error on a plan-close or review trigger must be RETRIED, not
    dead-lettered (C3 F05): the handler re-raises it as
    :class:`TransientHandlerError` so the consumer leaves the event pending for
    reclaim. A non-transient DB error (bad SQL, constraint violation) would only
    fail again on retry, so it falls through to the normal dead-letter path."""
    if isinstance(exc, OperationalError | InterfaceError):
        return True
    return isinstance(exc, DBAPIError) and bool(exc.connection_invalidated)


# A task is dispatchable the moment it reaches `ready`.
_READY = "ready"
_IN_PROGRESS = "in_progress"
_BLOCKED = "blocked"
# Terminal status that may complete the owning plan.
_DONE = "done"
_IN_REVIEW = TaskStatus.IN_REVIEW.value
_ASSIGNED_TO_HUMAN = TaskStatus.ASSIGNED_TO_HUMAN.value
# Agent scopes eligible to take a project's task (spec §5.7.5).
_GLOBAL_SCOPES = ("global_builtin", "global_tenant_template")
_RUN_EXECUTION_TASK = "workers.run_execution"
# Plan 10 fan-out task the orchestrator enqueues to notify the assigned user
# of a human task (task_16_05). The dispatcher owns recipient resolution /
# template render / retry/DLQ — the orchestrator only PRODUCES it by name
# (same clean app boundary the AI run-execution enqueue uses).
_DISPATCH_EVENT_TASK = "notification_dispatcher.dispatch_event"
# The notification event_type a freshly-routed human task fires (registered in
# notification_dispatcher.event_mapping.EVENT_REGISTRY + templates).
_HUMAN_TASK_ASSIGNED_EVENT = "human_task_assigned"

# --- review-runtime autostart (C8 F39 / ADR 0063, de-deferred D2) -----------
# The autostart constants + helpers + the async builder now live in
# `api_server.review_autostart` — the SINGLE source of truth shared by this live
# path AND the convergence reconciler (`workers.maintenance._reconcile_complete_
# plans`). `_COMPOSE_REVIEW_RUNTIME_TASK` / `_REVIEW_QUEUE` are imported above
# (aliased) for the enqueue; `build_review_autostart_request` for the payload.


def _is_ready_trigger(event: TaskEvent) -> bool:
    """True when the event means a task just became dispatchable."""
    if event.type == EVENT_TASK_STATUS_CHANGED:
        return event.payload.get("new_status") == _READY
    if event.type == EVENT_TASK_CREATED:
        return event.payload.get("status") == _READY
    return False


def _is_done_trigger(event: TaskEvent) -> bool:
    """True when a task just reached terminal ``done`` — it may complete its
    plan and so trigger the transition to ``pending_human_validation``."""
    return event.type == EVENT_TASK_STATUS_CHANGED and event.payload.get("new_status") == _DONE


def _is_in_review_trigger(event: TaskEvent) -> bool:
    """True when a task just entered ``in_review`` — if its reviewer is an AI
    agent, the orchestrator dispatches a review execution (prod-17 loop_01)."""
    return event.type == EVENT_TASK_STATUS_CHANGED and event.payload.get("new_status") == _IN_REVIEW


# Cap on test-run outcomes folded into the reviewer's `<test-report>` block — a
# single run emits one per runtime (usually 1-3); we keep the freshest few.
_MAX_TEST_REPORT_RUNTIMES = 6
# Cap on prior AI-reviewer rejections injected into a RE-DISPATCHED implementer's
# prompt (A2). A task the reviewer rejected loops in_review → backlog → ready and
# is re-routed here; we feed back the freshest few `review_comment` payloads so the
# implementer knows what to fix instead of repeating the mistake. Newest first; a
# couple is enough without bloating the spec.
_MAX_PRIOR_REVIEW_FEEDBACK = 3
_MAX_TASK_COMMENTS = 10
# Per-runtime log tail kept in the reviewer block (the full logs live in the
# audit event / `docker logs`); enough for the reviewer to see what failed.
_TEST_REPORT_LOG_TAIL = 1500


# P1-7: cuántos outputs previos del implementador ve el reviewer y la cola por
# output (el más reciente entra entero-ish; los anteriores, recortados).
_REVIEW_PRIOR_OUTPUTS = 3
_REVIEW_PRIOR_OUTPUT_TAIL = 4000


def _format_prior_outputs(outputs: list[str]) -> str:
    """Los outputs del implementador para el reviewer, etiquetados (P1-7).

    ``outputs`` llega más reciente primero. Uno solo → verbatim (byte-a-byte el
    comportamiento previo). Varios → el más reciente primero como «attempt N
    (latest)» y los anteriores etiquetados y recortados, para que el reviewer
    vea el histórico de intentos sin que el prompt crezca sin límite."""
    non_empty = [o for o in outputs if o.strip()]
    if not non_empty:
        return ""
    if len(non_empty) == 1:
        return non_empty[0]
    total = len(non_empty)
    blocks: list[str] = []
    for index, output in enumerate(non_empty):
        attempt_number = total - index
        label = f"[attempt {attempt_number}" + (" — latest]" if index == 0 else " — earlier]")
        tail = output if index == 0 else output[-_REVIEW_PRIOR_OUTPUT_TAIL:]
        blocks.append(f"{label}\n{tail}")
    return "\n\n".join(blocks)


def _format_test_report_block(outcomes: list[dict[str, Any]]) -> str:
    """Render persisted ``test_run_completed`` outcomes as the reviewer's
    ``<test-report>`` prompt block (prod-17 task_prod17_test_02).

    Reads the outcome dicts the test-runtime persists (``runtime``, ``exit_codes``,
    ``all_passed``, ``timed_out``, ``logs_tail``) — no dependency on the sandboxed
    runtime package. Returns ``""`` when there are no outcomes (the reviewer then
    reviews the diff alone — graceful degradation)."""
    if not outcomes:
        return ""
    lines = ["<test-report>"]
    for o in outcomes:
        runtime = str(o.get("runtime", "unknown"))
        passed = bool(o.get("all_passed", False))
        status = "PASSED" if passed else "FAILED"
        exit_codes = o.get("exit_codes")
        timed_out = bool(o.get("timed_out", False))
        header = f"- runtime {runtime}: {status} (exit_codes={exit_codes}"
        if timed_out:
            header += ", timed_out=true"
        header += ")"
        lines.append(header)
        logs_tail = str(o.get("logs_tail") or "")
        if not passed and logs_tail:
            lines.append("  logs (tail):")
            lines.append("  ```")
            lines.append(logs_tail[-_TEST_REPORT_LOG_TAIL:])
            lines.append("  ```")
    lines.append("</test-report>")
    return "\n".join(lines)


def _render_acceptance_criteria(task: Any) -> str:
    """Los acceptance_criteria REALES de la task como bloque de texto para el
    review run (F1.6a, auditoría 2026-07-02). Acepta criterios dict (usa su
    description/text/criterion) o string; fallback a la description de la task
    cuando no hay criteria (tasks antiguas / free tasks)."""
    lines: list[str] = []
    for criterion in list(getattr(task, "acceptance_criteria", None) or []):
        if isinstance(criterion, dict):
            text = str(
                criterion.get("description")
                or criterion.get("text")
                or criterion.get("criterion")
                or criterion.get("title")
                or ""
            ).strip()
        else:
            text = str(criterion).strip()
        if text:
            lines.append(f"- {text}")
    if lines:
        return "\n".join(lines)
    return str(task.description or "")


@dataclass(frozen=True)
class _AiDispatch:
    """A ready task routed to the AI runtime pool — the worker run payload."""

    request: dict[str, Any]


@dataclass(frozen=True)
class _HumanDispatch:
    """A ready task routed to a human: the assignment is already committed
    (task moved to ``assigned_to_human``, a ``HumanTaskAssignment`` row
    created), and ``event`` is the Plan 10 fan-out payload to enqueue so the
    assigned user is notified. NO runtime container is requested."""

    event: dict[str, Any]
    assignment_id: str
    assigned_to_user_id: str | None


class TaskDispatcher:
    """Assigns ready tasks to agents and enqueues the worker run."""

    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        celery_app: Celery,
        settings: Settings,
        redis: Redis | None = None,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._celery = celery_app
        self._settings = settings
        # Producer side of the task event bus (events:tasks). The dispatcher is
        # the ONLY place a task goes ready -> in_progress / assigned_to_human, so
        # it must emit that transition for the board's /ws/kanban to update live
        # (without it the Kanban only refreshes on a manual reload). Optional so
        # the unit/integration harness can construct a publish-less dispatcher.
        self._redis = redis
        self._round_robin = RoundRobin()

    async def _publish_status_changed(
        self, event: TaskEvent, new_status: str, *, old_status: str = _READY
    ) -> None:
        """Best-effort emit of a post-dispatch ``old_status -> new_status`` event.

        ``publish_task_status_changed`` swallows its own Redis errors, so a blip
        never breaks dispatch. We build a transient :class:`Task` from the event
        ids purely as the value carrier the publisher reads (id/tenant/project).
        ``old_status`` defaults to ``ready`` (the dispatch trigger state); a
        revert emits ``in_progress -> ready`` so the Kanban re-syncs (C3 F02)."""
        if self._redis is None:
            return
        task_ref = Task(
            id=UUID(event.task_id),
            tenant_id=UUID(event.tenant_id),
            project_id=UUID(event.project_id),
        )
        await publish_task_status_changed(
            self._redis, task_ref, old_status=old_status, new_status=new_status
        )

    async def handle(self, event: TaskEvent) -> None:
        """Event handler — dispatch a task that has just gone `ready`.

        Branches on the assignee Agent's ``agent_type`` (task_16_05): a task
        assigned to a Human Agent takes the human route (NO runtime container —
        a ``HumanTaskAssignment`` row + a notification to the assigned user); an
        AI-assigned (or pool-assigned) task keeps the existing runtime-pool path
        untouched.
        """
        if _is_done_trigger(event):
            await self._on_task_done(event)
            return
        if _is_in_review_trigger(event):
            await self._on_task_in_review(event)
            return
        if not _is_ready_trigger(event):
            return
        task_id = UUID(event.task_id)
        result = await self._dispatch(task_id, tenant_id=UUID(event.tenant_id))
        if result is None:
            return
        if isinstance(result, _HumanDispatch):
            await self._publish_status_changed(event, _ASSIGNED_TO_HUMAN)
            await self._notify_human_assignment(event, result)
            return
        # C3 F02: the `in_progress` event is emitted by `_enqueue_ai_run` ONLY
        # after the broker enqueue succeeds. Emitting it here (before the
        # enqueue) left the Kanban showing `in_progress` for a task the enqueue
        # then failed to deliver and reverted to `ready`.
        await self._enqueue_ai_run(event, task_id, result)

    @contextlib.asynccontextmanager
    async def _transient_db_guard(self, op: str) -> AsyncIterator[None]:
        """Re-raise a TRANSIENT DB failure inside ``op`` as a
        :class:`TransientHandlerError` so the consumer keeps the event pending
        for reclaim instead of dead-lettering it (C3 F05). A non-transient error
        (or any non-DB error) propagates unchanged → normal dead-letter path."""
        try:
            yield
        except TransientHandlerError:
            raise
        except Exception as exc:
            if _is_transient_db_error(exc):
                raise TransientHandlerError(f"{op}: transient DB error: {exc}") from exc
            raise

    async def _on_task_done(self, event: TaskEvent) -> None:
        """A task reached ``done``: if it was the plan's last open task, flip the
        plan ``in_progress`` → ``pending_human_validation``.

        This is the LIVE wiring of ``plan_progress.transition_to_pending_human_
        validation``, which until now ran only in the in-memory ``plan_runner``
        (demos) — so in production a plan whose tasks all completed never
        auto-moved to human validation (sesión 2026-06-18 gap). The orchestrator
        is the right home: it is the only live consumer of the task event stream,
        runs BYPASSRLS with an explicit tenant predicate, and already owns the
        Celery app for the follow-on review-runtime spawn.

        On a winning transition it emits ``orchestrator.plan_ready_for_review`` AND
        auto-starts the review-runtime (C8 F39 / ADR 0063, de-deferred D2): it
        resolves the plan's ``main_image`` + worktree identifiers and enqueues
        ``workers.compose_review_runtime`` so a ``review_sessions`` row is created
        and the owner is notified with signed reviewer URLs. Until this wiring the
        plan stalled in ``pending_human_validation`` forever (no session ⇒ the
        reviewer URLs 404). IDEMPOTENT: the autostart no-ops when an active session
        already exists for the plan, so it is safe even though the reconciler can
        re-drive the same transition. The enqueue is best-effort — the plan
        transition is already committed; a broker blip just leaves the autostart to
        a later trigger / the operator (it never re-raises into the handler).
        """
        tenant_id = UUID(event.tenant_id)
        task_id = UUID(event.task_id)
        # Collected INSIDE the txn, enqueued AFTER it commits (broker I/O must never
        # hold the DB transaction open). ``None`` ⇒ nothing to autostart.
        autostart_request: dict[str, Any] | None = None
        # c3/T7: set when the plan is escalated to `blocked`; the operator is notified
        # after commit (same broker-I/O-outside-txn rule).
        blocked_notify: dict[str, Any] | None = None
        # task_wf_32: la transición ganada, para anunciarla al tablero gerencial
        # DESPUÉS del commit — un consumidor rápido leería una fila no durable.
        # Se recoge aquí porque las dos transiciones de este handler se escriben
        # con UPDATE crudo (guarda atómica) y no pasan por `move_plan`. Se
        # guardan los VALORES, no la fila: tras el commit el objeto ORM está
        # expirado y leerlo dispararía un refresh sobre una sesión cerrada.
        plan_event: dict[str, str] | None = None
        # C3 F05: a transient DB error here must NOT dead-letter the `done` event
        # (the plan would never close) — re-raise it as TransientHandlerError so
        # the consumer keeps it pending for reclaim.
        async with (
            self._transient_db_guard("on_task_done"),
            self._sessionmaker() as session,
            session.begin(),
        ):
            task = (
                await session.execute(
                    select(Task).where(Task.id == task_id, Task.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if task is None or task.plan_id is None:
                return
            plan = (
                await session.execute(
                    select(Plan).where(Plan.id == task.plan_id, Plan.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if plan is None:
                return
            rows = (
                await session.execute(
                    select(Task.id, Task.status).where(
                        Task.plan_id == plan.id, Task.tenant_id == tenant_id
                    )
                )
            ).all()
            # prod-06 A1: cargar dependencias para distinguir un backlog que puede
            # avanzar de uno transitivamente atascado tras un blocked/cancelled.
            dep_rows = (
                await session.execute(
                    select(TaskDependency.task_id, TaskDependency.depends_on_task_id).where(
                        TaskDependency.task_id.in_([r.id for r in rows])
                    )
                )
            ).all()
            deps_by_task: dict[str, list[str]] = {}
            for dr in dep_rows:
                deps_by_task.setdefault(str(dr.task_id), []).append(str(dr.depends_on_task_id))
            snapshots = [
                TaskSnapshot(
                    id=str(r.id),
                    status=r.status,
                    depends_on=tuple(deps_by_task.get(str(r.id), ())),
                )
                for r in rows
            ]
            # La columna es `str`; el Literal PlanStatus refleja el StrEnum del
            # dominio 1:1 (mypy-total 2026-07-08) — cast, no conversión.
            plan_status = cast(PlanStatus, plan.status)
            # `task_wf_58`: la MISMA función que usa el reconciler como red de
            # seguridad. `blocked` sale del mismo resultado, no de una segunda
            # llamada — así las dos vías no pueden discrepar sobre el mismo
            # snapshot.
            result = decide_plan_closure(plan_status, snapshots)
            if not result.transitioned or result.new_status == _BLOCKED:
                # c3 (audit 2026-07-03): a plan whose only remaining open tasks
                # are `blocked` can never reach pending_human_validation (blocked
                # counts as open), so it would sit `in_progress` forever with no
                # automatic route out. Escalate it to `blocked` (same atomic,
                # idempotent status=in_progress guard) so the operator sees the
                # stall and can unblock/retry a task.
                blocked = result
                if blocked.transitioned:
                    won_blocked = (
                        await session.execute(
                            update(Plan)
                            .where(
                                Plan.id == plan.id,
                                Plan.tenant_id == tenant_id,
                                Plan.status == _IN_PROGRESS,
                            )
                            .values(status=blocked.new_status)
                            .returning(Plan.id)
                        )
                    ).scalar_one_or_none()
                    if won_blocked is not None:
                        plan_event = {
                            "plan_id": str(plan.id),
                            "project_id": str(plan.project_id),
                            "title": plan.title or "",
                            "old_status": _IN_PROGRESS,
                            "new_status": blocked.new_status,
                        }
                        _log.warning(
                            "orchestrator.plan_blocked",
                            plan_id=str(plan.id),
                            tenant_id=str(tenant_id),
                            reason="all remaining tasks are blocked",
                        )
                        # c3/T7: notify the operator so the stall is visible and they
                        # can unblock/retry a task. Enqueued AFTER the txn commits.
                        blocked_notify = {
                            "event_type": "plan_blocked",
                            "tenant_id": str(tenant_id),
                            "context": {
                                "plan_name": plan.title or "",
                                "plan_id": str(plan.id),
                            },
                        }
            else:
                # Atomic, idempotent guard: only the transaction that still observes
                # the plan `in_progress` wins. The event stream is at-least-once
                # (XREADGROUP), and several tasks can finish almost together — the
                # `WHERE status = in_progress` predicate makes the transition fire
                # exactly once, never a double review-runtime down the line.
                won = (
                    await session.execute(
                        update(Plan)
                        .where(
                            Plan.id == plan.id,
                            Plan.tenant_id == tenant_id,
                            Plan.status == _IN_PROGRESS,
                        )
                        .values(status=result.new_status)
                        .returning(Plan.id)
                    )
                ).scalar_one_or_none()
                if won is not None:
                    plan_event = {
                        "plan_id": str(plan.id),
                        "project_id": str(plan.project_id),
                        "title": plan.title or "",
                        "old_status": _IN_PROGRESS,
                        "new_status": result.new_status,
                    }
                    _log.info(
                        "orchestrator.plan_ready_for_review",
                        plan_id=str(plan.id),
                        tenant_id=str(tenant_id),
                    )
                    try:
                        autostart_request = await self._build_review_autostart_request(
                            session, plan=plan, tenant_id=tenant_id
                        )
                    except Exception as exc:  # autostart must never block plan closure
                        # Closing the plan is the committed outcome; resolving the
                        # review payload is a best-effort follow-on. A bug / odd row
                        # here must not roll back the transition (the reconciler or a
                        # later trigger can still spawn the runtime).
                        _log.error(
                            "orchestrator.review_autostart_build_failed",
                            plan_id=str(plan.id),
                            error=str(exc),
                        )
                        autostart_request = None
        # Enqueue OUTSIDE the txn (best-effort; never re-raises into the handler).
        if plan_event is not None and self._redis is not None:
            await publish_plan_status_changed(self._redis, tenant_id=str(tenant_id), **plan_event)
        if blocked_notify is not None:
            await self._send_plan_blocked_notification(blocked_notify)
        if autostart_request is not None:
            await self._enqueue_review_runtime(autostart_request)

    async def _build_review_autostart_request(
        self, session: AsyncSession, *, plan: Plan, tenant_id: UUID
    ) -> dict[str, Any] | None:
        """Thin wrapper over :func:`api_server.review_autostart.build_review_
        autostart_request` — the SINGLE source of truth shared with the reconciler.

        Kept as a method (same signature) so the orchestrator's behaviour is
        unchanged and the existing wiring/integration tests still drive it; the
        idempotent decision (``None`` on an active session / deleted project) lives
        in the shared module."""
        return await build_review_autostart_request(session, plan=plan, tenant_id=tenant_id)

    async def _enqueue_review_runtime(self, request: dict[str, Any]) -> None:
        """Best-effort enqueue of ``workers.compose_review_runtime`` (C8 F39).

        ``send_task`` does blocking broker I/O, so we run it off the loop (same
        approach as the AI run + human-assignment enqueues). A failure is logged,
        never raised: the plan transition is already committed and the autostart
        retries on a later trigger / via the operator."""
        try:
            await asyncio.to_thread(self._send_compose_review_runtime, request)
        except Exception as exc:
            _log.error(
                "orchestrator.review_autostart_enqueue_failed",
                plan_id=request.get("plan_id"),
                error=str(exc),
            )
            return
        _log.info(
            "orchestrator.review_runtime_autostarted",
            plan_id=request.get("plan_id"),
            main_image=request.get("main_image"),
        )

    def _send_compose_review_runtime(self, request: dict[str, Any]) -> None:
        """Blocking broker enqueue of the review-runtime task (runs in a thread)."""
        self._celery.send_task(
            _COMPOSE_REVIEW_RUNTIME_TASK,
            kwargs={"request": request},
            queue=_REVIEW_QUEUE,
        )

    async def _on_task_in_review(self, event: TaskEvent) -> None:
        """A task entered ``in_review``: if its reviewer is an AI agent, dispatch a
        review execution (prod-17 loop_01).

        The reviewer runs as a NORMAL agent execution (the engine is agnostic); the
        worker applies its verdict on completion (loop_03). Routing by agent_type: a
        human reviewer (``agent_type='human'``) is left to the peer-review path
        (unchanged); a missing / cross-tenant / absent reviewer is a no-op. Best-effort
        enqueue — a failure leaves the task ``in_review`` and a re-delivered event (or a
        future sweep) retries; we never strand a half-state."""
        tenant_id = UUID(event.tenant_id)
        task_id = UUID(event.task_id)
        # C3 F05: a transient DB error reading the review context must NOT
        # dead-letter the `in_review` event (the review would never dispatch) —
        # re-raise as TransientHandlerError so the consumer retries via reclaim.
        async with self._transient_db_guard("on_task_in_review"), self._sessionmaker() as session:
            task = (
                await session.execute(
                    select(Task).where(Task.id == task_id, Task.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            if task is None or task.status != _IN_REVIEW or task.reviewer_agent_id is None:
                return
            reviewer = (
                await session.execute(
                    select(Agent).where(
                        Agent.id == task.reviewer_agent_id, Agent.tenant_id == tenant_id
                    )
                )
            ).scalar_one_or_none()
            if reviewer is None or reviewer.agent_type == AgentType.HUMAN.value:
                # No AI reviewer → human peer-review path / nothing. Not our concern.
                return
            project = (
                await session.execute(
                    select(Project).where(
                        Project.id == task.project_id, Project.deleted_at.is_(None)
                    )
                )
            ).scalar_one_or_none()
            if project is None:
                _log.info("orchestrator.review_skip_deleted_project", task_id=str(task_id))
                return
            # C3 F09: idempotent review dispatch. The task stays `in_review` for
            # the whole review, so a re-delivered `in_review` event would launch a
            # SECOND review run. Guard on an already-running execution for the task
            # (the review the worker is conducting): a re-delivery is then a no-op.
            # Residual race: the window between this enqueue and the worker creating
            # the Execution row — narrowed, not eliminated; the run-level idempotency
            # / reconciler is the final net.
            review_in_flight = (
                await session.execute(
                    select(Execution.id)
                    .where(
                        Execution.task_id == task.id,
                        Execution.tenant_id == tenant_id,
                        Execution.status == "running",
                    )
                    .limit(1)
                )
            ).scalar_one_or_none()
            if review_in_flight is not None:
                _log.info("orchestrator.review_already_in_flight", task_id=str(task_id))
                return
            reviewer_agent_id_str = str(reviewer.id)
            review_request = await self._build_review_request(
                session, task=task, reviewer=reviewer, project=project
            )

        # Enqueue OUTSIDE the read txn — blocking broker I/O off the loop.
        soft_limit, hard_limit = await self._execution_time_limits()
        try:
            await asyncio.to_thread(
                self._send_run_execution, review_request, soft_limit, hard_limit
            )
            _log.info(
                "orchestrator.review_dispatched",
                task_id=str(task_id),
                reviewer_agent_id=reviewer_agent_id_str,
            )
            # NOTIF-3 (auditoría 2026-07-12): review_requested estaba registrado
            # (+plantillas) pero NADIE lo emitía. Opt-in (sin default de canal:
            # cada review de IA notificando a telegram sería ruido); best-effort.
            try:
                await asyncio.to_thread(
                    self._send_dispatch_event,
                    {
                        "event_type": "review_requested",
                        "tenant_id": str(task.tenant_id),
                        "context": {
                            "task_title": task.title or "",
                            "task_id": str(task_id),
                        },
                    },
                )
            except Exception as exc:  # la notificación nunca rompe el dispatch
                _log.warning(
                    "orchestrator.review_requested_notify_failed",
                    task_id=str(task_id),
                    error=str(exc),
                )
        except Exception as exc:
            _log.error(
                "orchestrator.review_enqueue_failed",
                task_id=str(task_id),
                error=str(exc),
            )

    async def _assemble_run_request(
        self,
        session: AsyncSession,
        *,
        task: Task,
        agent: Agent,
        project: Project | None,
        model_spec: dict[str, Any],
    ) -> dict[str, Any]:
        """El payload COMÚN de un run del worker — implementador Y reviewer (P4).

        Antes cada rama re-derivaba esto por su cuenta (~90 líneas duplicadas) y
        ya divergieron una vez (H2: el reviewer re-derivaba la cadena de modelo
        inline). Aquí vive todo lo que ambas derivan IGUAL; el caller añade sus
        claves específicas (``review``/``review_context`` vs
        ``prior_review_feedback``/``task_comments``). El ``model_spec`` llega ya
        resuelto porque el implementador debe validarlo ANTES del claim atómico
        (C3 F07) — el builder no lo re-resuelve.

        Contratos de emisión (los lee ``ExecutionRequest.from_dict`` /
        ``_agent_spec``):
          * ``allowed_tools`` / ``tool_specs`` / ``skill_prompt_fragments``:
            ``None`` = clave AUSENTE (sin restricción / sin familias nuevas /
            prompt intacto — 06.15/06.18 backward-compat); una lista vacía SÍ se
            emite (p.ej. allowlist deny-all).
          * ``allowed_commands``: SIEMPRE emitida (la columna TEXT[] default
            ``[]``); lista vacía = shell_exec deny-all (Plan 06.16).
          * ``default_runtime_template`` / ``mcp_servers``: solo si el proyecto
            los pinea — "no key" = defaults por-tool / sin sesión MCP.
        """
        agent_tool_names = await resolve_agent_tool_names(session, agent.id)
        allowed_tools = combine_tool_allowlists(agent_tool_names, None)
        # ADR 0128: las tools MCP las aporta el PROYECTO (no se conceden por-agente).
        # El runtime ya conecta los `project.mcp_servers` y registra sus
        # `<server>.<tool>`; aquí extendemos (unión, aditivo) el allowlist de un
        # agente restringido con esas tools para que pueda llamarlas sin un grant
        # por-agente. Un agente sin restricción (allowed_tools None) se queda igual.
        project_mcp_tool_names = await resolve_project_mcp_tool_names(
            session, project, role=agent.role
        )
        allowed_tools = extend_allowlist_with_project_mcp(allowed_tools, project_mcp_tool_names)
        tool_specs = await serialize_agent_tool_specs(session, agent.id)
        # task_wf_10 (B-01): permitir no es anunciar. `build_model_tool_schemas`
        # saca los esquemas de `tool_specs`, que es POR AGENTE, así que una tool
        # MCP de proyecto quedaba permitida pero invisible para el modelo — jamás
        # la llamaba. Aportamos también sus especificadores, derivados del MISMO
        # conjunto ya filtrado por la política de roles.
        tool_specs = merge_tool_specs(
            tool_specs, await serialize_project_mcp_tool_specs(session, project, role=agent.role)
        )
        skill_prompt_fragments = await resolve_agent_skill_prompt_fragments(session, agent.id)

        # Per-run budget envelope (prod-06 budget_02): plataforma ← proyecto,
        # clampado al techo del runtime. `None` = defaults compilados del runtime.
        platform_budgets = await get_default_execution_budgets(session)
        # ADR 0113: el System Admin puede ampliar el techo (x1..x4); el override
        # de proyecto puede entonces pedir mas margen sin tocar el default global.
        ceiling_multiplier = await get_execution_budget_ceiling_multiplier(session)
        budgets = resolve_execution_budgets(
            platform_default=platform_budgets,
            project_override=getattr(project, "execution_budgets", None) if project else None,
            ceiling_multiplier=ceiling_multiplier,
        )

        request: dict[str, Any] = {
            "tenant_id": str(task.tenant_id),
            "task_id": str(task.id),
            "agent_id": str(agent.id),
            "task": {
                "id": str(task.id),
                "title": task.title,
                "description": task.description or "",
            },
            "model": model_spec,
            "budgets": budgets,
        }
        if allowed_tools is not None:
            request["allowed_tools"] = allowed_tools
        if tool_specs is not None:
            request["tool_specs"] = tool_specs
        if skill_prompt_fragments is not None:
            request["skill_prompt_fragments"] = skill_prompt_fragments
        # P0-1 (investigación 2026-07-11): la persona del agente (system_prompt /
        # model_config.system_prompts) viaja al run — el runtime la prepende como
        # PRIMER bloque del system preamble. Sin persona → clave ausente.
        agent_persona = resolve_agent_persona(agent)
        if agent_persona is not None:
            request["agent_persona"] = agent_persona
            # `task_gov_03`: el sello del prompt del agente, para que
            # `executions.prompt_version` deje de hablar sólo del andamiaje del
            # runtime. Viaja junto a la persona y NUNCA sin ella: sin persona no
            # hay texto que sellar, y emitir el hash del vacío movería la etiqueta
            # de todos esos runs sin distinguir nada.
            request["agent_prompt_version"] = {
                # De la fila VIVA, no del historial: es el prompt que se acaba de
                # mandar. Si el agente lleva un prompt que nadie registró todavía
                # (nunca se editó desde `task_gov_02`), el hash sigue siendo
                # correcto y sólo falta el número.
                "prompt_hash": effective_prompt_hash(agent),
                "version": await latest_prompt_version_number(session, agent.id),
            }
        project_commands = getattr(project, "allowed_commands", None) if project else None
        request["allowed_commands"] = [str(c) for c in (project_commands or [])]
        # prod-12 Fase B (gap4-2): la allowlist de dominios de las tools HTTP,
        # SIEMPRE emitida (columna TEXT[] default []); lista vacia = deny-all
        # explicito. El runtime re-valida cada resolucion con el ssrf_guard.
        project_domains = getattr(project, "allowed_domains", None) if project else None
        request["allowed_domains"] = [str(d) for d in (project_domains or [])]
        project_runtime = getattr(project, "default_runtime_template", None) if project else None
        if project_runtime:
            request["default_runtime_template"] = str(project_runtime)
        project_mcp_servers = getattr(project, "mcp_servers", None) if project else None
        if project_mcp_servers and project is not None:
            # task_wf_12 (B-03): añade `oauth_ref` a los servidores OAuth. El
            # runtime no puede deducirlo — el config persistido no lleva
            # `auth_kind` (eso vive en el catálogo, por URL) ni el runtime sabe
            # su tenant/proyecto. Aquí sí se sabe.
            request["mcp_servers"] = serialise_servers_for_run(
                project_mcp_servers,
                tenant_id=str(task.tenant_id),
                project_id=str(project.id),
            )
        return request

    async def _build_review_request(
        self,
        session: AsyncSession,
        *,
        task: Task,
        reviewer: Agent,
        project: Project,
    ) -> dict[str, Any]:
        """Assemble the worker payload for a REVIEW execution of ``task`` by the AI
        ``reviewer`` (prod-17 loop_02).

        Mirrors `_route_ai`'s agent-payload assembly (model inheritance chain, per-agent
        tools/skills, per-run budget envelope) but: (a) marks the run ``review=True`` so
        the worker applies the verdict instead of the normal post-run transition; (b)
        carries the review context (acceptance criteria + the prior implementer
        execution's output) instead of mutating the task status. Kept separate from
        `_route_ai` to leave the central dispatch path untouched. The ``<test-report>``
        injection is layered in Fase C (task_prod17_test_02)."""
        # Hallazgo H2 (refactor 2026-07-07): la MISMA cadena de herencia ADR 0055
        # que el implementador — antes duplicada inline aquí, con riesgo de que un
        # cambio futuro en la cadena solo se aplicara a una de las dos ramas.
        model_spec = await self._resolve_model_spec(session, reviewer, project)

        # The implementer's recent outputs for this task — what the reviewer judges.
        # P1-7 (investigación 2026-07-11): antes solo el ULTIMO (LIMIT 1) — en un
        # ciclo con reintentos el reviewer perdía el histórico (qué se intentó ya
        # y volvió a fallar). Ahora los últimos 3, más reciente primero y
        # etiquetados; cada uno con cola acotada para no inflar el prompt.
        prior_rows = list(
            (
                await session.execute(
                    select(Execution.output)
                    .where(Execution.task_id == task.id, Execution.tenant_id == task.tenant_id)
                    .order_by(Execution.created_at.desc())
                    .limit(_REVIEW_PRIOR_OUTPUTS)
                )
            ).scalars()
        )
        prior_output = _format_prior_outputs([str(o or "") for o in prior_rows])

        # prod-17 task_prod17_test_02: fold the latest test-runtime outcomes into a
        # `<test-report>` block the reviewer reads (ADR 0027 loop). The test-runtime
        # (task_prod17_test_01) persists `test_run_completed` audit events; we read
        # the freshest few. Absent (no tests run yet) → empty → the reviewer reviews
        # the diff alone (graceful degradation).
        test_outcomes = list(
            (
                await session.execute(
                    select(TaskAuditEvent.payload)
                    .where(
                        TaskAuditEvent.task_id == task.id,
                        TaskAuditEvent.tenant_id == task.tenant_id,
                        TaskAuditEvent.kind == "test_run_completed",
                    )
                    .order_by(TaskAuditEvent.at.desc())
                    .limit(_MAX_TEST_REPORT_RUNTIMES)
                )
            ).scalars()
        )
        test_report = _format_test_report_block(list(reversed(test_outcomes)))

        request = await self._assemble_run_request(
            session, task=task, agent=reviewer, project=project, model_spec=model_spec
        )
        # Marks this as a review run — the worker applies the verdict (loop_03)
        # instead of the normal done/failed task transition (dag_01).
        request["review"] = True
        request["review_context"] = {
            # F1.6a (auditoría 2026-07-02): el reviewer certifica contra los
            # acceptance_criteria REALES de la task — antes recibía la
            # description, mientras el implementador trabajaba contra los
            # criteria: dos definiciones de "done" distintas en el mismo
            # ciclo. La description queda solo como fallback sin criteria.
            "acceptance_criteria": _render_acceptance_criteria(task),
            "implementer_output": prior_output or "",
            # `<test-report>` block (prod-17 test_02); "" when no tests ran yet.
            "test_report": test_report,
        }
        return request

    async def _enqueue_ai_run(self, event: TaskEvent, task_id: UUID, result: _AiDispatch) -> None:
        """Enqueue the worker run for an AI-routed task (the existing path)."""
        request = result.request
        # send_task does blocking broker I/O — keep it off the loop.
        #
        # The task is already committed `in_progress` with an assignee at this
        # point. If ANYTHING here fails (the operator-tunable time-limit read
        # below, OR the broker enqueue itself — broker down, network blip) the
        # task would be stranded `in_progress` yet never picked up by a worker
        # (workers-orchestrator-8). C3 F01: the `_execution_time_limits()` read
        # is INSIDE the try so a DB blip on it reverts the task too, instead of
        # raising past here and dead-lettering the event with the task left
        # `in_progress` forever. Revert to `ready` in a fresh transaction so the
        # next dispatch trigger (or the reconciler) re-enqueues it. A
        # transactional outbox would be sturdier but is overkill here —
        # revert-on-failure is the pragmatic safe fix (Plan 06.14 task_06_14_05).
        try:
            # Operator-tunable backstop limits, read fresh per dispatch so a
            # platform-settings change takes effect without restarting the
            # workers (Plan 06.14 task_06_14_04 / workers-orchestrator-10).
            soft_limit, hard_limit = await self._execution_time_limits()
            await asyncio.to_thread(
                self._send_run_execution,
                request,
                soft_limit,
                hard_limit,
            )
        except Exception as exc:
            await self._revert_to_ready(event, task_id)
            _log.error(
                "orchestrator.dispatch_enqueue_failed",
                task_id=event.task_id,
                agent_id=request["agent_id"],
                error=str(exc),
            )
            return
        # C3 F02: emit `in_progress` only now the enqueue has SUCCEEDED, so the
        # Kanban never shows `in_progress` for a run that failed to enqueue.
        await self._publish_status_changed(event, _IN_PROGRESS)
        _log.info(
            "orchestrator.task_dispatched",
            task_id=event.task_id,
            agent_id=request["agent_id"],
        )

    async def _notify_human_assignment(self, event: TaskEvent, result: _HumanDispatch) -> None:
        """Notify the assigned user that a human task landed on them (Plan 10).

        The assignment + task transition are ALREADY committed by the time we
        get here (unlike the AI path, the task is parked in
        ``assigned_to_human`` waiting on the human, not stranded mid-run). So a
        broker hiccup on the notification is best-effort: it is logged, not
        rolled back — the acceptance-timeout sweep (task_16_06) still escalates
        on the row, and the user can find the task in their inbox regardless.
        ``send_task`` does blocking broker I/O, so we run it off the loop."""
        try:
            await asyncio.to_thread(self._send_human_assigned_event, result.event)
        except Exception as exc:
            _log.warning(
                "orchestrator.human_assign_notify_failed",
                task_id=event.task_id,
                assignment_id=result.assignment_id,
                error=str(exc),
            )
            return
        _log.info(
            "orchestrator.human_task_assigned",
            task_id=event.task_id,
            assignment_id=result.assignment_id,
            assigned_to_user_id=result.assigned_to_user_id,
        )

    async def _revert_to_ready(self, event: TaskEvent, task_id: UUID) -> None:
        """Undo a dispatch whose enqueue failed: move the task back to `ready`,
        clear the assignment, and re-emit the status event so the board re-syncs.

        Best-effort and idempotent — only a task still `in_progress` is
        reverted (a worker may have raced ahead, though the broker-down case
        that triggers this makes that unlikely). A revert that itself fails is
        logged, never masking the original enqueue error. C3 F02: on a real
        revert we publish the `in_progress -> ready` change so the Kanban does
        not keep showing `in_progress` for a task that is once again `ready`
        (the reconciler owns the automatic re-dispatch)."""
        reverted = False
        try:
            async with self._sessionmaker() as session, session.begin():
                task = (
                    await session.execute(
                        select(Task).where(
                            Task.id == task_id, Task.tenant_id == UUID(event.tenant_id)
                        )
                    )
                ).scalar_one_or_none()
                if task is None or task.status != _IN_PROGRESS:
                    return
                task.status = _READY
                task.assigned_agent_id = None
                task.started_at = None
                reverted = True
        except Exception as revert_exc:  # pragma: no cover - defensive
            _log.error(
                "orchestrator.dispatch_revert_failed",
                task_id=str(task_id),
                error=str(revert_exc),
            )
            return
        if reverted:
            await self._publish_status_changed(event, _READY, old_status=_IN_PROGRESS)

    async def _execution_time_limits(self) -> tuple[int, int]:
        """Read the operator-tunable (soft, hard) run_execution time limits
        from platform settings — fresh per dispatch so a UI change applies
        to new runs immediately."""
        from api_server.db.platform_settings import get_execution_time_limits

        async with self._sessionmaker() as session:
            return await get_execution_time_limits(session)

    def _send_run_execution(
        self, request: dict[str, Any], soft_limit: int, hard_limit: int
    ) -> None:
        """Blocking broker enqueue (runs in a thread). Per-task time limits
        are passed as Celery execution options."""
        self._celery.send_task(
            _RUN_EXECUTION_TASK,
            kwargs={"request": request},
            queue=self._settings.dispatch_queue,
            soft_time_limit=soft_limit,
            time_limit=hard_limit,
        )

    def _send_human_assigned_event(self, event: dict[str, Any]) -> None:
        """Blocking broker enqueue of the Plan 10 fan-out (runs in a thread).

        Enqueues ``notification_dispatcher.dispatch_event`` onto the priority
        lane; the dispatcher resolves the tenant's channels, renders the
        ``human_task_assigned`` template, and sends. The orchestrator only
        PRODUCES it by name (clean app boundary)."""
        self._celery.send_task(
            _DISPATCH_EVENT_TASK,
            args=[event],
            queue=self._settings.notifications_event_queue,
        )

    def _send_dispatch_event(self, event: dict[str, Any]) -> None:
        """Blocking broker enqueue of a domain notification event (runs in a thread).

        Same clean app boundary as the human-assignment fan-out: the orchestrator only
        PRODUCES the event by name; the dispatcher owns recipients + template + retry."""
        self._celery.send_task(
            _DISPATCH_EVENT_TASK,
            args=[event],
            queue=self._settings.notifications_event_queue,
        )

    async def _send_plan_blocked_notification(self, event: dict[str, Any]) -> None:
        """Best-effort notify the operator that a plan was escalated to `blocked`
        (c3/T7). The plan status is already committed and visible in the UI, so a
        broker hiccup here is logged, never raised. ``send_task`` does blocking socket
        I/O, so we run it off the loop."""
        try:
            await asyncio.to_thread(self._send_dispatch_event, event)
        except Exception as exc:
            _log.warning(
                "orchestrator.plan_blocked_notify_failed",
                plan_id=(event.get("context") or {}).get("plan_id"),
                error=str(exc),
            )

    async def _dispatch(
        self, task_id: UUID, *, tenant_id: UUID
    ) -> _AiDispatch | _HumanDispatch | None:
        """Route a ready task: AI (pick agent → worker payload) or human
        (create the assignment, transition to ``assigned_to_human``). Returns
        None if the task is no longer ready, is budget-paused, or no AI agent
        is available. The orchestrator runs BYPASSRLS, so the initial task load
        carries an explicit ``tenant_id`` predicate (regla dura #1, audit c5)."""
        # PROJ-05: eventos de notificación producidos DENTRO de la txn (con su
        # testigo de dedupe) pero enviados al broker DESPUÉS del commit — el
        # broker I/O nunca sostiene la transacción abierta.
        notifications: list[dict[str, Any]] = []
        result: _AiDispatch | _HumanDispatch | None = None
        async with self._sessionmaker() as session, session.begin():
            task = (
                await session.execute(
                    select(Task).where(Task.id == task_id, Task.tenant_id == tenant_id)
                )
            ).scalar_one_or_none()
            # Re-check the live state: a stale `ready` event for a task
            # already dispatched (or cancelled) must be a no-op.
            if task is None or task.status != _READY:
                return None

            # P1-01: un proyecto pausado/archivado no despacha (ni ruta AI ni
            # humana) — la tarea queda `ready` y se re-despacha cuando el
            # proyecto vuelva a `active`. Cubre también el soft-delete.
            project_status = (
                await session.execute(
                    select(Project.status).where(
                        Project.id == task.project_id,
                        Project.deleted_at.is_(None),
                    )
                )
            ).scalar_one_or_none()
            if project_status != "active":
                _log.info(
                    "orchestrator.skip_inactive_project",
                    task_id=str(task_id),
                    project_id=str(task.project_id),
                    project_status=project_status,
                )
                return None

            # Budget auto-pause (Plan 11.1 task_11_1_06): if the task's tenant
            # or project has hit 100% of its budget for the active period, the
            # START of a NEW execution is refused — the task stays `ready` (it
            # is re-dispatched once the pause is overridden or a new period
            # clears it). Active executions are NEVER touched. The orchestrator
            # runs BYPASSRLS, so the guard carries an explicit tenant predicate.
            #
            # Applies to the AI route only: a human task starts no execution and
            # accrues no AI cost, so it is not gated by the budget pause.
            human_agent = await self._human_assignee(session, task)
            if human_agent is None:
                block = await budget_pause_block(
                    session, tenant_id=task.tenant_id, project_id=task.project_id
                )
                if block is not None:
                    _log.info(
                        "orchestrator.task_paused_by_budget",
                        task_id=str(task_id),
                        **block.as_log_fields(),
                    )
                    return None
                result = await self._route_ai(session, task, unassignable_out=notifications)
            else:
                result = await self._route_human(session, task, human_agent)

        # Broker I/O fuera de la txn; best-effort (la tarea sigue `ready` y el
        # audit event ya está commiteado — un fallo aquí solo pierde el aviso).
        for event in notifications:
            try:
                await asyncio.to_thread(self._send_dispatch_event, event)
            except Exception as exc:
                _log.warning(
                    "orchestrator.task_unassignable_notify_failed",
                    task_id=str(task_id),
                    error=str(exc),
                )
        return result

    async def _human_assignee(self, session: AsyncSession, task: Task) -> Agent | None:
        """Return the task's assignee Agent iff it is a Human Agent, else None.

        This is the branch point of task_16_05: a task whose
        ``assigned_agent_id`` resolves to an ``agent_type='human'`` Agent takes
        the human route (NO container). An unassigned task, or one assigned to
        an AI agent, returns None and falls through to the AI route. The
        BYPASSRLS orchestrator carries an explicit ``tenant_id`` predicate so a
        cross-tenant ``assigned_agent_id`` can never be resolved."""
        if task.assigned_agent_id is None:
            return None
        agent = (
            await session.execute(
                select(Agent).where(
                    Agent.id == task.assigned_agent_id,
                    Agent.tenant_id == task.tenant_id,
                )
            )
        ).scalar_one_or_none()
        if agent is None or agent.agent_type != AgentType.HUMAN.value:
            return None
        return agent

    async def _mark_task_unassignable(self, session: AsyncSession, task: Task) -> bool:
        """PROJ-05: deja el testigo ``task_unassignable`` en task_audit_events la
        PRIMERA vez y devuelve True; False si ya estaba marcado. El testigo es el
        dedupe de la notificación: el beat re-anuncia la tarea cada 30s y sin
        esto el operador recibiría una inundación."""
        from api_server.db.models import TaskAuditEvent
        from api_server.db.task_audit_repo import append_audit_event

        already = (
            await session.execute(
                select(TaskAuditEvent.id)
                .where(
                    TaskAuditEvent.task_id == task.id,
                    TaskAuditEvent.kind == "task_unassignable",
                )
                .limit(1)
            )
        ).scalar_one_or_none()
        if already is not None:
            return False
        await append_audit_event(
            session,
            tenant_id=task.tenant_id,
            task_id=task.id,
            kind="task_unassignable",
            actor="orchestrator",
            payload={"reason": "no_agent_for_task"},
        )
        return True

    def _task_unassignable_event(self, task: Task) -> dict[str, Any]:
        return {
            "event_type": "task_unassignable",
            "tenant_id": str(task.tenant_id),
            "context": {
                "task_title": task.title or "",
                "task_id": str(task.id),
                "project_id": str(task.project_id),
            },
        }

    async def _surface_unassignable(
        self,
        session: AsyncSession,
        task: Task,
        unassignable_out: list[dict[str, Any]] | None,
    ) -> None:
        """Marca + encola (vía out-param) el aviso de tarea sin candidatos."""
        if unassignable_out is None:
            return
        if await self._mark_task_unassignable(session, task):
            unassignable_out.append(self._task_unassignable_event(task))

    async def _clear_dead_preset(self, session: AsyncSession, task: Task) -> None:
        """PROJ-05 (auto-reparación): el preset ``assigned_agent_id`` apunta a un
        agente soft-borrado/inexistente y GANA siempre en ``_pick`` — sin esto la
        tarea quedaba `ready` para siempre. Limpiar el preset (con testigo de
        audit) deja que el siguiente dispatch caiga a la política del proyecto."""
        from api_server.db.task_audit_repo import append_audit_event

        dead_agent_id = str(task.assigned_agent_id)
        task.assigned_agent_id = None
        await append_audit_event(
            session,
            tenant_id=task.tenant_id,
            task_id=task.id,
            kind="assignment_preset_cleared",
            actor="orchestrator",
            payload={"reason": "agent_missing_or_deleted", "agent_id": dead_agent_id},
        )
        _log.warning(
            "orchestrator.assignment_preset_cleared",
            task_id=str(task.id),
            agent_id=dead_agent_id,
        )

    async def _route_ai(
        self,
        session: AsyncSession,
        task: Task,
        *,
        unassignable_out: list[dict[str, Any]] | None = None,
    ) -> _AiDispatch | None:
        """The existing AI route: pick an agent, move to ``in_progress``,
        build the worker payload. Untouched behaviour for AI tasks."""
        # prod-06 task_prod06_budget_03 (db-5): never start an execution for a
        # task whose project was soft-deleted. The cancellation cascade
        # (task_prod06_cancel_02) cleans up in-flight work on delete, but a stale
        # `ready` event could still arrive afterwards — load the project with the
        # `deleted_at IS NULL` filter and skip if it is gone.
        project = (
            await session.execute(
                select(Project).where(
                    Project.id == task.project_id,
                    Project.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if project is None:
            _log.info(
                "orchestrator.skip_deleted_project",
                task_id=str(task.id),
                project_id=str(task.project_id),
            )
            return None
        candidates = await self._candidates(session, task, project)
        required_skills = await self._task_required_skills(session, task)
        agent_id = self._pick(project, task, candidates, required_skills=required_skills)
        if agent_id is None:
            _log.warning("orchestrator.no_agent_for_task", task_id=str(task.id))
            await self._surface_unassignable(session, task, unassignable_out)
            return None

        # C3 F08: reload the picked agent SCOPED to the task's tenant (and not
        # soft-deleted). The previous unscoped `select(Agent).where(id==...)
        # .scalar_one()` could resolve a cross-tenant row, or raise
        # `NoResultFound` (tumbling the whole handler) if the agent was deleted
        # between the pick and now. `scalar_one_or_none` + an explicit predicate
        # turns a missing / cross-tenant agent into a clean no-op instead.
        agent = (
            await session.execute(
                select(Agent).where(
                    Agent.id == UUID(agent_id),
                    Agent.tenant_id == task.tenant_id,
                    Agent.deleted_at.is_(None),
                )
            )
        ).scalar_one_or_none()
        if agent is None:
            _log.warning("orchestrator.no_agent_for_task", task_id=str(task.id))
            if task.assigned_agent_id is not None:
                await self._clear_dead_preset(session, task)
            else:
                await self._surface_unassignable(session, task, unassignable_out)
            return None

        # C3 F07: resolve the model spec BEFORE claiming the task. If the
        # inheritance chain still yields no provider+model (and no scripted
        # `kind`), do NOT move the task to `in_progress` / enqueue a run the
        # worker would only fail with `model_unresolved`. Leave it `ready` and
        # alert; a later trigger / the reconciler retries once a default exists.
        model_spec = await self._resolve_model_spec(session, agent, project)
        if config_needs_default_model(model_spec):
            _log.warning(
                "orchestrator.no_default_model",
                task_id=str(task.id),
                agent_id=str(agent.id),
            )
            return None

        # C3 F04: claim the task ATOMICALLY. The `ready -> in_progress` move was a
        # read-then-write (status checked in `_dispatch`, set here) with no row
        # lock, so two deliveries of the same `ready` event could both dispatch a
        # run. A single conditional `UPDATE ... WHERE status='ready' RETURNING id`
        # lets exactly ONE delivery win (the same guard `_on_task_done` uses for
        # the plan transition); the loser is a no-op.
        claimed = (
            await session.execute(
                update(Task)
                .where(
                    Task.id == task.id,
                    Task.tenant_id == task.tenant_id,
                    Task.status == _READY,
                )
                .values(
                    status=_IN_PROGRESS,
                    assigned_agent_id=agent.id,
                    started_at=datetime.now(UTC),
                )
                .returning(Task.id)
            )
        ).scalar_one_or_none()
        if claimed is None:
            _log.info("orchestrator.dispatch_lost_race", task_id=str(task.id))
            return None

        # Payload común implementador/reviewer (P4) — tools/skills/budgets/base
        # dict/threading del proyecto. `model_spec` se resolvió ANTES del claim
        # atómico (C3 F07) y viaja ya validado.
        request = await self._assemble_run_request(
            session, task=task, agent=agent, project=project, model_spec=model_spec
        )

        # Inter-run reviewer feedback (A2). If THIS task was rejected by the AI
        # reviewer on a prior pass (in_review → backlog → ready → here), thread the
        # freshest rejection payloads into the spec so the re-dispatched implementer
        # knows what to fix. No prior rejection → no key (backward-compat: a normal
        # first dispatch is byte-for-byte the previous behaviour).
        prior_feedback = await self._read_prior_review_feedback(session, task)
        if prior_feedback:
            request["prior_review_feedback"] = prior_feedback
        # P0-7 (investigación 2026-07-11): a run that died WITHOUT finishing
        # (failed/aborted: loop, budget, provider bug) left no trace in the next
        # attempt's prompt — only reviewer rejections travelled. Thread the latest
        # failure so the implementer avoids the same dead end.
        prior_failure = await self._read_prior_failure(session, task)
        if prior_failure:
            request["prior_failure"] = prior_failure
        # `task_wf_70`: qué entregaron las dependencias DIRECTAS ya completadas
        # → el runtime las pliega como el terreno sobre el que construir.
        predecessors = await self._read_predecessor_briefs(session, task)
        if predecessors:
            request["predecessors"] = predecessors
        # ADR 0114: respuestas humanas a ask_human de intentos previos → el
        # runtime las pliega como preámbulo autoritativo (human_answers).
        human_answers = await self._read_prior_human_answers(session, task)
        if human_answers:
            request["human_answers"] = human_answers
        # Feature C: human comments on this task/plan → the runtime folds them into a
        # contextual preamble so the agent takes them into account.
        comments = await self._read_relevant_comments(session, task)
        if comments:
            request["task_comments"] = comments
        return _AiDispatch(request=request)

    async def _read_relevant_comments(
        self, session: AsyncSession, task: Task
    ) -> list[dict[str, str]]:
        """Human comments to surface to the agent run (Feature C).

        Reuses ``PlanComment`` (no separate task store): the comments that apply to
        THIS task are the task-scoped ones (``target_kind='task'`` with ``target_ref``
        = the task's plan-spec id), the plan-level ones (``target_kind='plan'``) and —
        P1-11a (investigación 2026-07-11) — the ones on the task's PHASE
        (``target_kind='phase'`` with ``target_ref`` = the index of the spec phase
        whose ``tasks`` list contains this task's spec id; before they were dropped).
        Newest first, capped. Empty → ``[]`` → no ``task_comments`` key
        (backward-compat). BYPASSRLS, so an explicit ``tenant_id`` predicate scopes it
        (same defence-in-depth as the prior-feedback read)."""
        if task.plan_id is None:
            return []
        spec_id = (task.inputs or {}).get(PLAN_TASK_SPEC_ID_KEY)
        scope_cond = PlanComment.target_kind == "plan"
        if spec_id:
            scope_cond = or_(
                scope_cond,
                and_(
                    PlanComment.target_kind == "task",
                    PlanComment.target_ref == str(spec_id),
                ),
            )
            phase_index = await self._task_phase_index(session, task, str(spec_id))
            if phase_index is not None:
                scope_cond = or_(
                    scope_cond,
                    and_(
                        PlanComment.target_kind == "phase",
                        PlanComment.target_ref == str(phase_index),
                    ),
                )
        rows = list(
            (
                await session.execute(
                    select(PlanComment)
                    .where(
                        PlanComment.plan_id == task.plan_id,
                        PlanComment.tenant_id == task.tenant_id,
                        PlanComment.deleted_at.is_(None),
                        scope_cond,
                    )
                    .order_by(PlanComment.created_at.desc())
                    .limit(_MAX_TASK_COMMENTS)
                )
            ).scalars()
        )
        comments: list[dict[str, str]] = []
        for row in rows:
            content = str(row.content or "").strip()
            if content:
                comments.append({"scope": str(row.target_kind), "content": content})
        return comments

    async def _read_prior_review_feedback(
        self, session: AsyncSession, task: Task
    ) -> list[dict[str, str]]:
        """The AI reviewer's most recent rejection feedback for ``task`` (A2).

        A task re-dispatched to the implementer after the AI reviewer rejected it
        (``in_review`` → ``backlog`` → ``ready``) otherwise carries no memory of WHY
        it was rejected, so the implementer repeats the same mistake. We read the
        freshest few ``review_comment`` audit events — the reviewer's rejection
        payloads (``failed_criterion`` / ``what_to_fix`` / ``testreport_evidence``,
        persisted by ``apply_reviewer_verdict``) — newest first and project them to
        the minimal feedback shape the worker forwards to the runtime. Empty (no
        prior rejection) → ``[]`` → no ``prior_review_feedback`` key is emitted
        (backward-compat). BYPASSRLS, so an explicit ``tenant_id`` predicate scopes
        it (same defence-in-depth as the ``<test-report>`` read)."""
        rows = list(
            (
                await session.execute(
                    select(TaskAuditEvent.payload)
                    .where(
                        TaskAuditEvent.task_id == task.id,
                        TaskAuditEvent.tenant_id == task.tenant_id,
                        TaskAuditEvent.kind == "review_comment",
                    )
                    .order_by(TaskAuditEvent.at.desc())
                    .limit(_MAX_PRIOR_REVIEW_FEEDBACK)
                )
            ).scalars()
        )
        feedback: list[dict[str, str]] = []
        for payload in rows:
            if not isinstance(payload, dict):
                continue
            # `task_wf_61`: un `review_comment` de APROBACIÓN (el desglose por
            # criterio de un review que pasó) no es feedback de rechazo. Sin
            # este filtro entraría como un bloque VACÍO en el preámbulo del
            # implementador — «te rechazaron por: (nada)», que confunde más que
            # no decir nada. Filtra también los rechazos sin contenido, que
            # tienen el mismo problema y ya existían.
            entry = {
                "failed_criterion": str(payload.get("failed_criterion") or ""),
                "what_to_fix": str(payload.get("what_to_fix") or ""),
                "testreport_evidence": str(payload.get("testreport_evidence") or ""),
            }
            if payload.get("approved") or not any(entry.values()):
                continue
            feedback.append(entry)
        return feedback

    # P0-7: cola del output del run muerto — suficiente para orientar sin
    # desplazar la tarea del prompt (mismo orden de magnitud que el tail del
    # test-report, _TEST_REPORT_LOG_TAIL).
    _PRIOR_FAILURE_OUTPUT_TAIL = 1500

    async def _task_phase_index(
        self, session: AsyncSession, task: Task, spec_id: str
    ) -> int | None:
        """El índice de la fase del spec que contiene ``spec_id`` (P1-11a).

        Best-effort: sin plan/spec/fase que lo contenga → ``None`` (los
        comentarios de fase simplemente no aplican)."""
        plan_spec = (
            await session.execute(
                select(Plan.specification).where(
                    Plan.id == task.plan_id, Plan.tenant_id == task.tenant_id
                )
            )
        ).scalar_one_or_none()
        phases = (plan_spec or {}).get("phases")
        if not isinstance(phases, list):
            return None
        for index, phase in enumerate(phases):
            tasks = phase.get("tasks") if isinstance(phase, dict) else None
            if isinstance(tasks, list) and spec_id in [str(t) for t in tasks]:
                return index
        return None

    async def _read_prior_failure(self, session: AsyncSession, task: Task) -> dict[str, str] | None:
        """The LATEST execution's failure payload, or ``None`` (P0-7).

        Only the most recent execution counts: a later successful run (done —
        e.g. the failure was transient and a retry finished) supersedes the
        failure, so a stale crash does not haunt the agent forever. Review
        rejections travel by their own rail (``prior_review_feedback``).
        BYPASSRLS → explicit ``tenant_id`` predicate (defence-in-depth)."""
        latest = (
            await session.execute(
                select(Execution.status, Execution.abort_code, Execution.output)
                .where(
                    Execution.task_id == task.id,
                    Execution.tenant_id == task.tenant_id,
                )
                .order_by(Execution.created_at.desc())
                .limit(1)
            )
        ).first()
        if latest is None or latest.status not in ("failed", "aborted"):
            return None
        output_tail = str(latest.output or "")[-self._PRIOR_FAILURE_OUTPUT_TAIL :]
        return {
            "status": str(latest.status),
            "abort_code": str(latest.abort_code or ""),
            "output_tail": output_tail,
        }

    # `task_wf_70`: cuántas predecesoras viajan y cuánto de cada resumen. Tope
    # bajo a propósito — cinco dependencias con su contrato entero desplazarían
    # del prompt la tarea PROPIA, que es lo que hay que hacer.
    _PREDECESSORS_MAX = 5
    _PREDECESSOR_SUMMARY_MAX = 1200

    async def _read_predecessor_briefs(
        self, session: AsyncSession, task: Task
    ) -> list[dict[str, str]]:
        """Qué entregaron las tareas de las que ``task`` depende (`task_wf_70`).

        Hasta ahora ``depends_on`` solo servía para reconciliar el DAG: el
        agente de la tarea 3 no sabía nada de lo que hicieron la 1 y la 2, así
        que reinventaba el contrato en vez de consumirlo. Un plan largo no era
        un equipo trabajando sobre un diseño común, eran N tareas aisladas
        compartiendo directorio.

        Acotado a las dependencias **directas** ya ``done``: una dependencia sin
        terminar no tiene nada que contar, y el cierre transitivo traería el
        plan entero al prompt. El resumen es el ``output`` de su última
        ejecución completada — lo que su propio agente declaró haber entregado.
        BYPASSRLS → predicado explícito de ``tenant_id``.
        """
        dep_ids = list(
            (
                await session.execute(
                    select(TaskDependency.depends_on_task_id).where(
                        TaskDependency.task_id == task.id
                    )
                )
            ).scalars()
        )
        if not dep_ids:
            return []
        rows = list(
            (
                await session.execute(
                    select(Task.id, Task.title)
                    .where(
                        Task.id.in_(dep_ids),
                        Task.tenant_id == task.tenant_id,
                        Task.status == TaskStatus.DONE.value,
                    )
                    .limit(self._PREDECESSORS_MAX)
                )
            ).all()
        )
        briefs: list[dict[str, str]] = []
        for row in rows:
            output = (
                await session.execute(
                    select(Execution.output)
                    .where(
                        Execution.task_id == row.id,
                        Execution.tenant_id == task.tenant_id,
                        Execution.status == "done",
                    )
                    .order_by(Execution.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            summary = str(output or "").strip()[: self._PREDECESSOR_SUMMARY_MAX]
            if not summary:
                # Sin resumen no hay nada sobre lo que construir; el hueco solo
                # ocuparía sitio en el prompt.
                continue
            briefs.append({"title": str(row.title or ""), "summary": summary})
        return briefs

    # ADR 0114: cuántas Q&A respondidas viajan al siguiente run (las más
    # recientes primero) y tope defensivo del texto de cada lado.
    _HUMAN_ANSWERS_MAX = 3
    _HUMAN_ANSWER_TEXT_MAX = 2000

    async def _read_prior_human_answers(
        self, session: AsyncSession, task: Task
    ) -> list[dict[str, str]]:
        """Respuestas humanas a ``ask_human`` de intentos previos (ADR 0114).

        Lee los ``ApprovalRequest`` RESUELTOS-aprobados con categoría
        ``human_question`` de ESTA task (los rechazados no llevan guía; los
        pendientes aún no tienen respuesta) — la pregunta vive en
        ``action.args.question`` y la respuesta del humano en ``reason``.
        Más recientes primero, cap ``_HUMAN_ANSWERS_MAX``. BYPASSRLS →
        predicado ``tenant_id`` explícito (defensa en profundidad)."""
        from api_server.db.domain import ApprovalRequest, ApprovalRequestStatus

        rows = (
            await session.execute(
                select(ApprovalRequest.action, ApprovalRequest.reason)
                .where(
                    ApprovalRequest.task_id == task.id,
                    ApprovalRequest.tenant_id == task.tenant_id,
                    ApprovalRequest.category == "human_question",
                    ApprovalRequest.status == ApprovalRequestStatus.APPROVED,
                )
                .order_by(ApprovalRequest.resolved_at.desc())
                .limit(self._HUMAN_ANSWERS_MAX)
            )
        ).all()
        answers: list[dict[str, str]] = []
        for action, reason in rows:
            question = str(((action or {}).get("args") or {}).get("question") or "").strip()
            answer = str(reason or "").strip()
            if question and answer:
                answers.append(
                    {
                        "question": question[: self._HUMAN_ANSWER_TEXT_MAX],
                        "answer": answer[: self._HUMAN_ANSWER_TEXT_MAX],
                    }
                )
        return answers

    async def _resolve_model_spec(
        self, session: AsyncSession, agent: Agent, project: Project | None
    ) -> dict[str, Any]:
        """Resolve the effective ``model_config`` for ``agent`` (ADR 0055 chain).

        Default seguro de model_config para spec legacy ``{}`` (Plan 06.17
        task_06_17_10 / ADR 0055): un agente sin spec de modelo (``{}`` legacy, o
        un agente SEMBRADO que solo trae ``system_prompts``) hereda por la cadena
        plataforma → proyecto → equipo → agente — el nivel MÁS específico que
        pinee provider+model rellena el spec, preservando las claves no-modelo del
        agente. Un spec ya pineado (o ``kind`` scripted) se devuelve verbatim.
        NUNCA levanta por un default mal puesto; el caller (C3 F07) decide qué
        hacer si la cadena sigue sin resolver provider+model."""
        model_spec = dict(agent.model_config or {})
        if config_needs_default_model(model_spec):
            platform_default = await get_default_model_config(session)
            team_cfg = await self._team_model_config(session, project)
            project_cfg = dict(getattr(project, "model_config", None) or {}) if project else {}
            model_spec = resolve_model_config_chain(
                model_spec, team_cfg, project_cfg, platform_default
            )
        return model_spec

    async def _team_model_config(
        self, session: AsyncSession, project: Project | None
    ) -> dict[str, Any]:
        """``model_config`` del equipo del proyecto para la cadena de herencia
        (Ola A). Vacío si el proyecto no tiene equipo o no se encuentra. El
        orchestrator corre con BYPASSRLS; aun así filtramos por tenant del
        proyecto como defensa en profundidad."""
        if project is None:
            return {}
        team_id = getattr(project, "team_id", None)
        if team_id is None:
            return {}
        team = (
            await session.execute(
                select(Team).where(
                    Team.id == team_id,
                    Team.tenant_id == project.tenant_id,
                )
            )
        ).scalar_one_or_none()
        return dict(team.model_config or {}) if team is not None else {}

    async def _route_human(
        self, session: AsyncSession, task: Task, human_agent: Agent
    ) -> _HumanDispatch:
        """The human route (task_16_05): NO runtime container.

        Resolve the concrete User from the human agent's
        ``human_agent_config.assigned_user_id``, create a ``HumanTaskAssignment``
        (status ``pending_acceptance``), transition the task ``ready ->
        assigned_to_human`` via the §7.2 state machine (the move is legal ONLY
        because the assignee is a Human Agent), and return the Plan 10 fan-out
        event so ``handle`` can notify the user. Everything below is committed
        in the same transaction the caller opened."""
        config = (
            await session.execute(
                select(HumanAgentConfig).where(
                    HumanAgentConfig.agent_id == human_agent.id,
                    HumanAgentConfig.tenant_id == task.tenant_id,
                )
            )
        ).scalar_one_or_none()
        assigned_user_id = config.assigned_user_id if config is not None else None

        assignment = HumanTaskAssignment(
            tenant_id=task.tenant_id,
            task_id=task.id,
            human_agent_id=human_agent.id,
            assigned_to_user_id=assigned_user_id,
            assigned_at=datetime.now(UTC),
            status=HumanTaskAssignmentStatus.PENDING_ACCEPTANCE.value,
        )
        session.add(assignment)
        await session.flush()  # populate assignment.id

        # ready -> assigned_to_human. Gated on the Human assignee type — the
        # state machine REJECTS this move for an AI assignee (task_16_04), so
        # routing it here for a non-human would raise rather than mis-transition.
        transition_task_status(task, _ASSIGNED_TO_HUMAN, assignee_agent_type=AgentType.HUMAN)

        event = {
            "event_type": _HUMAN_TASK_ASSIGNED_EVENT,
            "tenant_id": str(task.tenant_id),
            "context": {
                "task_id": str(task.id),
                "task_title": task.title,
                "assigned_to_user_id": (
                    str(assigned_user_id) if assigned_user_id is not None else None
                ),
                "human_agent_id": str(human_agent.id),
            },
            "locale": None,
        }
        return _HumanDispatch(
            event=event,
            assignment_id=str(assignment.id),
            assigned_to_user_id=(str(assigned_user_id) if assigned_user_id is not None else None),
        )

    async def _candidates(
        self, session: AsyncSession, task: Task, project: Project | None = None
    ) -> list[Candidate]:
        """Agents eligible to take `task` — with their load.

        PROJ-04: cuando el proyecto tiene equipo, el pool son sus
        ``team_members`` más los agentes ``project_local`` del propio proyecto
        (una elección deliberada del operador); los globales del tenant que no
        son del equipo ya NO reciben sus tareas. Sin equipo, el pool clásico:
        project-local del proyecto + globales del tenant."""
        team_id = project.team_id if project is not None else None
        project_local = and_(
            Agent.scope == "project_local",
            Agent.project_id == task.project_id,
        )
        if team_id is not None:
            member_ids = select(TeamMember.agent_id).where(TeamMember.team_id == team_id)
            pool_filter = or_(Agent.id.in_(member_ids), project_local)
        else:
            pool_filter = or_(project_local, Agent.scope.in_(_GLOBAL_SCOPES))
        agents = (
            (
                await session.execute(
                    select(Agent).where(
                        Agent.tenant_id == task.tenant_id,
                        Agent.deleted_at.is_(None),
                        Agent.agent_type == "ai",
                        pool_filter,
                    )
                )
            )
            .scalars()
            .all()
        )
        candidates: list[Candidate] = []
        for agent in agents:
            active = (
                await session.execute(
                    select(func.count())
                    .select_from(Task)
                    .where(
                        Task.assigned_agent_id == agent.id,
                        Task.status == _IN_PROGRESS,
                    )
                )
            ).scalar_one()
            candidates.append(
                Candidate(
                    agent_id=str(agent.id),
                    active_task_count=int(active),
                    # ADR 0115 fase 1: el rol del agente es su "skill" de matching.
                    skills=frozenset({str(agent.role)}) if agent.role else frozenset(),
                )
            )
        return candidates

    async def _task_required_skills(self, session: AsyncSession, task: Task) -> frozenset[str]:
        """El rol del spec de la tarea como requisito de matching (ADR 0115 f1).

        Best-effort: sin plan/spec/rol → vacío (skill_match cae a load-balanced,
        el comportamiento previo). Fase 2 (skills declaradas por tarea) queda en
        el ADR."""
        if task.plan_id is None:
            return frozenset()
        spec_id = (task.inputs or {}).get(PLAN_TASK_SPEC_ID_KEY)
        if not spec_id:
            return frozenset()
        plan_spec = (
            await session.execute(
                select(Plan.specification).where(
                    Plan.id == task.plan_id, Plan.tenant_id == task.tenant_id
                )
            )
        ).scalar_one_or_none()
        for entry in (plan_spec or {}).get("tasks") or []:
            if isinstance(entry, dict) and str(entry.get("id")) == str(spec_id):
                role = str(entry.get("role") or "").strip()
                return frozenset({role}) if role else frozenset()
        return frozenset()

    def _pick(
        self,
        project: Project | None,
        task: Task,
        candidates: list[Candidate],
        *,
        required_skills: frozenset[str] = frozenset(),
    ) -> str | None:
        """Apply the project's assignment policy to the candidate pool.

        A preset ``assigned_agent_id`` (the plan's per-task assignment, resolved
        from the spec ``role`` at sync time — Track 2) is AUTHORITATIVE and wins
        regardless of policy: implementation lands on the chosen agent instead of
        the least-loaded one. Only when no preset exists does the policy decide.
        """
        if task.assigned_agent_id is not None:
            return str(task.assigned_agent_id)
        policy = AssignmentPolicy.LOAD_BALANCED
        if project is not None and isinstance(project.worker_config, dict):
            raw = project.worker_config.get("assignment_policy")
            if raw:
                # An unknown policy string keeps the load-balanced default.
                with contextlib.suppress(ValueError):
                    policy = AssignmentPolicy(raw)

        if policy is AssignmentPolicy.MANUAL:
            preset = str(task.assigned_agent_id) if task.assigned_agent_id else None
            return assign_manual(TaskRequirement(task_id=str(task.id), preset_agent_id=preset))
        if policy is AssignmentPolicy.ROUND_ROBIN:
            return self._round_robin.pick(candidates)
        if policy is AssignmentPolicy.SKILL_MATCH:
            # ADR 0115 fase 1: matching por ROL (spec de la tarea vs rol del
            # agente). Sin señal (score 0 / sin rol) → load-balanced, el
            # comportamiento previo — la política ya no es un no-op.
            matched = assign_skill_match(
                TaskRequirement(task_id=str(task.id), required_skills=required_skills),
                candidates,
            )
            return matched if matched is not None else assign_load_balanced(candidates)
        return assign_load_balanced(candidates)


def build_dispatch_handler(settings: Settings) -> EventHandler:
    """Build the production dispatch handler from `Settings`."""
    engine = create_async_engine(settings.database_url)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    celery_app = Celery(broker=settings.broker_url)
    # Publish post-dispatch status events onto the same events:tasks stream the
    # consumer reads (settings.redis_url) so the Kanban updates live.
    redis: Redis = Redis.from_url(settings.redis_url)
    dispatcher = TaskDispatcher(
        sessionmaker=sessionmaker, celery_app=celery_app, settings=settings, redis=redis
    )
    return dispatcher.handle
