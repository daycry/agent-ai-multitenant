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
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from api_server.agent_tools_enforcement import (
    combine_tool_allowlists,
    resolve_agent_tool_names,
    serialize_agent_tool_specs,
)
from api_server.budgets import budget_pause_block
from api_server.db.domain import (
    Agent,
    AgentType,
    HumanAgentConfig,
    HumanTaskAssignment,
    HumanTaskAssignmentStatus,
    Project,
    Task,
    TaskStatus,
)
from api_server.task_state_machine import transition_task_status
from celery import Celery
from sqlalchemy import and_, func, or_, select
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
from orchestrator.consumer import EventHandler
from orchestrator.events import EVENT_TASK_CREATED, EVENT_TASK_STATUS_CHANGED, TaskEvent

_log = structlog.get_logger("orchestrator.dispatch")

# A task is dispatchable the moment it reaches `ready`.
_READY = "ready"
_IN_PROGRESS = "in_progress"
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


def _is_ready_trigger(event: TaskEvent) -> bool:
    """True when the event means a task just became dispatchable."""
    if event.type == EVENT_TASK_STATUS_CHANGED:
        return event.payload.get("new_status") == _READY
    if event.type == EVENT_TASK_CREATED:
        return event.payload.get("status") == _READY
    return False


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
    ) -> None:
        self._sessionmaker = sessionmaker
        self._celery = celery_app
        self._settings = settings
        self._round_robin = RoundRobin()

    async def handle(self, event: TaskEvent) -> None:
        """Event handler — dispatch a task that has just gone `ready`.

        Branches on the assignee Agent's ``agent_type`` (task_16_05): a task
        assigned to a Human Agent takes the human route (NO runtime container —
        a ``HumanTaskAssignment`` row + a notification to the assigned user); an
        AI-assigned (or pool-assigned) task keeps the existing runtime-pool path
        untouched.
        """
        if not _is_ready_trigger(event):
            return
        task_id = UUID(event.task_id)
        result = await self._dispatch(task_id)
        if result is None:
            return
        if isinstance(result, _HumanDispatch):
            await self._notify_human_assignment(event, result)
            return
        await self._enqueue_ai_run(event, task_id, result)

    async def _enqueue_ai_run(self, event: TaskEvent, task_id: UUID, result: _AiDispatch) -> None:
        """Enqueue the worker run for an AI-routed task (the existing path)."""
        request = result.request
        # Operator-tunable backstop limits, read fresh per dispatch so a
        # platform-settings change takes effect without restarting the
        # workers (Plan 06.14 task_06_14_04 / workers-orchestrator-10).
        soft_limit, hard_limit = await self._execution_time_limits()
        # send_task does blocking broker I/O — keep it off the loop.
        #
        # The task is already committed `in_progress` with an assignee at this
        # point. If the broker enqueue fails (broker down, network blip) the
        # task would be stranded `in_progress` yet never picked up by a worker
        # (workers-orchestrator-8). Revert it to `ready` in a fresh transaction
        # so the next dispatch trigger re-enqueues it. A transactional outbox
        # would be sturdier but is overkill here — revert-on-failure is the
        # pragmatic safe fix (Plan 06.14 task_06_14_05).
        try:
            await asyncio.to_thread(
                self._send_run_execution,
                request,
                soft_limit,
                hard_limit,
            )
        except Exception as exc:
            await self._revert_to_ready(task_id)
            _log.error(
                "orchestrator.dispatch_enqueue_failed",
                task_id=event.task_id,
                agent_id=request["agent_id"],
                error=str(exc),
            )
            return
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

    async def _revert_to_ready(self, task_id: UUID) -> None:
        """Undo a dispatch whose broker enqueue failed: move the task back to
        `ready` and clear the assignment so it can be re-dispatched.

        Best-effort and idempotent — only a task still `in_progress` is
        reverted (a worker may have raced ahead, though the broker-down case
        that triggers this makes that unlikely). A revert that itself fails is
        logged, never masking the original enqueue error."""
        try:
            async with self._sessionmaker() as session, session.begin():
                task = (
                    await session.execute(select(Task).where(Task.id == task_id))
                ).scalar_one_or_none()
                if task is None or task.status != _IN_PROGRESS:
                    return
                task.status = _READY
                task.assigned_agent_id = None
                task.started_at = None
        except Exception as revert_exc:  # pragma: no cover - defensive
            _log.error(
                "orchestrator.dispatch_revert_failed",
                task_id=str(task_id),
                error=str(revert_exc),
            )

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

    async def _dispatch(self, task_id: UUID) -> _AiDispatch | _HumanDispatch | None:
        """Route a ready task: AI (pick agent → worker payload) or human
        (create the assignment, transition to ``assigned_to_human``). Returns
        None if the task is no longer ready, is budget-paused, or no AI agent
        is available."""
        async with self._sessionmaker() as session, session.begin():
            task = (
                await session.execute(select(Task).where(Task.id == task_id))
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
        project = (
            await session.execute(select(Project).where(Project.id == task.project_id))
        ).scalar_one_or_none()
        candidates = await self._candidates(session, task)
        agent_id = self._pick(project, task, candidates)
        if agent_id is None:
            _log.warning("orchestrator.no_agent_for_task", task_id=str(task.id))
            return None

        agent = (
            await session.execute(select(Agent).where(Agent.id == UUID(agent_id)))
        ).scalar_one()
        task.status = _IN_PROGRESS
        task.assigned_agent_id = agent.id
        task.started_at = datetime.now(UTC)

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
            "model": dict(agent.model_config),
            "budgets": None,
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
        return _AiDispatch(request=request)

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
        """Apply the project's assignment policy to the candidate pool."""
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
    dispatcher = TaskDispatcher(sessionmaker=sessionmaker, celery_app=celery_app, settings=settings)
    return dispatcher.handle
