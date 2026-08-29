"""Un fallo de infraestructura deja de parecerse a un proyecto sin tests
(ADR 0162, decisión 2 opción D).

El módulo `workers.tasks.test_runtime_task` tenía **cinco puntos** donde una
excepción se convertía en silencio, y el silencio llega al reviewer exactamente
igual que «este proyecto no declara tests»:

1. `dispatch_test_runtime_and_wait` — broker caído / sin worker en `test` /
   presupuesto vencido → `_log.warning` y `{}`. Ni un evento de auditoría.
2. `_launch_test_runtime_plans` — `except ImportError: return None`, sin log.
3. `_launch_test_runtime_plans` — `get_docker_client() is None → return None`,
   sin log; el caller devolvía el stub `docker_unavailable` **sin persistir
   nada**, así que el informe se quedaba vacío.
4. `_launch_test_runtime_plans` — `except KeyError: return []` (runtime id
   desconocido). El comentario decía que el orchestrator lo expondría; no lo
   hacía nadie.
5. `_launch_test_runtime_plans` — `except RuntimeServicesConfigError` → corre sin
   servicios auxiliares y no lo cuenta.

Y el que el propio ADR pone de ejemplo: `runner.launch()` lanzando
`RuntimeImageUnavailableError` («no se pudo obtener la imagen fijada por
digest») tumbaba la tarea Celery entera, se la tragaba el punto 1 y se llevaba
por delante los planes que quedaban.

Lo que estos tests exigen: cada uno produce un **registro visible** que llega al
informe como FALLO. Y lo que NO exigen —y hay que conservar— es que nada de esto
tumbe un run que ya había terminado bien.
"""

from __future__ import annotations

import builtins
from typing import Any
from uuid import uuid4

import pytest

pytestmark = pytest.mark.unit

_INFRA = "infrastructure_failure"


def _check(runtime: str = "python-pytest", command: str = "pytest -q") -> dict[str, Any]:
    return {
        "check_type": "automated",
        "id": f"auto_{runtime}",
        "description": "x",
        "runtime": runtime,
        "command": command,
        "timeout_s": 60,
    }


def _request(*checks: dict[str, Any]) -> dict[str, Any]:
    return {
        "tenant_id": str(uuid4()),
        "task_id": str(uuid4()),
        "worktree_host_path": "/data/wt/t1",
        "acceptance_criteria": list(checks) or [_check()],
    }


# ---------------------------------------------------------------------------
# Dobles: la BD del worker, sin BD
# ---------------------------------------------------------------------------
class _FakeTxn:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _FakeSession:
    def begin(self) -> _FakeTxn:
        return _FakeTxn()

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False


class _FakeEngine:
    async def dispose(self) -> None:
        return None


@pytest.fixture
def audit_sink(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Captura los `append_audit_event` sin tocar PostgreSQL."""
    from api_server.db import task_audit_repo
    from workers.tasks import test_runtime_task

    events: list[dict[str, Any]] = []

    async def _append(_session: Any, **kwargs: Any) -> None:
        events.append(kwargs)

    def _engine(*_a: Any, **_k: Any) -> _FakeEngine:
        return _FakeEngine()

    def _sessionmaker(*_a: Any, **_k: Any) -> Any:
        def _factory() -> _FakeSession:
            return _FakeSession()

        return _factory

    monkeypatch.setattr(task_audit_repo, "append_audit_event", _append)
    monkeypatch.setattr(test_runtime_task, "worker_engine", _engine)
    monkeypatch.setattr(test_runtime_task, "async_sessionmaker", _sessionmaker)
    return events


def _a_live_docker_client() -> object:
    """Un daemon que responde: estos tests no van del daemon, van de lo que pasa
    DESPUÉS de que responda."""
    return object()


def _infra_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        e
        for e in events
        if e.get("kind") == "test_run_completed" and (e.get("payload") or {}).get(_INFRA)
    ]


# ---------------------------------------------------------------------------
# Punto 1 — el despacho de la fase
# ---------------------------------------------------------------------------
class _ExplodingAsyncResult:
    def get(self, timeout: float | None = None) -> Any:
        raise TimeoutError("no worker on `test`")


class _ExplodingApp:
    def send_task(self, name: str, args: list[Any], queue: str) -> _ExplodingAsyncResult:
        return _ExplodingAsyncResult()


@pytest.mark.asyncio
async def test_a_broker_failure_is_recorded_instead_of_swallowed(
    monkeypatch: pytest.MonkeyPatch, audit_sink: list[dict[str, Any]]
) -> None:
    from workers.tasks import test_runtime_task

    monkeypatch.setattr(test_runtime_task, "app", _ExplodingApp())

    out = await test_runtime_task.dispatch_test_runtime_and_wait(_request())

    infra = _infra_events(audit_sink)
    assert len(infra) == 1, "el fallo del despacho de la fase de tests se tragó otra vez"
    assert infra[0]["payload"]["all_passed"] is False
    assert infra[0]["payload"][_INFRA] == "test_phase_dispatch_failed"
    assert "TimeoutError" in str(infra[0]["payload"].get("logs_tail", ""))
    # …y el run que ya había terminado bien no se rompe.
    assert out.get("all_passed") is False


@pytest.mark.asyncio
async def test_a_broker_failure_never_raises_even_if_the_db_is_also_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El invariante que NO se puede perder: el run ya terminó y la tarea ya está
    en review. Ni el fallo del broker ni el de la BD pueden tumbarlo."""
    from workers.tasks import test_runtime_task

    monkeypatch.setattr(test_runtime_task, "app", _ExplodingApp())

    def _no_db(*_a: Any, **_k: Any) -> Any:
        raise RuntimeError("la BD tampoco responde")

    monkeypatch.setattr(test_runtime_task, "worker_engine", _no_db)

    out = await test_runtime_task.dispatch_test_runtime_and_wait(_request())
    assert isinstance(out, dict)


# ---------------------------------------------------------------------------
# Puntos 2 y 3 — el SDK que no está y el daemon que no responde
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_missing_runtime_sdk_is_recorded_instead_of_returning_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workers.config import Settings
    from workers.tasks import test_runtime_task

    real_import = builtins.__import__

    def _boom(name: str, *a: Any, **k: Any) -> Any:
        if name == "workers.test_runtime":
            raise ImportError("no docker SDK in this image")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)

    outcomes = await test_runtime_task._launch_test_runtime_plans(_request(), Settings())

    assert outcomes, "el ImportError volvió a devolver silencio"
    assert outcomes[0][_INFRA] == "runtime_sdk_missing"
    assert outcomes[0]["all_passed"] is False


