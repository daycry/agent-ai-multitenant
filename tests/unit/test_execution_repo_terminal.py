"""Unit tests for execution_repo terminal-status helpers + idempotency guard.

Covers three persistence fixes (cluster C7/C4) without touching a database:

* F45 — `record_execution` / `finalize_execution` agree via the pure
  `is_terminal_execution_status` helper, so `awaiting_human_approval` leaves
  `completed_at` NULL in BOTH (a parked run has not finished).
* F46 — `finalize_execution` is idempotent: a second finalize on an
  already-sealed terminal row does not rewrite the outcome/usage or re-seal
  `completed_at` (at most it folds a richer steps_log).
* F52 — a row closed as FAILED/`superseded` by `supersede_running_executions`
  is preserved by a late finalize, like `cancelled`.

The DB-backed paths are exercised by the integration suite; here we mock
`get_execution` + `snapshot_execution_prices` so the logic is tested in
isolation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from api_server.db import execution_repo
from api_server.db.domain import ExecutionStatus
from api_server.db.execution_repo import (
    finalize_execution,
    is_terminal_execution_status,
    record_execution,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Pure helper — is_terminal_execution_status
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "status",
    [
        ExecutionStatus.DONE,
        ExecutionStatus.ABORTED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.NEEDS_HUMAN_REVIEW,
        "done",
        "aborted",
        "failed",
        "cancelled",
        "needs_human_review",
    ],
)
def test_terminal_statuses_are_terminal(status: str) -> None:
    assert is_terminal_execution_status(status) is True


@pytest.mark.parametrize(
    "status",
    [
        ExecutionStatus.RUNNING,
        ExecutionStatus.AWAITING_HUMAN_APPROVAL,
        "running",
        "awaiting_human_approval",
        "",
        "unknown",
        None,
    ],
)
def test_non_terminal_statuses_are_not_terminal(status: str | None) -> None:
    assert is_terminal_execution_status(status) is False


# ---------------------------------------------------------------------------
# Lightweight fakes (no DB)
# ---------------------------------------------------------------------------
class _FakeSession:
    """Minimal AsyncSession stand-in: records add()/flush() calls."""

    def __init__(self) -> None:
        self.flush_count = 0
        self.added: list[Any] = []

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        self.flush_count += 1


class _FakeExecution:
    """A duck-typed `Execution` row that accepts arbitrary attribute writes."""

    def __init__(
        self,
        *,
        status: str,
        completed_at: datetime | None,
        steps_log: list[dict[str, Any]] | None = None,
        abort_code: str | None = None,
        output: str | None = "original output",
    ) -> None:
        self.status = status
        self.completed_at = completed_at
        self.steps_log = steps_log if steps_log is not None else []
        self.abort_code = abort_code
        self.output = output
        self.finish_status: str | None = None
        self.iterations = 7
        self.total_tokens = 999
        self.tool_call_count = 9
        self.model_call_count = 9
        self.price_snapshot_at: datetime | None = None


class _FakeResult:
    """A duck-typed `ExecutionResultLike`."""

    def __init__(
        self,
        *,
        status: str,
        abort_code: str | None = None,
        output: str | None = "new output",
        finish_status: str | None = "success",
        steps: list[dict[str, Any]] | None = None,
        usage: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.abort_code = abort_code
        self.output = output
        self.finish_status = finish_status
        self.iterations = 3
        self.steps = steps if steps is not None else []
        self.usage = usage or {
            "total_tokens": 42,
            "cost_usd": 0.5,
            "tool_calls": 1,
            "model_calls": 1,
        }


def _patch(monkeypatch: pytest.MonkeyPatch, execution: _FakeExecution | None) -> None:
    """Mock get_execution (returns `execution`) + a no-op price snapshot."""

    async def _fake_get_execution(_session: Any, _id: Any) -> _FakeExecution | None:
        return execution

    async def _fake_snapshot(
        _session: Any, *, steps: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], None]:
        return list(steps), None

    monkeypatch.setattr(execution_repo, "get_execution", _fake_get_execution)
    monkeypatch.setattr(execution_repo, "snapshot_execution_prices", _fake_snapshot)


# ---------------------------------------------------------------------------
# F46 — idempotency guard on an already-sealed terminal row
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "current_status",
    [
        ExecutionStatus.DONE,
        ExecutionStatus.FAILED,
        ExecutionStatus.ABORTED,
        ExecutionStatus.NEEDS_HUMAN_REVIEW,
    ],
)
async def test_finalize_is_noop_on_sealed_terminal_row(
    monkeypatch: pytest.MonkeyPatch, current_status: str
) -> None:
    sealed_at = datetime(2026, 1, 1, tzinfo=UTC)
    execution = _FakeExecution(status=current_status, completed_at=sealed_at, output="kept")
    _patch(monkeypatch, execution)
    # A late finalize trying to flip the outcome must be ignored.
    result = _FakeResult(status=ExecutionStatus.DONE, output="LATE overwrite")

    out = await finalize_execution(_FakeSession(), uuid4(), result=result)

    assert out is execution
    assert execution.status == current_status  # not reverted
    assert execution.output == "kept"  # outcome preserved
    assert execution.completed_at is sealed_at  # not re-sealed
    assert execution.total_tokens == 999  # usage not rewritten


@pytest.mark.asyncio
async def test_finalize_preserves_cancelled_row(monkeypatch: pytest.MonkeyPatch) -> None:
    sealed_at = datetime(2026, 1, 1, tzinfo=UTC)
    execution = _FakeExecution(
        status=ExecutionStatus.CANCELLED,
        completed_at=sealed_at,
        abort_code="cancelled",
        output="cancelled by operator",
    )
    _patch(monkeypatch, execution)
    result = _FakeResult(status=ExecutionStatus.DONE, output="late worker finish")

    await finalize_execution(_FakeSession(), uuid4(), result=result)

    assert execution.status == ExecutionStatus.CANCELLED
    assert execution.abort_code == "cancelled"
    assert execution.completed_at is sealed_at


# ---------------------------------------------------------------------------
# M2 — un único primitivo de sellado terminal idempotente (seal_terminal_execution)
# ---------------------------------------------------------------------------
def test_seal_terminal_execution_seals_running_row() -> None:
    from api_server.db.execution_repo import seal_terminal_execution

    now = datetime(2026, 3, 1, tzinfo=UTC)
    execution = _FakeExecution(status=ExecutionStatus.RUNNING, completed_at=None, output="live")

    changed = seal_terminal_execution(
        execution,  # type: ignore[arg-type]
        status=ExecutionStatus.FAILED.value,
        abort_code="stale_after_worker_loss",
        output="worker lost",
        now=now,
    )

    assert changed is True
    assert execution.status == ExecutionStatus.FAILED.value
    assert execution.abort_code == "stale_after_worker_loss"
    assert execution.output == "worker lost"
    assert execution.completed_at is now


def test_seal_terminal_execution_noop_on_sealed_row() -> None:
    """Guarda F46/F52: una fila ya terminal + completed_at NO se revierte."""
    from api_server.db.execution_repo import seal_terminal_execution

    sealed_at = datetime(2026, 1, 1, tzinfo=UTC)
    execution = _FakeExecution(status=ExecutionStatus.DONE, completed_at=sealed_at, output="kept")

    changed = seal_terminal_execution(
        execution,  # type: ignore[arg-type]
        status=ExecutionStatus.FAILED.value,
        abort_code="late overwrite",
        output="LATE",
        now=datetime(2026, 6, 1, tzinfo=UTC),
    )

    assert changed is False
    assert execution.status == ExecutionStatus.DONE  # no revertido
    assert execution.output == "kept"
    assert execution.completed_at is sealed_at  # no re-sellado


def test_seal_terminal_execution_leaves_output_when_not_passed() -> None:
    """output solo se escribe si se pasa (supersede lo pasa explícito)."""
    from api_server.db.execution_repo import seal_terminal_execution

    execution = _FakeExecution(status=ExecutionStatus.RUNNING, completed_at=None, output="prev")

    seal_terminal_execution(
        execution,  # type: ignore[arg-type]
        status=ExecutionStatus.CANCELLED.value,
        abort_code="cancelled",
    )

    assert execution.status == ExecutionStatus.CANCELLED.value
    assert execution.output == "prev"  # intacto


@pytest.mark.asyncio
async def test_finalize_preserves_superseded_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """F52: a FAILED/superseded row is not reverted to done/failed by a late finalize."""
    sealed_at = datetime(2026, 1, 1, tzinfo=UTC)
    execution = _FakeExecution(
        status=ExecutionStatus.FAILED,
        completed_at=sealed_at,
        abort_code="superseded",
        output="superseded by a re-delivered execution (worker retry)",
    )
    _patch(monkeypatch, execution)
    result = _FakeResult(status=ExecutionStatus.DONE, abort_code=None, output="late delivery")

    await finalize_execution(_FakeSession(), uuid4(), result=result)

    assert execution.status == ExecutionStatus.FAILED
    assert execution.abort_code == "superseded"  # not cleared
    assert execution.output.startswith("superseded")
    assert execution.completed_at is sealed_at


@pytest.mark.asyncio
async def test_finalize_folds_richer_steps_log_on_sealed_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sealed_at = datetime(2026, 1, 1, tzinfo=UTC)
    execution = _FakeExecution(
        status=ExecutionStatus.DONE,
        completed_at=sealed_at,
        steps_log=[{"kind": "model_call"}],
    )
    _patch(monkeypatch, execution)
    richer = [{"kind": "model_call"}, {"kind": "tool_call"}, {"kind": "finish"}]
    result = _FakeResult(status=ExecutionStatus.DONE, steps=richer)

    await finalize_execution(_FakeSession(), uuid4(), result=result)

    # Audit trail enriched, but the outcome/seal untouched.
    assert execution.steps_log == richer
    assert execution.status == ExecutionStatus.DONE
    assert execution.completed_at is sealed_at


@pytest.mark.asyncio
async def test_finalize_does_not_shrink_steps_log_on_sealed_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sealed_at = datetime(2026, 1, 1, tzinfo=UTC)
    original = [{"kind": "model_call"}, {"kind": "tool_call"}]
    execution = _FakeExecution(
        status=ExecutionStatus.DONE, completed_at=sealed_at, steps_log=original
    )
    _patch(monkeypatch, execution)
    result = _FakeResult(status=ExecutionStatus.DONE, steps=[{"kind": "finish"}])

    await finalize_execution(_FakeSession(), uuid4(), result=result)

    assert execution.steps_log == original  # shorter incoming ignored


# ---------------------------------------------------------------------------
# finalize_execution — normal (not-yet-sealed) paths
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_finalize_writes_outcome_and_seals_running_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution = _FakeExecution(status=ExecutionStatus.RUNNING, completed_at=None)
    _patch(monkeypatch, execution)
    result = _FakeResult(status=ExecutionStatus.DONE, output="done!", finish_status="success")

    await finalize_execution(_FakeSession(), uuid4(), result=result)

    assert execution.status == ExecutionStatus.DONE
    assert execution.output == "done!"
    assert execution.finish_status == "success"
    assert execution.completed_at is not None  # terminal → sealed


@pytest.mark.asyncio
async def test_finalize_does_not_seal_awaiting_human_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """F45: a run parked in awaiting_human_approval leaves completed_at NULL."""
    execution = _FakeExecution(status=ExecutionStatus.RUNNING, completed_at=None)
    _patch(monkeypatch, execution)
    result = _FakeResult(status=ExecutionStatus.AWAITING_HUMAN_APPROVAL, output=None)

    await finalize_execution(_FakeSession(), uuid4(), result=result)

    assert execution.status == ExecutionStatus.AWAITING_HUMAN_APPROVAL
    assert execution.completed_at is None


@pytest.mark.asyncio
async def test_finalize_returns_none_when_row_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, None)
    result = _FakeResult(status=ExecutionStatus.DONE)
    assert await finalize_execution(_FakeSession(), uuid4(), result=result) is None


# ---------------------------------------------------------------------------
# F45 — record_execution seals completed_at only for terminal statuses
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "expect_sealed"),
    [
        (ExecutionStatus.DONE, True),
        (ExecutionStatus.FAILED, True),
        (ExecutionStatus.CANCELLED, True),
        (ExecutionStatus.NEEDS_HUMAN_REVIEW, True),
        (ExecutionStatus.RUNNING, False),
        (ExecutionStatus.AWAITING_HUMAN_APPROVAL, False),
    ],
)
async def test_record_execution_completed_at_matches_terminality(
    monkeypatch: pytest.MonkeyPatch, status: str, expect_sealed: bool
) -> None:
    async def _fake_snapshot(
        _session: Any, *, steps: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], None]:
        return list(steps), None

    monkeypatch.setattr(execution_repo, "snapshot_execution_prices", _fake_snapshot)
    result = _FakeResult(status=status)

    execution = await record_execution(
        _FakeSession(),
        tenant_id=uuid4(),
        task_id=uuid4(),
        result=result,
    )

    assert (execution.completed_at is not None) is expect_sealed
