"""Persistence for agent loop executions (task_02_11 / task_02_12).

The agent loop (`agent_runtime`) produces an execution result; this
module writes it to the `executions` table and reads it back. The loop
result is duck-typed via `ExecutionResultLike` — api-server does not
import `agent_runtime` (the runtime is a separate, container-side
package; the steps_log is opaque JSONB here).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import Execution, ExecutionStatus


class ExecutionResultLike(Protocol):
    """The shape of an `agent_runtime.ExecutionResult` — read-only."""

    status: str
    abort_code: str | None
    output: str | None
    iterations: int
    steps: list[dict[str, Any]]
    usage: dict[str, Any]


async def record_execution(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    task_id: UUID,
    result: ExecutionResultLike,
    agent_id: UUID | None = None,
    started_at: datetime | None = None,
) -> Execution:
    """Persist one agent loop run as an `executions` row.

    `result` is an `agent_runtime.ExecutionResult` (duck-typed). The
    caller owns the transaction — this flushes but does not commit.
    """
    usage = result.usage
    execution = Execution(
        tenant_id=tenant_id,
        task_id=task_id,
        agent_id=agent_id,
        status=result.status,
        abort_code=result.abort_code,
        output=result.output,
        steps_log=list(result.steps),
        iterations=result.iterations,
        total_tokens=int(usage.get("total_tokens", 0)),
        total_cost_usd=Decimal(str(usage.get("cost_usd", 0))),
        tool_call_count=int(usage.get("tool_calls", 0)),
        model_call_count=int(usage.get("model_calls", 0)),
        started_at=started_at,
        # A finished run (done/aborted/failed) gets a completion stamp.
        completed_at=None if result.status == ExecutionStatus.RUNNING else datetime.now(UTC),
    )
    session.add(execution)
    await session.flush()
    return execution


async def create_running_execution(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    task_id: UUID,
    agent_id: UUID | None = None,
    started_at: datetime | None = None,
) -> Execution:
    """Insert an `executions` row in `running` state and return it.

    The worker (task_02_30) calls this *before* it launches the
    container, so the per-execution Redis stream (`exec:{id}`) has a
    stable id the UI can connect to while the run is still live. The
    row is finalised with `finalize_execution` once the container exits.
    The caller owns the transaction — this flushes but does not commit.
    """
    execution = Execution(
        tenant_id=tenant_id,
        task_id=task_id,
        agent_id=agent_id,
        status=ExecutionStatus.RUNNING,
        steps_log=[],
        started_at=started_at or datetime.now(UTC),
    )
    session.add(execution)
    await session.flush()
    return execution


async def supersede_running_executions(
    session: AsyncSession, *, tenant_id: UUID, task_id: UUID
) -> int:
    """Close out any still-`running` execution of `task_id` as failed/superseded.

    Idempotency guard for the worker (Plan 06.14 task_06_14_04 /
    workers-orchestrator-1): with `task_acks_late`, a worker crash
    re-delivers `run_execution`, and a fresh run would otherwise leave the
    crashed attempt as an orphan `running` row forever AND add a duplicate
    live row. Calling this before starting a new run guarantees at most one
    live execution per task. Returns the number of rows superseded; the
    caller owns the transaction. Scoped by `tenant_id` too — the worker is
    BYPASSRLS, so we never rely on RLS for the filter.
    """
    result = await session.execute(
        select(Execution).where(
            Execution.tenant_id == tenant_id,
            Execution.task_id == task_id,
            Execution.status == ExecutionStatus.RUNNING,
        )
    )
    stale = list(result.scalars().all())
    if not stale:
        return 0
    now = datetime.now(UTC)
    for execution in stale:
        execution.status = ExecutionStatus.FAILED
        execution.abort_code = "superseded"
        execution.output = "superseded by a re-delivered execution (worker retry)"
        execution.completed_at = now
    await session.flush()
    return len(stale)


async def finalize_execution(
    session: AsyncSession,
    execution_id: UUID,
    *,
    result: ExecutionResultLike,
) -> Execution | None:
    """Write the final result of a run onto an existing `running` row.

    The counterpart to `create_running_execution`: the worker calls this
    once the agent-runtime container has exited, folding the streamed
    steps_log and usage roll-ups into the row. Returns None if the row
    is absent (or RLS-filtered out). The caller owns the transaction.
    """
    execution = await get_execution(session, execution_id)
    if execution is None:
        return None

    usage = result.usage
    execution.status = result.status
    execution.abort_code = result.abort_code
    execution.output = result.output
    execution.steps_log = list(result.steps)
    execution.iterations = result.iterations
    execution.total_tokens = int(usage.get("total_tokens", 0))
    execution.total_cost_usd = Decimal(str(usage.get("cost_usd", 0)))
    execution.tool_call_count = int(usage.get("tool_calls", 0))
    execution.model_call_count = int(usage.get("model_calls", 0))
    # Only a terminal status completes the run — a run parked in
    # `awaiting_human_approval` has not finished (task_02_33).
    terminal = {ExecutionStatus.DONE, ExecutionStatus.ABORTED, ExecutionStatus.FAILED}
    execution.completed_at = datetime.now(UTC) if result.status in terminal else None
    await session.flush()
    return execution


async def get_execution(session: AsyncSession, execution_id: UUID) -> Execution | None:
    """Load one execution by id (None if absent or RLS-filtered out)."""
    result = await session.execute(select(Execution).where(Execution.id == execution_id))
    return result.scalar_one_or_none()


async def list_executions_for_task(session: AsyncSession, task_id: UUID) -> list[Execution]:
    """All executions of a task, oldest first (a task may be retried)."""
    result = await session.execute(
        select(Execution).where(Execution.task_id == task_id).order_by(Execution.created_at)
    )
    return list(result.scalars().all())
