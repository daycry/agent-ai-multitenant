"""ADR 0129 fase 2 — el review/preview monta los servicios del proyecto.

``_spawn_review_runtime`` traduce ``repository_config`` a sidecars endurecidos
sobre un **bridge interno per-sesión** (aislado, nunca en la red compartida
``agentic-agents`` para no filtrar entre tenants), conecta el contenedor
principal a ese bridge para que resuelva los aux por alias, le inyecta la
connection-env (DATABASE_URL/REDIS_URL/…) y devuelve TODOS los ids (main + aux)
para que los reapers los limpien. Sin servicios declarados: comportamiento
idéntico al de antes (solo main, sin bridge).
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.unit


class _FakeContainer:
    def __init__(self, cid: str, image: str, kwargs: dict[str, Any]) -> None:
        self.id = cid
        self.image = image
        self.kwargs = kwargs
        self.labels = dict(kwargs.get("labels") or {})

    def exec_run(self, _cmd: Any) -> Any:  # healthcheck always green
        return type("R", (), {"exit_code": 0})()


class _FakeNetwork:
    def __init__(self, name: str, kwargs: dict[str, Any]) -> None:
        self.name = name
        self.kwargs = kwargs
        self.connected: list[tuple[Any, tuple[str, ...]]] = []

    def connect(self, container: Any, aliases: list[str] | None = None) -> None:
        self.connected.append((container, tuple(aliases or ())))


class _FakeDocker:
    def __init__(self) -> None:
        self.ran: list[tuple[str, dict[str, Any]]] = []
        self.networks_created: list[_FakeNetwork] = []
        self._n = 0

        outer = self

        class _Containers:
            def run(self, image: str, **kwargs: Any) -> _FakeContainer:
                outer._n += 1
                cid = f"cid-{outer._n}"
                outer.ran.append((image, kwargs))
                return _FakeContainer(cid, image, kwargs)

        class _Networks:
            def create(self, name: str, **kwargs: Any) -> _FakeNetwork:
                net = _FakeNetwork(name, kwargs)
                outer.networks_created.append(net)
                return net

        self.containers = _Containers()
        self.networks = _Networks()


def _base_request(**overrides: Any) -> dict[str, Any]:
    req = {
        "main_image": "backend:plan-1",
        "worktree_host_path": "/data/wt/plan-1",
        "plan_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "22222222-2222-2222-2222-222222222222",
    }
    req.update(overrides)
    return req


def _install(monkeypatch: pytest.MonkeyPatch, client: _FakeDocker) -> Any:
    from workers.tasks import review_runtime_task as mod

    monkeypatch.setattr(mod, "get_docker_client", lambda: client)
    return mod


def test_no_services_spawns_only_main_no_bridge(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeDocker()
    mod = _install(monkeypatch, client)
    ids = mod._spawn_review_runtime(_base_request(), "sid-x", _settings())
    assert len(ids) == 1
    assert client.networks_created == []
    # main container went on the shared internal network for the proxy
    assert client.ran[0][1]["network"] == "agentic-agents"


def test_services_spawn_aux_on_private_bridge_and_inject_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeDocker()
    mod = _install(monkeypatch, client)
    req = _base_request(
        repository_config={
            "services": [{"type": "mysql"}, {"type": "redis"}],
            "env": {"APP_ENV": "review"},
        }
    )
    ids = mod._spawn_review_runtime(req, "sid-aux", _settings())

    # one bridge + main + 2 aux ids returned
    assert len(client.networks_created) == 1
    bridge = client.networks_created[0]
    assert bridge.kwargs.get("internal") is True
    assert len(ids) == 3  # main + mysql + redis

    # aux launched on the private bridge, labelled to the review session
    aux_runs = [r for r in client.ran if r[1].get("network") == bridge.name]
    assert {r[0] for r in aux_runs} == {"mysql:8", "redis:7-alpine"}
    for _img, kw in aux_runs:
        assert kw["labels"]["com.agentic-platform.review-session-id"] == "sid-aux"
        assert kw["labels"]["com.agentic-platform.component"] == "review-runtime"
        assert kw["cap_drop"] == ["ALL"]

    # main container: on agentic-agents, connected to the bridge, env injected
    main_run = next(r for r in client.ran if r[1].get("network") == "agentic-agents")
    assert main_run[1]["environment"]["DATABASE_URL"] == "mysql://app:app@mysql:3306/app"
    assert main_run[1]["environment"]["REDIS_URL"] == "redis://redis:6379/0"
    assert main_run[1]["environment"]["APP_ENV"] == "review"
    assert main_run[1]["environment"]["HOME"] == "/home/agent"  # not clobbered
    assert bridge.connected, "main container must be connected to the aux bridge"


def test_bridge_labelled_for_reaper(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeDocker()
    mod = _install(monkeypatch, client)
    req = _base_request(repository_config={"services": [{"type": "redis"}]})
    mod._spawn_review_runtime(req, "sid-net", _settings())
    bridge = client.networks_created[0]
    labels = bridge.kwargs["labels"]
    assert labels["com.agentic-platform.component"] == "review-runtime"
    assert labels["com.agentic-platform.managed"] == "true"
    assert labels["com.agentic-platform.review-session-id"] == "sid-net"


def test_invalid_services_config_falls_back_to_main_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeDocker()
    mod = _install(monkeypatch, client)
    req = _base_request(repository_config={"services": [{"type": "oraclexe"}]})
    ids = mod._spawn_review_runtime(req, "sid-bad", _settings())
    # invalid config must not strand the review: main still spawns, no aux/bridge
    assert len(ids) == 1
    assert client.networks_created == []


def _settings() -> Any:
    """Minimal Settings stand-in for build_hardened_run_kwargs + aux kwargs."""

    class _S:
        seccomp_profile_path = ""
        apparmor_profile = ""
        agent_network = "agentic-agents"
        container_mem_limit = "512m"
        container_pids_limit = 256
        container_tmp_size = "64m"
        container_home_size = "64m"
        container_workspace_size = "512m"
        aux_postgres_mem_limit = "256m"
        aux_redis_mem_limit = "128m"
        aux_default_pids_limit = 256

    return _S()