@pytest.mark.asyncio
async def test_a_dead_docker_daemon_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.config import Settings
    from workers.tasks import test_runtime_task

    monkeypatch.setattr(test_runtime_task, "get_docker_client", lambda: None)

    outcomes = await test_runtime_task._launch_test_runtime_plans(_request(), Settings())

    assert outcomes, "el daemon caído volvió a devolver silencio"
    assert outcomes[0][_INFRA] == "docker_unavailable"


@pytest.mark.asyncio
async def test_the_dead_daemon_reaches_the_audit_log(
    monkeypatch: pytest.MonkeyPatch, audit_sink: list[dict[str, Any]]
) -> None:
    """El informe del reviewer se arma leyendo EVENTOS, no el valor de retorno
    (que nadie consume). Sin evento, el fallo no existe para nadie."""
    from workers.config import Settings
    from workers.tasks import test_runtime_task

    monkeypatch.setattr(test_runtime_task, "get_docker_client", lambda: None)

    result = await test_runtime_task._run_test_runtime(_request(), Settings())

    assert _infra_events(audit_sink), "el stub `docker_unavailable` no persistió nada"
    # El contrato documentado del stub se conserva.
    assert result["status"] == "docker_unavailable"


# ---------------------------------------------------------------------------
# Punto 4 — un runtime id que no está en el catálogo
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_an_unknown_runtime_id_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    from workers.config import Settings
    from workers.tasks import test_runtime_task

    monkeypatch.setattr(test_runtime_task, "get_docker_client", _a_live_docker_client)

    outcomes = await test_runtime_task._launch_test_runtime_plans(
        _request(_check(runtime="cobol-batch", command="run")), Settings()
    )

    assert outcomes, "un runtime desconocido volvió a producir cero outcomes"
    assert outcomes[0][_INFRA] == "unknown_runtime"
    assert "cobol-batch" in str(outcomes[0].get("logs_tail", ""))


# ---------------------------------------------------------------------------
# Punto 5 — servicios auxiliares mal declarados (ADR 0129)
# ---------------------------------------------------------------------------
def _a_successful_result(runtime: str) -> Any:
    """Un resultado de lanzamiento correcto, con el tipo REAL.

    Esto era un `_FakeResult` escrito a mano con los seis atributos que el
    serializador leía entonces, y el día que el outcome ganó campos (ADR 0162,
    ola 1) el doble se quedó atrás y reventó. Usar `TestRuntimeResult` de verdad
    es lo que impide que vuelva a pasar: si el contrato crece, o el doble crece
    con él o falla la construcción aquí mismo, que es donde se ve."""
    from workers.test_runtime import TestRuntimeResult

    return TestRuntimeResult(
        runtime=runtime,
        exit_codes=(0,),
        logs="ok",
        container_id="c1",
        network_name="n1",
        timed_out=False,
    )


class _FakeRunner:
    def __init__(self, *_a: Any, **_k: Any) -> None:
        self.launched: list[Any] = []

    def launch(self, spec: Any) -> Any:
        self.launched.append(spec)
        return _a_successful_result(spec.plan.template.id)


