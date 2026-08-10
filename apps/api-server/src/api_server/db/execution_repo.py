"""Persistence for agent loop executions (task_02_11 / task_02_12).

The agent loop (`agent_runtime`) produces an execution result; this
module writes it to the `executions` table and reads it back. The loop
result is duck-typed via `ExecutionResultLike` — api-server does not
import `agent_runtime` (the runtime is a separate, container-side
package; the steps_log is opaque JSONB here).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api_server.db.domain import (
    ApprovalRequest,
    ApprovalRequestStatus,
    Execution,
    ExecutionStatus,
    Task,
    TaskStatus,
)
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

# The set of execution statuses that mean "the run has finished" — these are the
# only ones that seal `completed_at`. `running` is live; `awaiting_human_approval`
# is parked mid-run (a human_approval_policy decision is pending) and has NOT
# finished, so it stays uncompleted until the run resumes and reaches a terminal
# state (task_02_33 / ADR 0087). Kept as a single source of truth so
# `record_execution` and `finalize_execution` agree (F45).
_TERMINAL_EXECUTION_STATUSES: frozenset[str] = frozenset(
    {
        ExecutionStatus.DONE,
        ExecutionStatus.ABORTED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        # ADR 0087: escalated-to-human is terminal for the RUN (a human takes over).
        ExecutionStatus.NEEDS_HUMAN_REVIEW,
    }
)


def is_terminal_execution_status(status: str | None) -> bool:
    """True when `status` is a terminal execution state (the run has finished).

    Terminal = ``done`` / ``aborted`` / ``failed`` / ``cancelled`` /
    ``needs_human_review``. NOT terminal: ``running`` (live) and
    ``awaiting_human_approval`` (parked mid-run). Accepts the raw string or an
    ``ExecutionStatus`` (a ``StrEnum``, so membership works for either); ``None``
    is not terminal.
    """
    return status in _TERMINAL_EXECUTION_STATUSES


class ExecutionResultLike(Protocol):
    """The shape of an `agent_runtime.ExecutionResult` — read-only."""

    status: str
    abort_code: str | None
    output: str | None
    iterations: int
    steps: list[dict[str, Any]]
    usage: dict[str, Any]
    # ADR 0087: the agent's structured finish status (success|failed|partial) or
    # None. Optional on the Protocol so older/partial result shapes still satisfy it.
    finish_status: str | None


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


def _catalog_cost_total(steps: list[dict[str, Any]]) -> Decimal | None:
    """Sum of the frozen catalog cost of every PRICED `model_call` step.

    ``None`` when not a single call could be priced — which is *not* the same
    as ``Decimal(0)``: "the catalog does not know this model" must never become
    a bill. Only snapshots with ``available=True`` count; an unknown price is
    recorded as ``available=False`` with ``cost_usd=None`` (see
    ``price_snapshot``), and a free call (a real 0) still sums as 0.
    """
    total = Decimal(0)
    priced = False
    for step in steps:
        snapshot = step.get("price_snapshot")
        if not isinstance(snapshot, dict) or snapshot.get("available") is not True:
            continue
        raw = snapshot.get("cost_usd")
        if raw is None:
            continue
        try:
            total += Decimal(str(raw))
        except (ArithmeticError, ValueError):  # pragma: no cover - defensive
            continue
        priced = True
    return total if priced else None


def _billable_cost_usd(usage: dict[str, Any], steps: list[dict[str, Any]]) -> Decimal:
    """The cost that lands on ``executions.total_cost_usd`` (prod-07 llm-1).

    Precedence, in this order and no other:

    1. **What the runtime reported.** ``claude_sdk`` is the only kind that
       reports a real per-call cost today; that figure is what the provider
       actually charged and an estimate must never overwrite it.
    2. **The sum of the per-call catalog snapshots**, when the runtime reported
       0. The three OpenAI-compatible kinds (ollama, copilot, azure_foundry)
       never populate ``usage.cost`` — `_openai_compat` can only pass through
       what the endpoint sends — so that 0 was being persisted verbatim and the
       budgets summed $0 for three of the four providers of the closed catalog.
    3. **0**, when the catalog could not price a single call. An unknown price
       stays unknown.

    Why an override and not a new ``cost_estimated_usd`` column: the
    provenance the column would have carried is already per call in
    ``steps_log`` — the runtime's raw ``cost_usd`` stays beside a
    ``price_snapshot`` that names its ``source`` and ``price_id``. A column
    would duplicate that at the price of a schema migration, and every reader
    of the billable figure (budgets, dashboards, the plan cost breakdown)
    would have to learn to coalesce or keep under-counting.
    """
    reported = Decimal(str(usage.get("cost_usd", 0) or 0))
    if reported > 0:
        return reported
    estimated = _catalog_cost_total(steps)
    if estimated is None or estimated <= 0:
        return reported
    return estimated


@dataclass(frozen=True)
class StepsRollup:
    """La proyección de `steps_log` en las tres columnas de la migración 0139."""

    last_model: str | None
    tokens_in: int
    tokens_out: int


def steps_rollup(steps: Sequence[Any]) -> StepsRollup:
    """Denormaliza `steps_log` (prod-13 task_prod13_18).

    Réplica EXACTA, en Python, de las dos expresiones SQL que el explorador de
    runs usaba para no tener que expandir el JSONB por fila:

    * ``last_model`` ← el ``model`` del paso ``model_call`` de mayor ``index``
      que declare modelo (``NULL`` si el run no llamó a ninguno). Se ordena por
      el ``index`` del propio paso y no por la posición, igual que hacía
      ``tenant_stats._last_model_expr``; sin ``index`` se usa la posición.
    * ``tokens_in`` / ``tokens_out`` ← la suma sobre los pasos ``model_call``,
      con ``0`` cuando no hay ninguno (el ``coalesce`` de ``_token_split``).

    **Nunca levanta.** Un `steps_log` viejo o mal formado no puede hacer fallar
    el cierre de un run: convertiría un dato de panel en trabajo perdido. Los
    valores no numéricos cuentan como 0 y las entradas no-dict se ignoran, que
    es lo que el ``(el->>'tokens_in')::bigint`` de PostgreSQL hace con un NULL.
    """
    last_model: str | None = None
    best_order: int | None = None
    tokens_in = 0
    tokens_out = 0
    for position, step in enumerate(steps):
        if not isinstance(step, dict) or step.get("kind") != "model_call":
            continue
        tokens_in += _as_int(step.get("tokens_in"))
        tokens_out += _as_int(step.get("tokens_out"))
        model = step.get("model")
        if not isinstance(model, str) or not model:
            continue
        raw_index: Any = step.get("index")
        usable = isinstance(raw_index, int) and not isinstance(raw_index, bool)
        order: int = raw_index if usable else position
        if best_order is None or order >= best_order:
            best_order, last_model = order, model
    return StepsRollup(last_model=last_model, tokens_in=tokens_in, tokens_out=tokens_out)


def _as_int(value: Any) -> int:
    """El valor como entero, o 0. Ver el «nunca levanta» de :func:`steps_rollup`."""
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def apply_steps_rollup(execution: Execution, steps: Sequence[Any]) -> None:
    """Escribe la proyección de `steps_log` sobre las columnas de la ejecución.

    **Pública a propósito, y es contrato**: quien asigne ``execution.steps_log``
    —desde donde sea— tiene que llamar a esto acto seguido. Es lo único que
    impide que la proyección y su fuente se separen, y no hay ningún mecanismo
    en la BD (ni trigger ni columna generada) que lo haga por su cuenta: si un
    escritor la olvida, `last_model` / `tokens_in` / `tokens_out` describen un
    `steps_log` que ya no existe y el panel enseña cifras falsas SIN que nada
    falle, que es exactamente el modo de fallo que estas columnas tenían que
    evitar.

    Hoy hay dos llamantes: este repositorio (``record_execution`` /
    ``finalize_execution``) y ``workers.execution._mark_commit_failed``, que
    anexa el paso estructurado del conflicto de rebase en su propia sesión
    BYPASSRLS. El segundo existe porque la invariante de «un solo escritor» que
    el diseño invocaba era falsa; se hizo verdadera la garantía en vez de
    rebajar el texto (revisión adversarial del 2026-08-12).
    """
    rollup = steps_rollup(steps)
    execution.last_model = rollup.last_model
    execution.tokens_in = rollup.tokens_in
    execution.tokens_out = rollup.tokens_out


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
        total_cost_usd=_billable_cost_usd(usage, steps),
        tool_call_count=int(usage.get("tool_calls", 0)),
        model_call_count=int(usage.get("model_calls", 0)),
        started_at=started_at,
        # Only a terminal status (the run has finished) gets a completion stamp;
        # `awaiting_human_approval` is parked mid-run and stays uncompleted (F45).
        completed_at=(datetime.now(UTC) if is_terminal_execution_status(result.status) else None),
    )
    _apply_price_snapshot(execution, rollup)
    apply_steps_rollup(execution, steps)
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
    # Aquí el rollup de un log vacío coincide con los `server_default` de las tres
    # columnas (NULL/0/0), así que llamarlo no cambia ni una fila. Se llama igual
    # porque la regla «quien asigna `steps_log` llama a `apply_steps_rollup`» solo
    # sirve si no tiene excepciones: una excepción «inocua» es lo que hay que
    # recordar al añadir el cuarto escritor, y nadie lo recuerda.
    apply_steps_rollup(execution, [])
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


async def cancel_running_executions_for_task(
    session: AsyncSession, task_id: UUID
) -> list[Execution]:
    """Cancel every non-terminal execution of a task (prod-06 cancel_01/cancel_02).

    Two cases, because a run can be parked without a live worker:

      * ``running`` — a worker/container is (or was) live: seal only
        ``cancel_requested_at``; the worker polls it, kills the container and
        finalises the row as ``cancelled``.
      * ``awaiting_human_approval`` — the run is parked mid-run with its container
        already gone; NO worker will ever finalise it (the reaper/reconciler both
        skip this state). Seal it terminally IN LINE (``cancelled`` + ``completed_at``)
        and close its ``pending`` ApprovalRequest(s) so they leave the inbox and a
        later ``resolve`` can't resurrect the cancelled task (CANCELAWAIT).

    Returns every affected execution — with its ``celery_task_id`` — so the caller
    can ``revoke`` the queued jobs (a no-op on an already-sealed row). Idempotent;
    the caller owns the transaction.
    """
    rows = (
        (
            await session.execute(
                select(Execution).where(
                    Execution.task_id == task_id,
                    Execution.status.in_(
                        [
                            ExecutionStatus.RUNNING.value,
                            ExecutionStatus.AWAITING_HUMAN_APPROVAL.value,
                        ]
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    now = datetime.now(UTC)
    cancelled: list[Execution] = []
    parked_exec_ids: list[UUID] = []
    for execution in rows:
        execution.cancel_requested_at = execution.cancel_requested_at or now
        if execution.status == ExecutionStatus.AWAITING_HUMAN_APPROVAL.value:
            # No worker owns this parked run — seal it here so it doesn't hang forever.
            execution.status = ExecutionStatus.CANCELLED.value
            execution.abort_code = "cancelled"
            execution.completed_at = now
            parked_exec_ids.append(execution.id)
        cancelled.append(execution)
    if parked_exec_ids:
        # Close the pending approval requests of the parked runs (tenant-scoped via
        # the execution_id, which belongs to this task) so they leave the inbox.
        pending = (
            (
                await session.execute(
                    select(ApprovalRequest).where(
                        ApprovalRequest.execution_id.in_(parked_exec_ids),
                        ApprovalRequest.status == ApprovalRequestStatus.PENDING.value,
                    )
                )
            )
            .scalars()
            .all()
        )
        for request in pending:
            request.status = ApprovalRequestStatus.CANCELLED.value
            request.resolved_at = now
            request.reason = "task cancelled"
    if cancelled:
        await session.flush()
    return cancelled


async def cancel_tasks_and_executions(
    session: AsyncSession,
    *,
    plan_id: UUID | None = None,
    project_id: UUID | None = None,
) -> list[Execution]:
    """Cancel every NON-terminal task of a plan OR a project and request
    cancellation of their running executions (prod-06 cancel_02).

    Used by the plan-level cancellation (``PUT /plans/{id}`` → ``cancelled``) and
    the project soft-delete cascade — neither cancelled in-flight work before.
    Returns the cancelled executions (with ``celery_task_id``) so the caller can
    revoke the queued jobs. Pass exactly one of ``plan_id``/``project_id``.
    Idempotent; the caller owns the transaction.
    """
    if (plan_id is None) == (project_id is None):
        raise ValueError("pass exactly one of plan_id / project_id")
    scope = Task.plan_id == plan_id if plan_id is not None else Task.project_id == project_id
    tasks = (
        (
            await session.execute(
                select(Task).where(
                    scope,
                    Task.status.notin_([TaskStatus.DONE.value, TaskStatus.CANCELLED.value]),
                )
            )
        )
        .scalars()
        .all()
    )
    cancelled_execs: list[Execution] = []
    for task in tasks:
        task.status = TaskStatus.CANCELLED.value
        cancelled_execs.extend(await cancel_running_executions_for_task(session, task.id))
    if tasks:
        await session.flush()
    return cancelled_execs


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
    from api_server.db.task_audit_repo import append_audit_event

    now = datetime.now(UTC)
    for execution in stale:
        # A row already flagged for cancellation that gets re-delivered (revoke +
        # task_acks_late) must close as CANCELLED, not FAILED/superseded — otherwise
        # the supersede would mask the operator's explicit cancel.
        if execution.cancel_requested_at is not None:
            sealed = seal_terminal_execution(
                execution,
                status=ExecutionStatus.CANCELLED.value,
                abort_code="cancelled",
                output="cancelled by operator",
                now=now,
            )
        else:
            sealed = seal_terminal_execution(
                execution,
                status=ExecutionStatus.FAILED.value,
                abort_code="superseded",
                output="superseded by a re-delivered execution (worker retry)",
                now=now,
            )
        # AUD16-21: la cronología de una task debe ser reconstruible desde BD —
        # cada sello por re-entrega deja su rastro con actor y motivo.
        if sealed:
            await append_audit_event(
                session,
                tenant_id=tenant_id,
                task_id=task_id,
                kind="execution_superseded",
                actor="system:redelivery_guard",
                payload={
                    "execution_id": str(execution.id),
                    "abort_code": execution.abort_code,
                },
            )
    await session.flush()
    return len(stale)


def seal_terminal_execution(
    execution: Execution,
    *,
    status: str,
    abort_code: str | None = None,
    output: str | None = None,
    now: datetime | None = None,
) -> bool:
    """Seal a `running` execution row into a terminal state, idempotently (M2).

    The single primitive the non-`finalize` close-out paths funnel through
    (``supersede_running_executions``, the zombie sweeper, the soft-timeout
    finalizer) instead of hand-writing ``status`` + ``abort_code`` + ``output`` +
    ``completed_at`` four times. Reuses ``finalize_execution``'s F46/F52 guard: a
    row already sealed (terminal status + non-NULL ``completed_at``) is left
    untouched and ``False`` is returned — a re-delivery/race can no longer stomp a
    freshly-sealed outcome. ``output`` is written only when passed (so a caller can
    seal status without clobbering the existing output). Returns ``True`` iff it
    sealed the row. Pure (no I/O); the caller owns the session/flush.
    """
    if is_terminal_execution_status(execution.status) and execution.completed_at is not None:
        return False
    execution.status = status
    execution.abort_code = abort_code
    if output is not None:
        execution.output = output
    execution.completed_at = now or datetime.now(UTC)
    # AUD16-21: un cierre administrativo no pasa por el memorizer — sellar el
    # motivo canónico (si nadie lo puso antes) para que la UI pueda explicar
    # por qué este run no dejó memoria, en vez de un NULL indistinguible de
    # un bug del trigger.
    if execution.memorize_skip_reason is None:
        execution.memorize_skip_reason = "administrative_finalize"
    return True


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

    # F46 / F52: idempotency guard. A row already in a SEALED terminal state
    # (terminal status + a non-NULL `completed_at`) has already been finalised —
    # by a previous finalize (double delivery under task_acks_late), by an
    # operator CANCELLED (preserve_cancel), or by a FAILED/superseded close-out
    # from `supersede_running_executions` (F52). A late/duplicate finalize from
    # the worker must not revert the outcome, recompute the usage roll-ups, or
    # re-seal `completed_at`. We only fold in a richer streamed steps_log for the
    # audit trail (a no-op when the same log is re-delivered).
    if is_terminal_execution_status(execution.status) and execution.completed_at is not None:
        incoming = list(result.steps)
        if len(incoming) > len(execution.steps_log or []):
            steps, _rollup = await snapshot_execution_prices(session, steps=incoming)
            execution.steps_log = steps
            # Y su proyección, porque es eso: una proyección de la columna que
            # se acaba de reemplazar (prod-13 task_prod13_18). Esto NO es
            # recomputar los roll-ups de `usage` que esta guarda protege — a
            # `total_tokens` / `total_cost_usd` no se les toca —; es no dejar
            # que `last_model` describa un `steps_log` que ya no existe.
            apply_steps_rollup(execution, steps)
            await session.flush()
        return execution

    usage = result.usage
    steps, rollup = await snapshot_execution_prices(session, steps=list(result.steps))
    execution.status = result.status
    execution.abort_code = result.abort_code
    execution.output = result.output
    # ADR 0087: persist the structured finish status (None when absent / for
    # older result shapes without the field).
    execution.finish_status = getattr(result, "finish_status", None)
    # `task_wf_52`: la etiqueta del conjunto de prompts que produjo el run. Con
    # `getattr` porque el Protocol admite formas de resultado más viejas — un
    # run de una imagen anterior al versionado simplemente la deja a NULL.
    execution.prompt_version = getattr(result, "prompt_version", None)
    # `task_wf_62`: qué IMAGEN lo produjo. Junto a `prompt_version` cierra la
    # trazabilidad de un run: qué prompts y qué build.
    execution.runtime_image_digest = getattr(result, "runtime_image_digest", None)
    execution.steps_log = steps
    _apply_price_snapshot(execution, rollup)
    apply_steps_rollup(execution, steps)
    execution.iterations = result.iterations
    execution.total_tokens = int(usage.get("total_tokens", 0))
    execution.total_cost_usd = _billable_cost_usd(usage, steps)
    execution.tool_call_count = int(usage.get("tool_calls", 0))
    execution.model_call_count = int(usage.get("model_calls", 0))
    # Only a terminal status completes the run — a run parked in
    # `awaiting_human_approval` has not finished (task_02_33). A cooperative
    # cancel arriving as the new result seals here too (`cancelled` is terminal).
    execution.completed_at = (
        datetime.now(UTC) if is_terminal_execution_status(result.status) else None
    )
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
