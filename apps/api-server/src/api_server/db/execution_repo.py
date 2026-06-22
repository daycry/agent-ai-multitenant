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
from api_server.db.price_snapshot import PriceSnapshot, snapshot_model_call

# The step kind that carries an LLM call's tokens + cost (the canonical
# steps_log shape — see agent_runtime/steps.py). Only these steps get a
# price snapshot.
_MODEL_CALL_KIND = "model_call"

# A model call step records its model under `model`; newer producers may
# also carry an explicit `provider` and a cached-input token count under
# any of these aliases. We read whatever is present (the snapshot lookup
# records a typed "unknown" when the key cannot be resolved — never a fake
# price), so an older steps_log shape degrades cleanly rather than crashing.
_CACHED_TOKEN_KEYS = ("cached_input_tokens", "tokens_cached_input", "tokens_cached")


class ExecutionResultLike(Protocol):
    """The shape of an `agent_runtime.ExecutionResult` — read-only."""

    status: str
    abort_code: str | None
    output: str | None
    iterations: int
    steps: list[dict[str, Any]]
    usage: dict[str, Any]


def _int_field(step: dict[str, Any], *names: str, default: int = 0) -> int:
    """First present, int-coercible value among `names`, else `default`."""
    for name in names:
        value = step.get(name)
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                continue
    return default


async def snapshot_execution_prices(
    session: AsyncSession,
    *,
    steps: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], PriceSnapshot | None]:
    """Freeze a catalog price snapshot onto each `model_call` step.

    For every ``model_call`` step in ``steps`` this looks up the current
    catalog price for the call's ``(provider, model_id, modality)`` and
    embeds an immutable ``price_snapshot`` payload (unit prices in effect +
    ``price_snapshot_at`` + a computed ``cost_usd`` for the call, charging
    cached-input tokens at the cached rate). A missing price is recorded as
    a typed *unknown* (``available=False``), never a fake zero.

    Returns ``(enriched_steps, rollup)`` where ``rollup`` is a
    representative snapshot for the execution row's snapshot columns: the
    LAST priced model call (so ``executions.price_snapshot_at`` reflects a
    real lookup and the unit-price columns mirror an actual call), or the
    last *unknown* snapshot when no call could be priced, or ``None`` when
    there were no model calls at all. The catalog (``model_prices``) is
    platform-global with global-read RLS, so the lookup works on a tenant
    session; the snapshot is written onto the tenant-scoped execution.
    """
    enriched: list[dict[str, Any]] = []
    last_priced: PriceSnapshot | None = None
    last_any: PriceSnapshot | None = None

    for step in steps:
        # Copy: never mutate the caller's step dicts.
        enriched_step = dict(step)
        if enriched_step.get("kind") == _MODEL_CALL_KIND:
            model_id = str(enriched_step.get("model_id") or enriched_step.get("model") or "")
            provider = str(enriched_step.get("provider") or "")
            modality = str(enriched_step.get("modality") or "text")
            snapshot = await snapshot_model_call(
                session,
                provider=provider,
                model_id=model_id,
                modality=modality,
                tokens_in=_int_field(enriched_step, "tokens_in"),
                tokens_out=_int_field(enriched_step, "tokens_out"),
                cached_input_tokens=_int_field(enriched_step, *_CACHED_TOKEN_KEYS),
            )
            enriched_step["price_snapshot"] = snapshot.as_step_payload()
            last_any = snapshot
            if snapshot.available:
                last_priced = snapshot
        enriched.append(enriched_step)

    rollup = last_priced or last_any
    return enriched, rollup


