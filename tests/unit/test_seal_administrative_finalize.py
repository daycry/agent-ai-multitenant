"""AUD16-21 (auditoría 2026-07-16): los finalizadores ADMINISTRATIVOS dejan
rastro de por qué el run no dejó memoria.

8 executions selladas por reaper/supersede quedaron con
``memorize_skip_reason`` NULL: esos caminos no pasan por el memorizer, así que
la columna que la UI usa para explicar «por qué este run no dejó memoria» no
decía nada. ``seal_terminal_execution`` — el primitivo por el que canalizan
TODOS los cierres administrativos (sweeper, supersede, cancel, soft-timeout) —
sella ahora también el motivo canónico ``administrative_finalize``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from api_server.db.domain import Execution, ExecutionStatus
from api_server.db.execution_repo import seal_terminal_execution
from api_server.memorizer.policy import MemorizeSkipReason

pytestmark = pytest.mark.unit


def _running_execution() -> Execution:
    return Execution(
        id=uuid4(),
        tenant_id=uuid4(),
        task_id=uuid4(),
        status=ExecutionStatus.RUNNING.value,
    )


def test_enum_has_administrative_finalize() -> None:
    assert MemorizeSkipReason.ADMINISTRATIVE_FINALIZE.value == "administrative_finalize"


def test_seal_stamps_memorize_skip_reason() -> None:
    execution = _running_execution()
    sealed = seal_terminal_execution(
        execution,
        status=ExecutionStatus.FAILED.value,
        abort_code="stale_after_worker_loss",
        now=datetime.now(UTC),
    )
    assert sealed is True
    assert execution.memorize_skip_reason == "administrative_finalize"


def test_seal_does_not_clobber_an_existing_skip_reason() -> None:
    execution = _running_execution()
    execution.memorize_skip_reason = "llm_error"
    seal_terminal_execution(execution, status=ExecutionStatus.FAILED.value, abort_code="x")
    assert execution.memorize_skip_reason == "llm_error"


def test_already_sealed_row_is_untouched() -> None:
    execution = _running_execution()
    execution.status = ExecutionStatus.DONE.value
    execution.completed_at = datetime.now(UTC)
    assert (
        seal_terminal_execution(execution, status=ExecutionStatus.FAILED.value, abort_code="x")
        is False
    )
    assert execution.memorize_skip_reason is None
