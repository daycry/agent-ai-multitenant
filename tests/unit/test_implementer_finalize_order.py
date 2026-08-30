"""El ORDEN del cierre del camino implementador (prod-18).

`commit+push` → `run_test_runtime` → **evento** de transición a `in_review`.

Ese orden no es estético: es el reordenado que mató una carrera real. El
orquestador reacciona al evento `in_review` en milisegundos despachando el review,
y el reviewer lee (a) el diff del worktree y (b) el `<test-report>` que persiste el
test-runtime. Si el evento se publicara antes del commit, el reviewer podría
arrancar sobre un worktree sin commitear y sin informe de tests — juzgando trabajo
que todavía no existe. `conduct_execution` lo documenta en su docstring («el MISMO
comportamiento y orden (prod-18) de siempre») y `_implementer_post_process` también,
pero **ningún test lo fijaba**: un refactor podía reordenar las dos líneas o
adelantar la publicación del evento y toda la suite seguía verde.

Se fija en los dos tramos donde vive el orden, con espías sobre una lista
compartida:

  1. dentro de `_implementer_post_process`: commit ANTES de tests, y los tests
     solo en un run `done` (un `needs_human_review` commitea WIP pero no testea);
  2. en `run_cycle._run_execution`: el evento de estado se publica DESPUÉS de que
     `conduct_execution` haya vuelto —donde ya ocurrió el commit— y además DESPUÉS
     de soltar el run-lock (H1: si se publica con el lock puesto, el despacho
     inmediato del review se descarta como `concurrent_run_locked` y el ciclo
     depende del reconciler).

Sin BD, sin Docker, sin Redis: todo son dobles.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from workers.config import Settings

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Dobles mínimos de las entradas de `_implementer_post_process`
# ---------------------------------------------------------------------------
def _prepared(*, execution_id: Any = None) -> SimpleNamespace:
    return SimpleNamespace(
        execution_id=execution_id or uuid4(),
        worktree_inputs=("tenant-a", "proj-a", str(uuid4()), str(uuid4()), "plan-slug"),
        task_acceptance_criteria=[
            {"id": "a", "description": "pasan", "runtime": "python-pytest", "command": "pytest"}
        ],
    )


def _workspace(host_path: str | None = "/data/agent-platform/worktrees/t") -> SimpleNamespace:
    return SimpleNamespace(host_path=host_path, read_only=False, error=None, code_diff=None)


def _result(status: str = "done") -> SimpleNamespace:
    return SimpleNamespace(
        status=status,
        abort_code=None,
        output="hecho",
        iterations=1,
        steps=[],
        usage={},
    )


async def _run_post_process(monkeypatch: pytest.MonkeyPatch, *, status: str = "done") -> list[str]:
    """Ejecuta `_implementer_post_process` con espías y devuelve la secuencia."""
    from workers import execution as exec_mod
    from workers import orchestration_drain

    calls: list[str] = []

    async def spy_drain(*_args: Any, **_kwargs: Any) -> None:
        calls.append("drain_comments")

    async def spy_commit(*_args: Any, **_kwargs: Any) -> None:
        calls.append("commit_and_push")

    async def spy_tests(*_args: Any, **_kwargs: Any) -> None:
        calls.append("run_test_runtime")

    async def spy_mark_failed(*_args: Any, **_kwargs: Any) -> None:
        calls.append("mark_commit_failed")

    monkeypatch.setattr(orchestration_drain, "drain_task_comment_effects", spy_drain)
    monkeypatch.setattr(exec_mod, "_commit_and_push_worktree", spy_commit)
    monkeypatch.setattr(exec_mod, "_run_task_tests", spy_tests)
    monkeypatch.setattr(exec_mod, "_mark_commit_failed", spy_mark_failed)

    await exec_mod._implementer_post_process(
        Settings(),
        object(),  # sessionmaker: solo viaja a los espías
        prepared=_prepared(),
        workspace=_workspace(),
        result=_result(status),
        task_id=uuid4(),
        tenant_id=uuid4(),
        exec_id=str(uuid4()),
        # ADR 0162 (opción A, ola 2): sin declaraciones el post-proceso no abre
        # ni la transacción de persistencia, así que el `object()` de arriba
        # sigue bastando como sessionmaker. El orden que fija este fichero no
        # cambia; lo que la declaración añade se fija en
        # `test_la_declaracion_cierra_el_circulo.py`.
        check_declarations=[],
    )
    return calls


# ===========================================================================
# Tramo 1 — dentro del post-proceso: commit ANTES de los tests
# ===========================================================================
@pytest.mark.asyncio
async def test_commit_happens_before_the_test_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = await _run_post_process(monkeypatch)
    assert "commit_and_push" in calls, "el run `done` con worktree no commiteó"
    assert "run_test_runtime" in calls, "el run `done` con criteria automáticos no lanzó tests"
    assert calls.index("commit_and_push") < calls.index("run_test_runtime"), (
        "los tests se lanzaron ANTES del commit: el test-runtime correría sobre un "
        f"worktree sin commitear (secuencia observada: {calls})"
    )


@pytest.mark.asyncio
async def test_escalated_run_commits_wip_but_does_not_run_tests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P2.3/F26: un `needs_human_review` commitea (para que el humano vea el diff)
    pero NO testea — solo un `done` reclama que su trabajo pase los tests."""
    calls = await _run_post_process(monkeypatch, status="needs_human_review")
    assert "commit_and_push" in calls
    assert "run_test_runtime" not in calls, (
        "un run escalado a needs_human_review lanzó el test-runtime: reportaría un "
        "TestReport de trabajo que el propio agente no certificó"
    )


