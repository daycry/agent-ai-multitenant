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
from typing import Any
from uuid import UUID

import structlog
from api_server.agent_skills_enforcement import resolve_agent_skill_prompt_fragments
from api_server.agent_tools_enforcement import (
    combine_tool_allowlists,
    resolve_agent_tool_names,
    serialize_agent_tool_specs,
)
from api_server.budgets import budget_pause_block, resolve_execution_budgets
from api_server.chat.sync_to_kanban import PLAN_TASK_SPEC_ID_KEY
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
)
from api_server.db.models import TaskAuditEvent
from api_server.db.plan_comment import PlanComment
from api_server.db.platform_settings import (
    config_needs_default_model,
    get_default_execution_budgets,
    get_default_model_config,
    resolve_model_config_chain,
)
from api_server.events import publish_task_status_changed
from api_server.plan_progress import (
    TaskSnapshot,
    transition_to_blocked,
    transition_to_pending_human_validation,
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
            result = transition_to_pending_human_validation(plan.status, snapshots)
            if not result.transitioned:
                # c3 (audit 2026-07-03): a plan whose only remaining open tasks
                # are `blocked` can never reach pending_human_validation (blocked
                # counts as open), so it would sit `in_progress` forever with no
                # automatic route out. Escalate it to `blocked` (same atomic,
                # idempotent status=in_progress guard) so the operator sees the
                # stall and can unblock/retry a task.
                blocked = transition_to_blocked(plan.status, snapshots)
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
        except Exception as exc:
            _log.error(
                "orchestrator.review_enqueue_failed",
                task_id=str(task_id),
                error=str(exc),
            )

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
        agent_tool_names = await resolve_agent_tool_names(session, reviewer.id)
        allowed_tools = combine_tool_allowlists(agent_tool_names, None)
        tool_specs = await serialize_agent_tool_specs(session, reviewer.id)
        skill_prompt_fragments = await resolve_agent_skill_prompt_fragments(session, reviewer.id)

        # Hallazgo H2 (refactor 2026-07-07): la MISMA cadena de herencia ADR 0055
        # que el implementador — antes duplicada inline aquí, con riesgo de que un
        # cambio futuro en la cadena solo se aplicara a una de las dos ramas.
        model_spec = await self._resolve_model_spec(session, reviewer, project)

        platform_budgets = await get_default_execution_budgets(session)
        budgets = resolve_execution_budgets(
            platform_default=platform_budgets,
            project_override=getattr(project, "execution_budgets", None),
        )

        # The implementer's most recent output for this task — what the reviewer judges.
        prior_output = (
            await session.execute(
                select(Execution.output)
                .where(Execution.task_id == task.id, Execution.tenant_id == task.tenant_id)
                .order_by(Execution.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

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

        request: dict[str, Any] = {
            "tenant_id": str(task.tenant_id),
            "task_id": str(task.id),
            "agent_id": str(reviewer.id),
            # Marks this as a review run — the worker applies the verdict (loop_03)
            # instead of the normal done/failed task transition (dag_01).
            "review": True,
            "task": {
                "id": str(task.id),
                "title": task.title,
                "description": task.description or "",
            },
            "review_context": {
                # F1.6a (auditoría 2026-07-02): el reviewer certifica contra los
                # acceptance_criteria REALES de la task — antes recibía la
                # description, mientras el implementador trabajaba contra los
                # criteria: dos definiciones de "done" distintas en el mismo
                # ciclo. La description queda solo como fallback sin criteria.
                "acceptance_criteria": _render_acceptance_criteria(task),
                "implementer_output": prior_output or "",
                # `<test-report>` block (prod-17 test_02); "" when no tests ran yet.
                "test_report": test_report,
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
        project_commands = getattr(project, "allowed_commands", None)
        request["allowed_commands"] = [str(c) for c in (project_commands or [])]
        project_runtime = getattr(project, "default_runtime_template", None)
        if project_runtime:
            request["default_runtime_template"] = str(project_runtime)
        project_mcp_servers = getattr(project, "mcp_servers", None)
        if project_mcp_servers:
            request["mcp_servers"] = [dict(server) for server in project_mcp_servers]
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
                return await self._route_ai(session, task)

            return await self._route_human(session, task, human_agent)

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

    async def _route_ai(self, session: AsyncSession, task: Task) -> _AiDispatch | None:
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
        candidates = await self._candidates(session, task)
        agent_id = self._pick(project, task, candidates)
        if agent_id is None:
            _log.warning("orchestrator.no_agent_for_task", task_id=str(task.id))
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

        # Per-agent tool enforcement (Plan 06.15 task_06_15_02). When the
        # agent has `agent_tools` rows its resolved toolset is restricted to
        # those tool names; the worker forwards the allowlist into the task
        # spec and the runtime's ToolRegistry rejects any tool outside it at
        # call time. No rows → `resolve_agent_tool_names` returns None →
        # `combine_tool_allowlists` returns None → no `allowed_tools` key is
        # emitted, preserving the current unrestricted behaviour. The
        # task-dispatch path carries no chat-mode allowlist (modes apply to
        # the chat/conversation path), so the per-agent set stands alone
        # here; `combine_tool_allowlists` intersects with a mode allowlist
        # when one is present.
        agent_tool_names = await resolve_agent_tool_names(session, agent.id)
        allowed_tools = combine_tool_allowlists(agent_tool_names, None)

        # Executable ToolSpec serialisation (Plan 06.18 task_06_18_05). The
        # allowlist (names) is not enough for the runtime to REGISTER a tool —
        # it needs the implementation_type + type config. We serialise the
        # agent's assigned Tool rows so the runtime boot wires the real
        # executors (file/network/run_*/custom) under canonical names instead
        # of falling into the silent "unknown tool". `None` when the agent has
        # no assignments → no `tool_specs` key → the runtime keeps the pre-06.18
        # echo/noop behaviour (06.15 backward-compat).
        tool_specs = await serialize_agent_tool_specs(session, agent.id)

        # Skills → inyección de prompt (Plan 06.18 task_06_18_13 / ADR 0050). El
        # prompt_fragment de las skills asignadas se threadea al spec para que el
        # runtime lo prependa al system prompt EFECTIVO. `None` cuando el agente
        # no tiene skills → no se emite la clave → el prompt actual queda intacto
        # (backward-compat, mismo sentinel que `tool_specs`).
        skill_prompt_fragments = await resolve_agent_skill_prompt_fragments(session, agent.id)

        # `model_spec` was resolved above (C3 F07) before the atomic claim.

        # Per-run budget envelope (prod-06 task_prod06_budget_02 / workers-10).
        # Resolve platform-default ← project-override and clamp every key to the
        # runtime ceiling, so a runaway loop is bounded by an operator-tunable
        # budget instead of only the agent-runtime's compiled-in defaults. `None`
        # when nothing overrides → the runtime keeps its own dataclass defaults.
        platform_budgets = await get_default_execution_budgets(session)
        budgets = resolve_execution_budgets(
            platform_default=platform_budgets,
            project_override=getattr(project, "execution_budgets", None),
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
            # The agent carries its ModelClient spec; the worker
            # feeds it to the agent-runtime verbatim.
            "model": model_spec,
            "budgets": budgets,
        }
        # Only emit the key when a restriction applies — `None` means "no
        # key", which `ExecutionRequest.from_dict` / `_agent_spec` read as
        # "no restriction". An empty list IS emitted (block every tool).
        if allowed_tools is not None:
            request["allowed_tools"] = allowed_tools

        # Serialised executable ToolSpec list (task_06_18_05). Only emit when
        # the agent has assignments — `None` keeps the key absent so the
        # runtime boot stays on the pre-06.18 path (no new families wired).
        if tool_specs is not None:
            request["tool_specs"] = tool_specs

        # Skill prompt fragments (task_06_18_13). Solo se emite cuando el agente
        # tiene skills — `None` mantiene la clave ausente para que el runtime no
        # altere el system prompt (06.15/06.18 backward-compat).
        if skill_prompt_fragments is not None:
            request["skill_prompt_fragments"] = skill_prompt_fragments

        # Per-project shell-command allowlist (Plan 06.16 task_06_16_02). Thread
        # `projects.allowed_commands` into the spec so the runtime can build a
        # per-project `shell_exec` bound to exactly these program basenames
        # (deny-by-default). A real project always carries the column (default
        # `[]`), so the key is always emitted; an empty list registers a
        # deny-all shell_exec. We coerce to a plain list of strings — the column
        # is TEXT[] and may surface as a tuple/None depending on the driver.
        project_commands = getattr(project, "allowed_commands", None) if project else None
        request["allowed_commands"] = [str(c) for c in (project_commands or [])]

        # Per-project stack runtime (Plan 06.16 task_06_16_03). Thread
        # `projects.default_runtime_template` into the spec so the runtime's
        # `run_*` docker_command tools resolve their RuntimeTemplate from the
        # project stack (a PHP project with `php-phpunit` runs `run_pytest`
        # there). Only emit the key when the project pinned a stack; NULL keeps
        # each tool's own default runtime (backward-compatible). `_agent_spec`
        # reads "no key" as "no override".
        project_runtime = getattr(project, "default_runtime_template", None) if project else None
        if project_runtime:
            request["default_runtime_template"] = str(project_runtime)

        # Per-project MCP servers (Plan 06.18 task_06_18_12 / ADR 0052). Thread
        # `projects.mcp_servers` into the spec so the runtime opens an MCP
        # session per declared server and registers its `<server>.<tool>` tools
        # (which the agent∩mode allowlist then intersects, ADR 0048). Only emit
        # the key when the project declares servers; an empty/absent list keeps
        # the key absent so the runtime opens no MCP session (feature-safe — no
        # behaviour change for projects without MCP).
        project_mcp_servers = getattr(project, "mcp_servers", None) if project else None
        if project_mcp_servers:
            request["mcp_servers"] = [dict(server) for server in project_mcp_servers]

        # Inter-run reviewer feedback (A2). If THIS task was rejected by the AI
        # reviewer on a prior pass (in_review → backlog → ready → here), thread the
        # freshest rejection payloads into the spec so the re-dispatched implementer
        # knows what to fix. No prior rejection → no key (backward-compat: a normal
        # first dispatch is byte-for-byte the previous behaviour).
        prior_feedback = await self._read_prior_review_feedback(session, task)
        if prior_feedback:
            request["prior_review_feedback"] = prior_feedback
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
        = the task's plan-spec id) plus the plan-level ones (``target_kind='plan'``,
        which apply to every task of the plan). Phase comments are out of scope here.
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
            feedback.append(
                {
                    "failed_criterion": str(payload.get("failed_criterion") or ""),
                    "what_to_fix": str(payload.get("what_to_fix") or ""),
                    "testreport_evidence": str(payload.get("testreport_evidence") or ""),
                }
            )
        return feedback

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

    async def _candidates(self, session: AsyncSession, task: Task) -> list[Candidate]:
        """Agents eligible to take `task` — project-local agents of its
        project plus the tenant's global agents — with their load."""
        agents = (
            (
                await session.execute(
                    select(Agent).where(
                        Agent.tenant_id == task.tenant_id,
                        Agent.deleted_at.is_(None),
                        Agent.agent_type == "ai",
                        or_(
                            and_(
                                Agent.scope == "project_local",
                                Agent.project_id == task.project_id,
                            ),
                            Agent.scope.in_(_GLOBAL_SCOPES),
                        ),
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
            candidates.append(Candidate(agent_id=str(agent.id), active_task_count=int(active)))
        return candidates

    def _pick(self, project: Project | None, task: Task, candidates: list[Candidate]) -> str | None:
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
            # Task-level skill data lands with RAG in Plan 04; until then
            # skill_match has nothing to match on and falls through.
            return assign_skill_match(TaskRequirement(task_id=str(task.id)), candidates)
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
