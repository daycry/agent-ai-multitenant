"""Cada ejecución corre en su propio bridge interno (`task_cv_25`, B-07).

Auditoría 2026-09-01. Todos los sandboxes de todos los tenants, los previews de
review, el api-server y los workers compartían `agentic-agents` con ICC
activado, mientras `isolation.py` prometía «una red dedicada». Dos sandboxes de
tenants distintos podían hablarse por IP. Ahora el runner crea un bridge
`internal` por ejecución (el patrón de `test_runtime._create_bridge`), le
conecta SÓLO lo que el run necesita —el egress-proxy, el api-server interno y
los servidores MCP internos que declare el proyecto— con sus alias, y lo
desmonta al terminar aunque el contenedor reviente.
"""

from __future__ import annotations

from typing import Any

import docker.errors
import pytest
from workers.config import Settings
from workers.container import AgentContainerRunner, ContainerSpec

pytestmark = pytest.mark.unit


class _FakeNetwork:
    def __init__(self, name: str, **kwargs: Any) -> None:
        self.name = name
        self.id = f"net-{name}"
        self.attrs = {"Name": name, "Labels": kwargs.get("labels") or {}, "Containers": {}}
        self.kwargs = kwargs
        self.connected: list[tuple[str, list[str]]] = []
        self.disconnected: list[str] = []
        self.removed = False

    def connect(self, container: Any, aliases: list[str] | None = None, **_kw: Any) -> None:
        self.connected.append((container.name, list(aliases or [])))
        self.attrs["Containers"][container.id] = {"Name": container.name}

    def disconnect(self, container: Any, force: bool = False) -> None:
        self.disconnected.append(container.name)
        self.attrs["Containers"].pop(container.id, None)

    def remove(self) -> None:
        self.removed = True

    def reload(self) -> None:
        return None


class _FakeContainer:
    def __init__(self, name: str, *, labels: dict[str, str] | None = None) -> None:
        self.name = name
        self.id = f"ctr-{name}"
        self.status = "exited"
        self.labels = labels or {}
        self.attrs: dict[str, Any] = {
            "State": {"ExitCode": 0},
            "Config": {"Env": []},
            "HostConfig": {},
            "NetworkSettings": {"Networks": {}},
        }
        self.removed = False
        self.remove_raises = False

    def reload(self) -> None:
        return None

    def logs(self, **_kw: Any) -> bytes:
        return b""

    def remove(self, **_kw: Any) -> None:
        self.removed = True
        if self.remove_raises:
            raise RuntimeError("daemon hiccup")


class _FakeDocker:
    def __init__(self, peers: dict[str, _FakeContainer]) -> None:
        self.peers = peers
        self.networks_created: list[_FakeNetwork] = []
        self.run_kwargs: list[dict[str, Any]] = []
        self.last_container: _FakeContainer | None = None
        outer = self

        class _Networks:
            def create(self, name: str, **kwargs: Any) -> _FakeNetwork:
                net = _FakeNetwork(name, **kwargs)
                outer.networks_created.append(net)
                return net

            def get(self, name: str) -> _FakeNetwork:
                for net in outer.networks_created:
                    if net.name == name:
                        return net
                raise docker.errors.NotFound(name)

            def list(self, filters: dict[str, Any] | None = None) -> list[_FakeNetwork]:
                return [n for n in outer.networks_created if not n.removed]

        class _Containers:
            def get(self, name: str) -> _FakeContainer:
                if name in outer.peers:
                    return outer.peers[name]
                raise KeyError(name)

            def list(self, **kwargs: Any) -> list[_FakeContainer]:
                label = str((kwargs.get("filters") or {}).get("label") or "")
                if not label:
                    return []
                key, _sep, value = label.partition("=")
                return [c for c in outer.peers.values() if c.labels.get(key) == value]

            def run(self, image: str, **kwargs: Any) -> _FakeContainer:
                outer.run_kwargs.append(kwargs)
                container = _FakeContainer(kwargs.get("name") or "run")
                outer.last_container = container
                return container

        self.networks = _Networks()
        self.containers = _Containers()


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "egress_proxy_url": "http://egress-proxy:8888",
        "agent_network_per_execution": True,
    }
    base.update(overrides)
    return Settings(**base)


