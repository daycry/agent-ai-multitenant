"""El worker que se apaga mata sus contenedores y sella sus runs (`task_cv_43`).

Auditoría 2026-09-01 (G-08): el quiesce nocturno del backup (y cualquier
`docker compose stop`) manda SIGTERM al worker; Celery espera al job, el job
espera al contenedor, y a los 10 s de gracia el worker muere. El agent-runtime
sigue vivo, facturando tokens hasta 6-7 h, y su fila `executions` queda
`running` hasta que el sweeper la sella como `stale_after_worker_loss`.

Ahora cada contenedor lleva la etiqueta del worker que lo lanzó, y al recibir
`worker_shutting_down` el worker mata SUS contenedores de agent-runtime y sella
sus filas (`failed`, `abort_code=quiesced`) en el acto.

Y la DLQ de ejecuciones (A-09) tiene lector: un endpoint System Admin que
enseña la profundidad y las últimas entradas de cada stream.
"""

from __future__ import annotations

from typing import Any

import pytest
from workers.config import Settings
from workers.container import WORKER_LABEL, AgentContainerRunner, ContainerSpec

pytestmark = pytest.mark.unit


# ----------------------------------------------------------- etiqueta del worker


class _FakeContainer:
    def __init__(self, name: str, labels: dict[str, str]) -> None:
        self.name = name
        self.id = f"ctr-{name}"
        self.labels = labels
        self.status = "running"
        self.attrs: dict[str, Any] = {
            "State": {"ExitCode": 0},
            "Config": {"Env": [], "Labels": labels},
            "HostConfig": {},
            "NetworkSettings": {"Networks": {}},
        }
        self.killed = False
        self.removed = False

    def reload(self) -> None:
        return None

    def logs(self, **_kw: Any) -> bytes:
        return b""

    def kill(self) -> None:
        self.killed = True

    def remove(self, **_kw: Any) -> None:
        self.removed = True


class _FakeDocker:
    def __init__(self, containers: list[_FakeContainer] | None = None) -> None:
        self.run_kwargs: list[dict[str, Any]] = []
        self._containers = list(containers or [])
        outer = self

        class _Containers:
            def run(self, image: str, **kwargs: Any) -> _FakeContainer:
                outer.run_kwargs.append(kwargs)
                return _FakeContainer(kwargs.get("name") or "run", dict(kwargs.get("labels") or {}))

            def list(self, **kwargs: Any) -> list[_FakeContainer]:
                wanted = (kwargs.get("filters") or {}).get("label") or []
                if isinstance(wanted, str):
                    wanted = [wanted]
                out = []
                for c in outer._containers:
                    ok = True
                    for item in wanted:
                        key, _sep, value = str(item).partition("=")
                        if c.labels.get(key) != value:
                            ok = False
                    if ok:
                        out.append(c)
                return out

            def get(self, name: str) -> _FakeContainer:
                raise KeyError(name)

        class _Networks:
            def get(self, name: str) -> Any:
                return object()  # la red compartida "existe": no se crea nada

            def create(self, name: str, **_kw: Any) -> Any:
                raise AssertionError("no debería crear redes en este test")

        self.containers = _Containers()
        self.networks = _Networks()


def test_every_container_carries_the_worker_that_launched_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import workers.container as container_mod

    monkeypatch.setattr(container_mod, "worker_identity", lambda: "workers-heavy-3")
    docker = _FakeDocker()
    runner = AgentContainerRunner(Settings(agent_network_per_execution=False), client=docker)

    runner.run(
        ContainerSpec(
            image="agent-runtime:test",
            env={},
            labels={"com.agentic-platform.execution-id": "exec-1"},
        ),
        timeout=5,
    )

    labels = docker.run_kwargs[0]["labels"]
    assert labels[WORKER_LABEL] == "workers-heavy-3"
    assert labels["com.agentic-platform.execution-id"] == "exec-1"


# ----------------------------------------------------------- quiesce


def _agent(name: str, worker: str, execution_id: str | None) -> _FakeContainer:
    labels = {
        "com.agentic-platform.component": "agent-runtime",
        "com.agentic-platform.managed": "true",
        WORKER_LABEL: worker,
    }
    if execution_id is not None:
        labels["com.agentic-platform.execution-id"] = execution_id
    return _FakeContainer(name, labels)


