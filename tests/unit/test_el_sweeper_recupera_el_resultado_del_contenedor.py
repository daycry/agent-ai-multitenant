"""El sweeper recupera el resultado real de un contenedor `exited` (`task_cv_12`).

Auditoría 2026-09-01 (A-04). Cuando el proceso worker muere con el contenedor
vivo (OOM, SIGKILL, reinicio del host), el contenedor termina solo y deja su
`execution.finished` en los logs de Docker; la re-entrega de Celery la descarta
el run-lock y, como `list_managed_execution_ids` cuenta un contenedor `exited`
como existente, el sweeper no lo trataba como huérfano: la fila quedaba 7 horas
en `running` y el resultado —tokens ya pagados— se perdía al sellarla como
«worker loss». Ahora, para una fila `running` cuyo contenedor está `exited`, el
sweeper lee los logs y finaliza con el resultado real; si no hay línea terminal,
sella ya, sin esperar 7 horas.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from workers.container import AgentContainerRunner
from workers.maintenance.stale_sweeper import (
    _is_review_run,
    _recover_result_from_exited_logs,
)

pytestmark = pytest.mark.unit


def _finished(status: str = "done", **extra: Any) -> str:
    return json.dumps(
        {
            "event": "execution.finished",
            "result": {
                "status": status,
                "output": "entregado",
                "iterations": 4,
                "usage": {"total_tokens": 1234, "model_calls": 4},
                **extra,
            },
        }
    )


def test_a_finished_line_in_the_logs_is_the_real_result() -> None:
    logs = "\n".join(
        [
            json.dumps({"event": "execution.step", "index": 0, "kind": "node"}),
            "ruido del runtime que no es JSON",
            _finished(),
        ]
    )
    result = _recover_result_from_exited_logs(logs, exit_code=0)
    assert result is not None
    assert (result.status, result.output, result.iterations) == ("done", "entregado", 4)
    assert result.usage["total_tokens"] == 1234


def test_the_last_terminal_line_wins_and_exit_code_does_not_veto_it() -> None:
    """El run emitió su resultado y luego el contenedor murió con 137 (SIGKILL del
    reaper, OOM del host): el resultado ya estaba escrito y es lo que cuenta."""
    logs = "\n".join([_finished("failed"), _finished("done")])
    result = _recover_result_from_exited_logs(logs, exit_code=137)
    assert result is not None and result.status == "done"


def test_an_execution_error_line_is_a_failed_result_with_its_reason() -> None:
    logs = json.dumps({"event": "execution.error", "error": "model provider returned 529"})
    result = _recover_result_from_exited_logs(logs, exit_code=1)
    assert result is not None
    assert result.status == "failed"
    assert "529" in (result.output or "") + (result.abort_code or "")


@pytest.mark.parametrize("logs", ["", 'sin nada terminal\n{"event": "execution.step"}'])
def test_without_a_terminal_line_there_is_nothing_to_recover(logs: str) -> None:
    assert _recover_result_from_exited_logs(logs, exit_code=0) is None


# ------------------------------------------------------------ lectura del contenedor


class _FakeContainer:
    def __init__(self, logs: bytes, exit_code: int) -> None:
        self._logs = logs
        self.attrs = {"State": {"Status": "exited", "ExitCode": exit_code}}

    def logs(self, **_kw: Any) -> bytes:
        return self._logs


class _FakeDocker:
    def __init__(self, containers: dict[str, _FakeContainer]) -> None:
        self.containers = SimpleNamespace(get=lambda cid: containers[cid])


def test_the_runner_reads_logs_and_exit_code_of_an_exited_container() -> None:
    runner = AgentContainerRunner(
        SimpleNamespace(),  # type: ignore[arg-type]
        client=_FakeDocker({"c1": _FakeContainer(_finished().encode() + b"\n", 0)}),
    )
    read = runner.read_exited_container("c1")
    assert read is not None
    logs, exit_code = read
    assert "execution.finished" in logs and exit_code == 0


def test_a_container_that_vanished_reads_as_none() -> None:
    runner = AgentContainerRunner(SimpleNamespace(), client=_FakeDocker({}))  # type: ignore[arg-type]
    assert runner.read_exited_container("desaparecido") is None


# ---------------------------------------------------------------- review o no


def test_the_reviewers_own_run_on_an_in_review_task_is_a_review() -> None:
    reviewer = uuid4()
    assert _is_review_run(
        execution_agent_id=reviewer, task_reviewer_agent_id=reviewer, task_status="in_review"
    )


def test_anything_else_is_an_implementer_run() -> None:
    reviewer, implementer = uuid4(), uuid4()
    assert not _is_review_run(
        execution_agent_id=implementer, task_reviewer_agent_id=reviewer, task_status="in_review"
    )
    assert not _is_review_run(
        execution_agent_id=reviewer, task_reviewer_agent_id=reviewer, task_status="in_progress"
    )
    assert not _is_review_run(
        execution_agent_id=None, task_reviewer_agent_id=None, task_status="in_review"
    )