@pytest.mark.asyncio
async def test_failed_run_neither_commits_nor_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = await _run_post_process(monkeypatch, status="failed")
    assert "commit_and_push" not in calls
    assert "run_test_runtime" not in calls


# ===========================================================================
# Tramo 2 — el evento de estado se publica DESPUÉS del post-proceso y del lock
# ===========================================================================
@pytest.mark.asyncio
async def test_status_event_is_published_after_conduct_and_after_the_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workers import run_lock as lock_mod
    from workers.execution import ExecutionOutcome, ExecutionRequest
    from workers.tasks import run_cycle

    calls: list[str] = []

    class _FakeEngine:
        async def dispose(self) -> None:
            calls.append("engine_dispose")

    class _FakeRedis:
        @staticmethod
        def from_url(*_a: Any, **_k: Any) -> _FakeRedis:
            return _FakeRedis()

        async def aclose(self) -> None:
            calls.append("redis_close")

    task = SimpleNamespace(id=uuid4(), tenant_id=uuid4(), title="t")

    async def spy_acquire(*_a: Any, **_k: Any) -> bool:
        calls.append("acquire_lock")
        return True

    async def spy_release(*_a: Any, **_k: Any) -> None:
        calls.append("release_lock")

    async def spy_conduct(*_a: Any, **_k: Any) -> ExecutionOutcome:
        # Dentro de conduct_execution ocurren finalize + commit + tests (tramo 1).
        calls.append("conduct_execution")
        return ExecutionOutcome(
            execution_id="e1",
            status="done",
            abort_code=None,
            pending_task_event=(task, "in_progress", "in_review"),
        )

    async def spy_publish(_redis: Any, _task: Any, *, old_status: str, new_status: str) -> None:
        calls.append(f"publish:{old_status}->{new_status}")

    # `worker_engine` y no `create_async_engine`: ver la nota en
    # tests/unit/test_stack_exec_errors.py::_wire_db — el engine compartido de la
    # remediación cambió la costura, no lo que este test fija (el ORDEN del finalize).
    monkeypatch.setattr(run_cycle, "worker_engine", lambda *_a, **_k: _FakeEngine())
    monkeypatch.setattr(run_cycle, "Redis", _FakeRedis)
    monkeypatch.setattr(lock_mod, "acquire_run_lock", spy_acquire)
    monkeypatch.setattr(lock_mod, "release_run_lock", spy_release)
    monkeypatch.setattr(run_cycle, "conduct_execution", spy_conduct)
    monkeypatch.setattr(run_cycle, "publish_task_status_changed", spy_publish)

    request = ExecutionRequest(
        tenant_id=str(uuid4()),
        task_id=str(task.id),
        agent_id=str(uuid4()),
        task={"id": str(task.id), "title": "t", "description": ""},
        model={"kind": "scripted", "decisions": [{"kind": "finish", "output": "ok"}]},
    )
    out = await run_cycle._run_execution(request, Settings(), celery_task_id="c1")

    assert out["status"] == "done"
    event = "publish:in_progress->in_review"
    assert event in calls, f"el evento de transición no se publicó nunca ({calls})"
    assert calls.index("conduct_execution") < calls.index(event), (
        "el evento in_review se publicó ANTES de que conduct_execution terminara: "
        "el orquestador podría despachar el review sobre un worktree sin commit "
        f"(secuencia observada: {calls})"
    )
    assert calls.index("release_lock") < calls.index(event), (
        "H1: el evento se publicó con el run-lock TODAVÍA puesto — el despacho "
        f"inmediato del review se descartaría como concurrent_run_locked ({calls})"
    )
