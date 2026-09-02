"""Un commit/rebase que falla bloquea la tarea; el PR no calla trabajo perdido.

Auditoría 2026-09-01 (A-06, C-04), `task_cv_11`. La transición a `in_review` se
persiste en la fase 4 (finalize) y el commit del worktree ocurre DESPUÉS, en el
post-proceso. Si el commit o el rebase fallan, sólo se marcaba la ejecución
(`abort_code`) y la tarea seguía `in_review`: el reviewer revisaba un worktree
cuyo trabajo no está en la rama del plan, podía aprobar a `done`, y el siguiente
`sync_to_head` lo borraba. Tres capas:

  1. el marcador de commit fallido mueve la tarea `in_review → blocked` en la
     MISMA transacción, y ese evento sustituye al `in_review` pendiente;
  2. `commit_failed` es escalable en el panel (como ya lo era `rebase_conflict`);
  3. `open_plan_pr` no abre el PR si una tarea `done` cuyo último run acabó en
     `commit_failed`/`rebase_conflict` no tiene ningún commit `Task-Id` en la rama.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from api_server.db.domain import Task, TaskStatus
from workers import execution as exec_mod

pytestmark = pytest.mark.unit


# ------------------------------------------------------------------ capa 1


class _Txn:
    async def __aenter__(self) -> _Txn:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class _Session:
    def __init__(self, execution: Any, task: Any) -> None:
        self._execution = execution
        self._task = task
        self.added: list[Any] = []

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    def begin(self) -> _Txn:
        return _Txn()

    async def get(self, model: Any, ident: Any, **_kw: Any) -> Any:
        if model is Task:
            return self._task
        return self._execution

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def flush(self) -> None:
        return None


def _execution() -> SimpleNamespace:
    return SimpleNamespace(id=uuid4(), abort_code=None, output="hecho", steps_log=[])


def _task(status: str = TaskStatus.IN_REVIEW.value) -> Task:
    task = Task(
        id=uuid4(),
        tenant_id=uuid4(),
        project_id=uuid4(),
        title="t",
        status=status,
        priority="medium",
    )
    task.retry_count = 0
    task.max_retries = 3
    return task


@pytest.mark.asyncio
async def test_el_marcador_bloquea_la_tarea_en_review_y_devuelve_el_evento(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execution, task = _execution(), _task()
    session = _Session(execution, task)

    async def _audit(_session: Any, **kw: Any) -> None:
        session.added.append(kw)

    monkeypatch.setattr(exec_mod, "get_execution", lambda _s, _id: _awaitable(execution))
    import api_server.db.task_audit_repo as audit_repo

    monkeypatch.setattr(audit_repo, "append_audit_event", _audit)

    event = await exec_mod._mark_commit_failed(
        lambda: session,
        execution.id,
        "commit_failed",
        task_id=task.id,
        tenant_id=task.tenant_id,
    )

    assert execution.abort_code == "commit_failed"
    assert task.status == TaskStatus.BLOCKED.value
    assert event == (task, TaskStatus.IN_REVIEW.value, TaskStatus.BLOCKED.value)
    assert any(a.get("payload", {}).get("abort_code") == "commit_failed" for a in session.added)


@pytest.mark.asyncio
async def test_una_tarea_que_ya_no_esta_en_review_no_se_toca(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otro camino ganó (un humano la movió): se marca la ejecución y nada más."""
    execution, task = _execution(), _task(status=TaskStatus.DONE.value)
    session = _Session(execution, task)
    monkeypatch.setattr(exec_mod, "get_execution", lambda _s, _id: _awaitable(execution))

    event = await exec_mod._mark_commit_failed(
        lambda: session,
        execution.id,
        "rebase_conflict",
        task_id=task.id,
        tenant_id=task.tenant_id,
    )

    assert execution.abort_code == "rebase_conflict"
    assert task.status == TaskStatus.DONE.value
    assert event is None


async def _awaitable(value: Any) -> Any:
    return value


@pytest.mark.asyncio
async def test_el_post_proceso_devuelve_el_evento_de_bloqueo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El evento `in_review → blocked` sustituye al `in_review` pendiente de publicar."""
    from workers import orchestration_drain

    sentinel = ("task", "in_review", "blocked")

    async def _commit(*_a: Any, **_k: Any) -> tuple[str, None]:
        return ("commit_failed", None)

    async def _mark(*_a: Any, **_k: Any) -> tuple[str, str, str]:
        return sentinel

    async def _tests(*_a: Any, **_k: Any) -> None:
        return None

    async def _drain(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(exec_mod, "_commit_and_push_worktree", _commit)
    monkeypatch.setattr(exec_mod, "_mark_commit_failed", _mark)
    monkeypatch.setattr(exec_mod, "_run_task_tests", _tests)
    monkeypatch.setattr(orchestration_drain, "drain_task_comment_effects", _drain)

    prepared = SimpleNamespace(
        execution_id=uuid4(),
        worktree_inputs=("t", "p", str(uuid4()), str(uuid4()), "plan"),
        task_acceptance_criteria=[],
    )
    override = await exec_mod._implementer_post_process(
        SimpleNamespace(),
        lambda: None,
        prepared=prepared,  # type: ignore[arg-type]
        workspace=SimpleNamespace(host_path="/data/wt", read_only=False),  # type: ignore[arg-type]
        result=SimpleNamespace(status="done", output="", steps=[]),  # type: ignore[arg-type]
        task_id=uuid4(),
        tenant_id=uuid4(),
        exec_id="e",
        check_declarations=[],
    )
    assert override == sentinel


# ------------------------------------------------------------------ capa 2


def test_commit_failed_es_escalable_en_el_panel() -> None:
    from api_server.routers.plans import _REVIEW_ESCALATION_ABORT_CODES

    assert "commit_failed" in _REVIEW_ESCALATION_ABORT_CODES
    assert "rebase_conflict" in _REVIEW_ESCALATION_ABORT_CODES


# ------------------------------------------------------------------ capa 3


def test_solo_las_done_con_commit_fallido_y_sin_commit_en_la_rama_paran_el_pr() -> None:
    from workers.plan_pr import _done_tasks_without_commits

    a, b, c, d = (uuid4() for _ in range(4))
    done_tasks: list[tuple[UUID, str | None]] = [
        (a, "commit_failed"),  # sin commit → para el PR
        (b, "rebase_conflict"),  # con commit (alguien lo resolvió) → no para
        (c, None),  # tarea de diseño: nunca tuvo commit y no lo necesita
        (d, "commit_failed"),  # sin commit → para el PR
    ]
    on_branch = {b}
    missing = _done_tasks_without_commits(done_tasks, has_commit=lambda tid: tid in on_branch)
    assert missing == [a, d]


def test_sin_tareas_perdidas_el_pr_sigue_su_curso() -> None:
    from workers.plan_pr import _done_tasks_without_commits

    assert _done_tasks_without_commits([(uuid4(), None)], has_commit=lambda _t: False) == []
