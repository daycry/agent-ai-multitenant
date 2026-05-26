"""The worker conducts a real execution (Plan 02 Fase G / task_02_30).

Fases A-F built the components; this is where the worker wires them
into a live run. `conduct_execution`:

  1. creates the `executions` row in `running` state — so the
     per-execution Redis stream has a stable id the UI can connect to;
  2. launches the `agent-runtime` container for the task and streams
     its stdout, republishing every JSON event onto `exec:{id}` (the
     stream `/ws/executions/{id}` tails);
  3. finalises the row with the streamed steps_log and usage roll-ups
     once the container exits.

The DB sessionmaker and Redis client are injected so the integration
tests can point them at the throwaway test stack; the Celery task
(task_02_31) builds them from `Settings`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from api_server.db.approval_repo import request_approval_if_needed
from api_server.db.domain import Project, Task
from api_server.db.execution_repo import (
    create_running_execution,
    finalize_execution,
    get_execution,
)
from api_server.events import publish_execution_event, publish_task_status_changed
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from workers.config import Settings
from workers.container import AgentContainerRunner, ContainerSpec
from workers.memorizer import trigger_memorize

_log = structlog.get_logger("workers.execution")

# Status the agent loop reports when it parks on a sensitive action —
# mirrors agent_runtime.state.STATUS_AWAITING_APPROVAL and
# ExecutionStatus.AWAITING_HUMAN_APPROVAL.
_AWAITING_APPROVAL = "awaiting_human_approval"

# A zeroed usage roll-up — used when a run produces no result line
# (the container crashed or timed out before `execution.finished`).
_EMPTY_USAGE: dict[str, Any] = {
    "iterations": 0,
    "total_tokens": 0,
    "cost_usd": 0.0,
    "tool_calls": 0,
    "model_calls": 0,
}


@dataclass(frozen=True)
class ExecutionRequest:
    """Everything the worker needs to conduct one execution.

    The orchestrator (task_02_31) builds this from a task event. `task`
    and `model` are the spec dicts the agent-runtime entrypoint expects
    (`AGENT_TASK_SPEC`); `task_id` / `tenant_id` are the real DB ids the
    `executions` row is keyed on.
    """

    tenant_id: str
    task_id: str
    agent_id: str | None
    task: dict[str, Any]
    model: dict[str, Any]
    budgets: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe dict — the Celery payload the orchestrator sends."""
        return {
            "tenant_id": self.tenant_id,
            "task_id": self.task_id,
            "agent_id": self.agent_id,
            "task": self.task,
            "model": self.model,
            "budgets": self.budgets,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> ExecutionRequest:
        """Rebuild a request from the Celery payload (worker side)."""
        return cls(
            tenant_id=raw["tenant_id"],
            task_id=raw["task_id"],
            agent_id=raw.get("agent_id"),
            task=raw["task"],
            model=raw["model"],
            budgets=raw.get("budgets"),
        )


@dataclass(frozen=True)
class ExecutionOutcome:
    """The result of one conducted execution."""

    execution_id: str
    status: str
    abort_code: str | None

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe summary — what the Celery result backend stores."""
        return {
            "execution_id": self.execution_id,
            "status": self.status,
            "abort_code": self.abort_code,
        }


@dataclass
class _RuntimeResult:
    """The agent run's result, in the shape `finalize_execution`
    duck-types (`ExecutionResultLike`)."""

    status: str
    abort_code: str | None
    output: str | None
    iterations: int
    steps: list[dict[str, Any]]
    usage: dict[str, Any]


def _agent_spec(
    request: ExecutionRequest, approval_policy: dict[str, Any] | None
) -> dict[str, Any]:
    """The `AGENT_TASK_SPEC` payload for the container."""
    spec: dict[str, Any] = {"task": request.task, "model": request.model}
    if request.budgets:
        spec["budgets"] = request.budgets
    # With a policy the loop gates sensitive tool calls (task_02_33).
    if approval_policy:
        spec["approval_policy"] = approval_policy
    return spec


async def _load_project(session: AsyncSession, task_id: UUID) -> Project | None:
    """The task's project — its `human_approval_policy` gates the run."""
    task = await session.get(Task, task_id)
    if task is None:
        return None
    return await session.get(Project, task.project_id)


def _parse_line(line: str) -> dict[str, Any] | None:
    """Parse one stdout line into a JSON event, or None if it isn't one.

    The agent-runtime emits one JSON object per line; LangGraph (and the
    occasional library) may also print free text — those are ignored.
    """
    if not line.startswith("{"):
        return None
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _assemble_result(
    final_result: dict[str, Any] | None,
    steps: list[dict[str, Any]],
    *,
    timed_out: bool,
    exit_code: int,
    runtime_error: str | None,
) -> _RuntimeResult:
    """Fold the streamed steps + final result line into a `_RuntimeResult`.

    When the container produced an `execution.finished` line, that is
    the result. Otherwise the run failed (crash, timeout, or an
    `execution.error` line) — keep whatever steps streamed and mark it
    `failed`.
    """
    if final_result is not None:
        return _RuntimeResult(
            status=final_result.get("status", "failed"),
            abort_code=final_result.get("abort_code"),
            output=final_result.get("output"),
            iterations=int(final_result.get("iterations", 0)),
            steps=steps,
            usage=final_result.get("usage") or dict(_EMPTY_USAGE),
        )

    if runtime_error is not None:
        detail = f"agent-runtime error: {runtime_error}"
    elif timed_out:
        detail = "agent-runtime container timed out"
    else:
        detail = f"agent-runtime container exited {exit_code} with no result"
    return _RuntimeResult(
        status="failed",
        abort_code=None,
        output=detail,
        iterations=0,
        steps=steps,
        usage=dict(_EMPTY_USAGE),
    )


async def conduct_execution(  # noqa: PLR0915 - tramos lineales (seed/run/finalize/publish)
    request: ExecutionRequest,
    *,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Redis,
) -> ExecutionOutcome:
    """Run one task end to end: container → Redis stream → `executions` row."""
    task_id = UUID(request.task_id)
    async with sessionmaker() as session, session.begin():
        execution = await create_running_execution(
            session,
            tenant_id=UUID(request.tenant_id),
            task_id=task_id,
            agent_id=UUID(request.agent_id) if request.agent_id else None,
        )
        execution_id = execution.id
        project = await _load_project(session, task_id)
        approval_policy = project.human_approval_policy if project is not None else None
    exec_id = str(execution_id)
    _log.info("workers.execution_started", execution_id=exec_id, task_id=request.task_id)

    # The container's stdout is pumped by a background thread; bridge
    # each line onto an asyncio queue so the live Redis publishing (and
    # event collection) happens on the running loop.
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    steps: list[dict[str, Any]] = []
    final_result: dict[str, Any] | None = None
    runtime_error: str | None = None

    def on_line(line: str) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, line)

    async def drain() -> None:
        nonlocal final_result, runtime_error
        while True:
            line = await queue.get()
            if line is None:
                return
            event = _parse_line(line)
            if event is None or not event.get("event"):
                continue
            kind = str(event["event"])
            if kind == "step" and isinstance(event.get("step"), dict):
                steps.append(event["step"])
            elif kind == "execution.finished":
                final_result = event.get("result")
            elif kind == "execution.error":
                runtime_error = event.get("error")
            payload = {key: value for key, value in event.items() if key != "event"}
            await publish_execution_event(redis, exec_id, event_type=kind, payload=payload)

    drainer = asyncio.create_task(drain())
    container_spec = ContainerSpec(
        image=settings.agent_runtime_image,
        env={"AGENT_TASK_SPEC": json.dumps(_agent_spec(request, approval_policy))},
        labels={"com.agentic-platform.execution-id": exec_id},
    )
    runner = AgentContainerRunner(settings)
    container_result = await asyncio.to_thread(runner.run_streamed, container_spec, on_line)
    await queue.put(None)
    await drainer

    result = _assemble_result(
        final_result,
        steps,
        timed_out=container_result.timed_out,
        exit_code=container_result.exit_code,
        runtime_error=runtime_error,
    )
    approval = final_result.get("approval") if final_result else None
    task_event: tuple[Any, str, str] | None = None
    async with sessionmaker() as session, session.begin():
        await finalize_execution(session, execution_id, result=result)
        # A run parked on a sensitive action becomes a real
        # ApprovalRequest — the approval engine on the live run (task_02_33).
        # request_approval_if_needed also moves the TASK to
        # `awaiting_human_approval` and frees its agent (ADR 0020).
        if result.status == _AWAITING_APPROVAL and approval:
            execution = await get_execution(session, execution_id)
            project = await _load_project(session, task_id)
            task = await session.get(Task, task_id)
            if execution is not None and project is not None and task is not None:
                old_status = task.status
                await request_approval_if_needed(
                    session,
                    execution=execution,
                    project=project,
                    category=str(approval.get("category", "")),
                    action=dict(approval.get("action") or {}),
                )
                if task.status != old_status:
                    task_event = (task, old_status, task.status)

    # Publish the task event AFTER the commit so the board sees a
    # consistent state. publish_* is best-effort and swallows its own errors.
    if task_event is not None:
        task_obj, old, new = task_event
        await publish_task_status_changed(redis, task_obj, old_status=old, new_status=new)

    # Fire-and-forget Memorizer (Plan 04.5 task_04_5_02). The Celery
    # task does the LLM distillation off the executor's critical path,
    # so the executor returns immediately even if Ollama is slow.
    # `trigger_memorize` swallows broker errors — a Memorizer outage
    # must never break an agent run.
    trigger_memorize(execution_id, result.status)

    _log.info("workers.execution_finished", execution_id=exec_id, status=result.status)
    return ExecutionOutcome(
        execution_id=exec_id,
        status=result.status,
        abort_code=result.abort_code,
    )
