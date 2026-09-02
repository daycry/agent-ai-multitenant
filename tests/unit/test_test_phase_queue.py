"""La fase de tests sale del worker `default` (task_wf_22, C-04).

`_run_task_tests` corre los tests de aceptación del proyecto justo después de
que el agente termina, y lo hacía **en proceso**: `await _run_test_runtime(...)`
dentro del worker de la cola `default`. Eso significa que el slot que acaba de
liberar un run se queda ocupado orquestando Docker —levantar el runtime, los
servicios auxiliares, N checks de hasta 600 s cada uno, teardown— con los
recursos del worker equivocado. `stack_exec` ya enruta a la cola `test` por
exactamente este motivo (ADR 0093); esta fase se había quedado atrás.

Se sigue **esperando** el resultado, y a propósito: el reviewer se despacha
después y necesita encontrar un `<test-report>` real, que es justo lo que el
hallazgo C1/F51 arregló. Lo que cambia es DÓNDE se hace el trabajo, no si se
espera. La espera es acotada y, si vence, la fase se da por perdida sin romper
un run que ya terminó bien.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


def _check(timeout_s: Any = 600) -> dict[str, Any]:
    return {"runtime": "python-pytest", "command": "pytest -q", "timeout_s": timeout_s}


# ---------------------------------------------------------------------------
# El presupuesto de espera
# ---------------------------------------------------------------------------
def test_the_budget_covers_every_check_plus_spin_up() -> None:
    """Los checks corren en SERIE dentro del mismo contenedor, así que el
    presupuesto es la suma; el margen cubre el arranque de la imagen, los
    servicios auxiliares y el teardown."""
    from workers.tasks.test_runtime_task import test_phase_wait_budget_s

    assert test_phase_wait_budget_s([_check(100), _check(200)]) > 300


def test_a_check_without_a_timeout_uses_the_default() -> None:
    from workers.tasks.test_runtime_task import test_phase_wait_budget_s

    assert test_phase_wait_budget_s([_check(None)]) == test_phase_wait_budget_s([_check(600)])


@pytest.mark.parametrize("bad", ["", "diez", -5, 0, {"a": 1}])
def test_a_nonsense_timeout_does_not_shrink_the_budget(bad: Any) -> None:
    """Un `timeout_s` basura no puede producir una espera de 0 s: cortaría la
    fase antes de empezar y el reviewer volvería a quedarse sin informe."""
    from workers.tasks.test_runtime_task import test_phase_wait_budget_s

    assert test_phase_wait_budget_s([_check(bad)]) == test_phase_wait_budget_s([_check(600)])


def test_the_budget_is_capped() -> None:
    """Cien checks de 600 s no pueden bloquear el slot 16 horas."""
    from workers.tasks.test_runtime_task import _TEST_PHASE_MAX_WAIT_S, test_phase_wait_budget_s

    assert test_phase_wait_budget_s([_check() for _ in range(100)]) == _TEST_PHASE_MAX_WAIT_S


def test_no_checks_needs_no_wait() -> None:
    from workers.tasks.test_runtime_task import test_phase_wait_budget_s

    assert test_phase_wait_budget_s([]) == 0


# ---------------------------------------------------------------------------
# El despacho
# ---------------------------------------------------------------------------
class _FakeAsyncResult:
    def __init__(self, value: Any = None, raises: Exception | None = None) -> None:
        self._value = value if value is not None else {"status": "ok"}
        self._raises = raises
        self.get_timeout: float | None = None

    def get(self, timeout: float | None = None) -> Any:
        self.get_timeout = timeout
        if self._raises is not None:
            raise self._raises
        return self._value


class _FakeApp:
    def __init__(self, result: _FakeAsyncResult | None = None) -> None:
        self.result = result or _FakeAsyncResult()
        self.sent: list[dict[str, Any]] = []

    def send_task(self, name: str, args: list[Any], queue: str) -> _FakeAsyncResult:
        self.sent.append({"name": name, "args": args, "queue": queue})
        return self.result


@pytest.mark.asyncio
async def test_the_phase_is_dispatched_to_the_test_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El hallazgo en una línea: esto corría en la cola `default`."""
    from workers.tasks import test_runtime_task

    fake = _FakeApp()
    monkeypatch.setattr(test_runtime_task, "app", fake)
    request = {"task_id": "t1", "acceptance_criteria": [_check()]}

    await test_runtime_task.dispatch_test_runtime_and_wait(request)

    assert len(fake.sent) == 1
    assert fake.sent[0]["name"] == "workers.run_test_runtime"
    assert fake.sent[0]["queue"] == "test"
    assert fake.sent[0]["args"] == [request]


@pytest.mark.asyncio
async def test_the_wait_is_bounded_by_the_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.tasks import test_runtime_task

    fake = _FakeApp()
    monkeypatch.setattr(test_runtime_task, "app", fake)
    request = {"task_id": "t1", "acceptance_criteria": [_check(120)]}

    await test_runtime_task.dispatch_test_runtime_and_wait(request)

    expected = test_runtime_task.test_phase_wait_budget_s([_check(120)])
    assert fake.result.get_timeout == expected