def _apply_price_snapshot(execution: Execution, rollup: PriceSnapshot | None) -> None:
    """Write the representative snapshot onto the execution's columns."""
    if rollup is None:
        return
    execution.price_snapshot_at = rollup.price_snapshot_at
    execution.price_snapshot_currency = rollup.currency
    execution.price_input_usd = rollup.input_price
    execution.price_output_usd = rollup.output_price
    execution.price_cached_input_usd = rollup.cached_input_price
    execution.price_snapshot_cost_usd = rollup.cost_usd


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
    caller owns the transaction — this flushes but does not commit. Each
    `model_call` step is enriched with an immutable catalog price snapshot
    (task_11_13) and the execution's snapshot columns are stamped.
    """
    usage = result.usage
    steps, rollup = await snapshot_execution_prices(session, steps=list(result.steps))
    execution = Execution(
        tenant_id=tenant_id,
        task_id=task_id,
        agent_id=agent_id,
        status=result.status,
        abort_code=result.abort_code,
        output=result.output,
        steps_log=steps,
        iterations=result.iterations,
        total_tokens=int(usage.get("total_tokens", 0)),
        total_cost_usd=Decimal(str(usage.get("cost_usd", 0))),
        tool_call_count=int(usage.get("tool_calls", 0)),
        model_call_count=int(usage.get("model_calls", 0)),
        started_at=started_at,
        # A finished run (done/aborted/failed) gets a completion stamp.
        completed_at=None if result.status == ExecutionStatus.RUNNING else datetime.now(UTC),
    )
    _apply_price_snapshot(execution, rollup)
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
    celery_task_id: str | None = None,
) -> Execution:
    """Insert an `executions` row in `running` state and return it.

    The worker (task_02_30) calls this *before* it launches the
    container, so the per-execution Redis stream (`exec:{id}`) has a
    stable id the UI can connect to while the run is still live. The
    row is finalised with `finalize_execution` once the container exits.
    The caller owns the transaction — this flushes but does not commit.

    ``celery_task_id`` (the worker's ``self.request.id``) is stored so the
    cancel endpoint can ``revoke(terminate=True)`` the job; ``None`` for
    callers that don't run under Celery (tests, the orchestrator demo path).
    """
    execution = Execution(
        tenant_id=tenant_id,
        task_id=task_id,
        agent_id=agent_id,
        status=ExecutionStatus.RUNNING,
        steps_log=[],
        started_at=started_at or datetime.now(UTC),
        celery_task_id=celery_task_id,
    )
    session.add(execution)
    await session.flush()
    return execution


class ExecutionNotCancellableError(Exception):
    """Raised when a cancel is requested on an execution that is not ``running``.

    ``status`` is the execution's current (terminal) status, so the REST surface
    can return a focused 409 explaining why it can't be cancelled.
    """

    def __init__(self, status: str) -> None:
        self.status = status
        super().__init__(f"execution is {status!r}, not cancellable")


async def request_execution_cancel(session: AsyncSession, execution_id: UUID) -> Execution | None:
    """Flag a ``running`` execution for cooperative cancellation (idempotent).

    Stamps ``cancel_requested_at`` (the worker polls it to kill the container and
    finalise the row as ``cancelled``). Returns the execution — with its
    ``celery_task_id`` for the caller to ``revoke`` — or ``None`` if the row is
    absent/RLS-filtered. Raises :class:`ExecutionNotCancellable` if the execution
    is already terminal. A second call is a no-op (the timestamp is not bumped).
    The caller owns the transaction.
    """
    execution = await get_execution(session, execution_id)
    if execution is None:
        return None
    if execution.status != ExecutionStatus.RUNNING:
        raise ExecutionNotCancellableError(execution.status)
    if execution.cancel_requested_at is None:
        execution.cancel_requested_at = datetime.now(UTC)
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
        # A row already flagged for cancellation that gets re-delivered (revoke +
        # task_acks_late) must close as CANCELLED, not FAILED/superseded — otherwise
        # the supersede would mask the operator's explicit cancel.
        if execution.cancel_requested_at is not None:
            execution.status = ExecutionStatus.CANCELLED
            execution.abort_code = "cancelled"
            execution.output = "cancelled by operator"
        else:
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

    # A row already CANCELLED by the operator wins: a late finalisation from the
    # (revoked) worker must not revert it to done/failed. Fold in the streamed
    # steps_log/usage for the audit trail but preserve the cancelled outcome.
    preserve_cancel = execution.status == ExecutionStatus.CANCELLED

    usage = result.usage
    steps, rollup = await snapshot_execution_prices(session, steps=list(result.steps))
    if not preserve_cancel:
        execution.status = result.status
        execution.abort_code = result.abort_code
        execution.output = result.output
    execution.steps_log = steps
    _apply_price_snapshot(execution, rollup)
    execution.iterations = result.iterations
    execution.total_tokens = int(usage.get("total_tokens", 0))
    execution.total_cost_usd = Decimal(str(usage.get("cost_usd", 0)))
    execution.tool_call_count = int(usage.get("tool_calls", 0))
    execution.model_call_count = int(usage.get("model_calls", 0))
    # Only a terminal status completes the run — a run parked in
    # `awaiting_human_approval` has not finished (task_02_33). `cancelled` is
    # terminal too, whether it arrives as the new result (cooperative cancel from
    # the worker) or was already on the row (preserve_cancel, late finalisation).
    terminal = {
        ExecutionStatus.DONE,
        ExecutionStatus.ABORTED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
    }
    is_terminal = preserve_cancel or result.status in terminal
    execution.completed_at = datetime.now(UTC) if is_terminal else None
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
