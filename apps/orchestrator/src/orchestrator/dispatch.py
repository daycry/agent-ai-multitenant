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
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import structlog
from api_server.db.domain import Agent, Project, Task
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
# Agent scopes eligible to take a project's task (spec §5.7.5).
_GLOBAL_SCOPES = ("global_builtin", "global_tenant_template")
_RUN_EXECUTION_TASK = "workers.run_execution"


def _is_ready_trigger(event: TaskEvent) -> bool:
    """True when the event means a task just became dispatchable."""
    if event.type == EVENT_TASK_STATUS_CHANGED:
        return event.payload.get("new_status") == _READY
    if event.type == EVENT_TASK_CREATED:
        return event.payload.get("status") == _READY
    return False


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
        """Event handler — dispatch a task that has just gone `ready`."""
        if not _is_ready_trigger(event):
            return
        task_id = UUID(event.task_id)
        request = await self._dispatch(task_id)
        if request is None:
            return
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

    async def _dispatch(self, task_id: UUID) -> dict[str, Any] | None:
        """Pick an agent, move the task to `in_progress`, return the
        worker payload — or None if the task is no longer ready or no
        agent is available."""
        async with self._sessionmaker() as session, session.begin():
            task = (
                await session.execute(select(Task).where(Task.id == task_id))
            ).scalar_one_or_none()
            # Re-check the live state: a stale `ready` event for a task
            # already dispatched (or cancelled) must be a no-op.
            if task is None or task.status != _READY:
                return None

            project = (
                await session.execute(select(Project).where(Project.id == task.project_id))
            ).scalar_one_or_none()
            candidates = await self._candidates(session, task)
            agent_id = self._pick(project, task, candidates)
            if agent_id is None:
                _log.warning("orchestrator.no_agent_for_task", task_id=str(task_id))
                return None

            agent = (
                await session.execute(select(Agent).where(Agent.id == UUID(agent_id)))
            ).scalar_one()
            task.status = _IN_PROGRESS
            task.assigned_agent_id = agent.id
            task.started_at = datetime.now(UTC)
            return {
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