def _spec(**kw: Any) -> ContainerSpec:
    return ContainerSpec(
        image="agent-runtime:test",
        env={"AGENTIC_API_URL": "http://api-server:8000"},
        labels={"com.agentic-platform.execution-id": "exec-1"},
        **kw,
    )


def _peers() -> dict[str, _FakeContainer]:
    return {
        "agentic-egress-proxy": _FakeContainer("agentic-egress-proxy"),
        "agentic-platform-api-server-1": _FakeContainer(
            "agentic-platform-api-server-1",
            labels={"com.docker.compose.service": "api-server"},
        ),
        "agentic-platform-docling-1": _FakeContainer(
            "agentic-platform-docling-1", labels={"com.docker.compose.service": "docling"}
        ),
    }


def test_each_run_gets_its_own_internal_bridge_with_only_its_peers() -> None:
    docker = _FakeDocker(_peers())
    runner = AgentContainerRunner(_settings(), client=docker)

    runner.run(_spec(peers=("docling",)), timeout=5)

    assert len(docker.networks_created) == 1, "no se creó un bridge propio"
    bridge = docker.networks_created[0]
    assert bridge.kwargs.get("internal") is True
    assert bridge.attrs["Labels"].get("com.agentic-platform.run-bridge") == "true"
    assert docker.run_kwargs[0]["network"] == bridge.name
    connected = dict(bridge.connected)
    assert connected["agentic-egress-proxy"] == ["egress-proxy"]
    assert connected["agentic-platform-api-server-1"] == ["api-server"]
    assert connected["agentic-platform-docling-1"] == ["docling"]


def test_the_bridge_is_torn_down_after_the_run_even_if_remove_fails() -> None:
    docker = _FakeDocker(_peers())
    runner = AgentContainerRunner(_settings(), client=docker)
    original_run = docker.containers.run

    def _run_then_break(image: str, **kwargs: Any) -> _FakeContainer:
        container = original_run(image, **kwargs)
        container.remove_raises = True
        return container

    docker.containers.run = _run_then_break  # type: ignore[method-assign]

    runner.run(_spec(), timeout=5)

    bridge = docker.networks_created[0]
    assert set(bridge.disconnected) >= {"agentic-egress-proxy", "agentic-platform-api-server-1"}
    assert bridge.removed, "el bridge quedó huérfano tras el run"


def test_with_the_flag_off_the_shared_network_is_used() -> None:
    docker = _FakeDocker(_peers())
    runner = AgentContainerRunner(_settings(agent_network_per_execution=False), client=docker)

    runner.run(_spec(), timeout=5)

    assert docker.networks_created == [] or all(
        n.name == "agentic-agents" for n in docker.networks_created
    )
    assert docker.run_kwargs[0]["network"] == "agentic-agents"


def test_a_missing_peer_does_not_block_the_run() -> None:
    docker = _FakeDocker({"agentic-egress-proxy": _FakeContainer("agentic-egress-proxy")})
    runner = AgentContainerRunner(_settings(), client=docker)

    result = runner.run(_spec(peers=("docling",)), timeout=5)

    assert result.exit_code == 0
    bridge = docker.networks_created[0]
    assert [name for name, _a in bridge.connected] == ["agentic-egress-proxy"]


def test_orphan_bridges_are_pruned_but_busy_ones_are_not() -> None:
    docker = _FakeDocker(_peers())
    runner = AgentContainerRunner(_settings(), client=docker)
    orphan = docker.networks.create(
        "agent-run-dead", internal=True, labels={"com.agentic-platform.run-bridge": "true"}
    )
    busy = docker.networks.create(
        "agent-run-live", internal=True, labels={"com.agentic-platform.run-bridge": "true"}
    )
    busy.attrs["Containers"]["ctr-x"] = {"Name": "x"}

    removed = runner.prune_run_bridges()

    assert removed == 1
    assert orphan.removed and not busy.removed