@pytest.mark.asyncio
async def test_a_bad_aux_services_config_is_recorded_and_the_checks_still_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El flujo NO cambia: se sigue corriendo sin servicios. Lo que cambia es que
    ahora se sabe."""
    from workers import runtime_services, test_runtime
    from workers.config import Settings
    from workers.tasks import test_runtime_task

    monkeypatch.setattr(test_runtime_task, "get_docker_client", _a_live_docker_client)
    monkeypatch.setattr(test_runtime, "TestRuntimeRunner", _FakeRunner)

    calls: list[Any] = []

    def _services(config: Any) -> Any:
        calls.append(config)
        if len(calls) == 1:
            raise runtime_services.RuntimeServicesConfigError("`services[0].image` vacío")
        return runtime_services.ProjectRuntimeServices()

    monkeypatch.setattr(runtime_services, "build_project_runtime_services", _services)

    request = _request()
    request["repository_config"] = {"services": [{}]}
    outcomes = await test_runtime_task._launch_test_runtime_plans(request, Settings())

    infra = [o for o in outcomes if o.get(_INFRA)]
    ran = [o for o in outcomes if not o.get(_INFRA)]
    assert infra, "la config de servicios rota se volvió a tragar"
    assert infra[0][_INFRA] == "aux_services_config_invalid"
    assert ran, "el flujo cambió: los checks dejaron de correr"


# ---------------------------------------------------------------------------
# El ejemplo del propio ADR — la imagen fijada por digest que no se pudo obtener
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_launch_failure_is_recorded_and_the_other_plans_still_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from workers import test_runtime
    from workers.config import Settings
    from workers.tasks import test_runtime_task

    monkeypatch.setattr(test_runtime_task, "get_docker_client", _a_live_docker_client)

    class _HalfBrokenRunner:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def launch(self, spec: Any) -> Any:
            if spec.plan.template.id == "python-pytest":
                raise test_runtime.RuntimeImageUnavailableError(
                    "no se pudo obtener la imagen de runtime fijada por digest"
                )
            return _a_successful_result(spec.plan.template.id)

    monkeypatch.setattr(test_runtime, "TestRuntimeRunner", _HalfBrokenRunner)

    outcomes = await test_runtime_task._launch_test_runtime_plans(
        _request(_check(), _check(runtime="node-jest", command="jest")), Settings()
    )

    by_runtime = {str(o["runtime"]): o for o in outcomes}
    assert by_runtime["python-pytest"][_INFRA] == "runtime_launch_failed"
    assert "digest" in str(by_runtime["python-pytest"]["logs_tail"])
    # El plan que SÍ podía correr corrió: antes la excepción se llevaba el resto.
    assert by_runtime["node-jest"]["all_passed"] is True


# ---------------------------------------------------------------------------
# Forma del outcome: tiene que renderizar en el bloque del reviewer sin tocarlo
# ---------------------------------------------------------------------------
def test_the_infra_outcome_has_the_shape_the_report_block_expects() -> None:
    from orchestrator.dispatch import _format_test_report_block
    from workers.tasks.test_runtime_task import infra_failure_outcome

    outcome = infra_failure_outcome(stage="docker_unavailable", detail="daemon down")
    block = _format_test_report_block(
        [outcome],
        project_declares_runtime=True,
        executable_criteria=1,
        tests_were_launched=True,
    )
    assert "INFRASTRUCTURE FAILURE" in block
    assert "docker_unavailable" in block


def test_the_infra_outcome_is_json_safe() -> None:
    """Va a un JSONB de auditoría: nada de objetos ni excepciones dentro."""
    import json

    from workers.tasks.test_runtime_task import infra_failure_outcome

    outcome = infra_failure_outcome(
        stage="runtime_launch_failed", detail="boom", runtime="python-pytest"
    )
    assert json.loads(json.dumps(outcome)) == outcome


def test_the_two_outcome_shapes_carry_the_same_keys() -> None:
    """El outcome de FALLO y el de éxito tienen que ser el mismo dict.

    Es la razón de que `infra_failure_outcome` exista con esta forma: el bloque
    `<test-report>` los renderiza sin casos especiales, así que una clave
    presente sólo en uno de los dos se leería como ausente en el otro — y en
    este módulo «ausente» es justo lo que no puede confundirse con nada.

    Esta guarda sustituye a una que comparaba el resultado real contra un doble
    escrito a mano recorriendo una LISTA de atributos: al crecer el contrato
    (ADR 0162, ola 1) la lista se quedó corta y la guarda no vio la divergencia.
    Comparar los conjuntos de claves enteros no se queda corto nunca."""
    from workers.tasks.test_runtime_task import INFRA_FAILURE_KEY, infra_failure_outcome
    from workers.tasks.test_runtime_task import runtime_outcome as serialise

    ok = serialise(_a_successful_result("python-pytest"))
    ko = infra_failure_outcome(stage="docker_unavailable", detail="daemon down")

    assert set(ko) - {INFRA_FAILURE_KEY} == set(ok)