@pytest.mark.asyncio
async def test_the_result_comes_back_to_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.tasks import test_runtime_task

    fake = _FakeApp(_FakeAsyncResult({"status": "completed", "runtimes": []}))
    monkeypatch.setattr(test_runtime_task, "app", fake)

    out = await test_runtime_task.dispatch_test_runtime_and_wait(
        {"task_id": "t1", "acceptance_criteria": [_check()]}
    )
    assert out == {"status": "completed", "runtimes": []}


@pytest.mark.asyncio
async def test_a_broker_failure_never_breaks_a_finished_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El invariante que hay que conservar: el run YA terminó bien y la tarea ya
    se movió a review. Un fallo de la fase de tests no puede tumbarlo.

    Lo que cambió con el ADR 0162 (decisión 2, opción D): el fallo ya no se
    devuelve como `{}`. Devolver la nada era exactamente lo que hacía que un
    fallo de infraestructura se le presentara al reviewer igual que un proyecto
    sin tests. Aquí el request ni siquiera trae `tenant_id`, así que no puede
    persistirse el evento — y ni eso rompe el run."""
    from workers.tasks import test_runtime_task

    fake = _FakeApp(_FakeAsyncResult(raises=TimeoutError("no worker on `test`")))
    monkeypatch.setattr(test_runtime_task, "app", fake)

    out = await test_runtime_task.dispatch_test_runtime_and_wait(
        {"task_id": "t1", "acceptance_criteria": [_check()]}
    )
    assert out["status"] == "dispatch_failed"
    assert out["all_passed"] is False


@pytest.mark.asyncio
async def test_nothing_is_dispatched_without_automated_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workers.tasks import test_runtime_task

    fake = _FakeApp()
    monkeypatch.setattr(test_runtime_task, "app", fake)

    await test_runtime_task.dispatch_test_runtime_and_wait(
        {"task_id": "t1", "acceptance_criteria": []}
    )
    assert fake.sent == []


# ---------------------------------------------------------------------------
# El sitio de llamada
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_task_tests_no_longer_runs_the_runtime_in_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regresión directa de C-04: si alguien vuelve a llamar `_run_test_runtime`
    desde aquí, el slot de `default` se queda otra vez orquestando Docker."""
    from uuid import uuid4

    from workers import execution
    from workers.tasks import test_runtime_task

    async def _explode(*_a: Any, **_k: Any) -> dict[str, Any]:
        raise AssertionError("la fase de tests volvió a correr en proceso")

    dispatched: list[dict[str, Any]] = []

    async def _dispatch(request: dict[str, Any]) -> dict[str, Any]:
        dispatched.append(request)
        return {}

    monkeypatch.setattr(test_runtime_task, "_run_test_runtime", _explode)
    monkeypatch.setattr(test_runtime_task, "dispatch_test_runtime_and_wait", _dispatch)

    await execution._run_task_tests(
        execution.Settings(),
        tenant_id=uuid4(),
        task_id=uuid4(),
        worktree_host_path="/data/wt/t1",
        acceptance_criteria=[_check()],
    )

    assert len(dispatched) == 1
    assert dispatched[0]["worktree_host_path"] == "/data/wt/t1"


# ---------------------------------------------------------------------------
# Auditoría 2026-09-01 (A-02): el `get()` corría DENTRO de un task prefork
# ---------------------------------------------------------------------------
# Celery prohíbe `AsyncResult.get()` dentro de un task («Never call result.get()
# within a task!»): en el hijo prefork `task_join_will_block()` es True y `get()`
# lanza `RuntimeError` antes de tocar el backend. El `_FakeAsyncResult` de arriba
# no reproduce esa guarda, así que el cierre de C-04 (`task_wf_22`) quedó en verde
# con la fase de tests muerta en producción: cada `done` con criterios acababa en
# `test_phase_dispatch_failed` y el reviewer nunca vio un test real.


class _AsyncResultConLaGuardaDeCelery(_FakeAsyncResult):
    """Un resultado cuyo `get()` aplica la MISMA guarda que Celery."""

    def get(self, timeout: float | None = None) -> Any:
        from celery.result import assert_will_not_block

        assert_will_not_block()  # RuntimeError si estamos «dentro de un task»
        return super().get(timeout)


@pytest.mark.asyncio
async def test_the_wait_survives_inside_a_prefork_task(monkeypatch: pytest.MonkeyPatch) -> None:
    from celery._state import _set_task_join_will_block
    from workers.tasks import test_runtime_task

    fake = _FakeApp(_AsyncResultConLaGuardaDeCelery({"status": "completed", "runtimes": []}))
    monkeypatch.setattr(test_runtime_task, "app", fake)
    request = {"task_id": "t1", "acceptance_criteria": [_check()]}

    _set_task_join_will_block(True)  # lo que hace el hijo prefork al arrancar
    try:
        result = await test_runtime_task.dispatch_test_runtime_and_wait(request)
    finally:
        _set_task_join_will_block(False)

    assert result.get("status") == "completed", (
        f"la espera murió con la guarda de Celery y volvió {result.get('status')!r}: "
        "la fase de tests no corre nunca en un worker prefork"
    )