def test_shutdown_kills_only_this_workers_runs_and_seals_them() -> None:
    from workers.quiesce import quiesce_worker_runs

    mine = _agent("run-a", "workers-heavy-3", "exec-a")
    mine_no_exec = _agent("run-b", "workers-heavy-3", None)
    theirs = _agent("run-c", "workers-default-1", "exec-c")
    docker = _FakeDocker([mine, mine_no_exec, theirs])
    sealed: list[list[str]] = []

    report = quiesce_worker_runs(
        docker, worker_id="workers-heavy-3", seal=lambda ids: sealed.append(list(ids)) or len(ids)
    )

    assert mine.killed and mine_no_exec.killed, "un run de este worker siguió facturando"
    assert not theirs.killed, "se mató el run de OTRO worker"
    assert sealed == [["exec-a"]], sealed
    assert report == {"killed": 2, "sealed": 1}


def test_shutdown_with_nothing_running_is_a_noop() -> None:
    from workers.quiesce import quiesce_worker_runs

    calls: list[Any] = []
    report = quiesce_worker_runs(
        _FakeDocker([]), worker_id="workers-heavy-3", seal=lambda ids: calls.append(ids) or 0
    )

    assert report == {"killed": 0, "sealed": 0}
    assert calls == [], "se llamó a sellar sin nada que sellar"


def test_a_container_that_refuses_to_die_does_not_stop_the_others() -> None:
    from workers.quiesce import quiesce_worker_runs

    stubborn = _agent("run-x", "w", "exec-x")

    def _boom() -> None:
        raise RuntimeError("daemon hiccup")

    stubborn.kill = _boom  # type: ignore[method-assign]
    other = _agent("run-y", "w", "exec-y")
    sealed: list[list[str]] = []

    report = quiesce_worker_runs(
        _FakeDocker([stubborn, other]),
        worker_id="w",
        seal=lambda ids: sealed.append(list(ids)) or len(ids),
    )

    assert other.killed
    assert sealed == [["exec-x", "exec-y"]], "el run que no murió se dejó sin sellar"
    assert report["killed"] == 1


def test_the_signal_receiver_is_installed_once_on_the_celery_app() -> None:
    import workers.celery_app  # noqa: F401 - importar la app instala los receptores
    from workers import quiesce

    assert quiesce.is_installed(), "worker_shutting_down no está conectado en el arranque"


# ----------------------------------------------------------- lector de la DLQ


class _FakeAsyncRedis:
    def __init__(self, streams: dict[str, list[tuple[str, dict[str, str]]]]) -> None:
        self.streams = streams

    async def xlen(self, name: str) -> int:
        return len(self.streams.get(name, []))

    async def xrevrange(self, name: str, max: str = "+", min: str = "-", count: int | None = None):
        entries = list(reversed(self.streams.get(name, [])))
        return entries[:count] if count else entries


@pytest.mark.asyncio
async def test_the_dead_letter_reader_reports_depth_and_latest_entries() -> None:
    from api_server.routers.admin import _read_dead_letters

    redis = _FakeAsyncRedis(
        {
            "dlq:executions": [
                ("1-0", {"task": "workers.run_execution", "task_id": "t1", "error": "boom"}),
                ("2-0", {"task": "workers.run_execution", "task_id": "t2", "error": "bang"}),
                ("3-0", {"task": "workers.run_execution", "task_id": "t3", "error": "crash"}),
            ],
            "dlq:notifications": [],
        }
    )

    report = await _read_dead_letters(redis, ("dlq:executions", "dlq:notifications"), limit=2)

    by_name = {s["stream"]: s for s in report}
    assert by_name["dlq:executions"]["depth"] == 3
    assert [e["id"] for e in by_name["dlq:executions"]["entries"]] == ["3-0", "2-0"]
    assert by_name["dlq:executions"]["entries"][0]["fields"]["task_id"] == "t3"
    assert by_name["dlq:notifications"] == {
        "stream": "dlq:notifications",
        "depth": 0,
        "entries": [],
    }


def test_the_dead_letter_endpoint_is_a_system_admin_route() -> None:
    from api_server.routers.admin import router

    paths = {getattr(route, "path", None) for route in router.routes}
    assert "/admin/dead-letters" in paths, sorted(p for p in paths if p)
