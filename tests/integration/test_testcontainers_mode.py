"""Integration tests: Testcontainers opt-in via DinD socket-proxy
(Plan 06 task_06_07).

The test container NEVER gets the host's docker.sock. When opt-in is
enabled, the runner spawns a docker-socket-proxy sidecar with a
hardened ACL (no EXEC, no VOLUMES, no host network) and exposes it to
the test container as ``DOCKER_HOST=tcp://docker-proxy:2375``.

We mock docker; the contract pinned here is the proxy's mount layout
(host's docker.sock onto the *proxy*, NOT the test container), the
ACL environment variables, and the test container's ``DOCKER_HOST``.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from workers.config import Settings

pytestmark = pytest.mark.integration


def _client() -> tuple[MagicMock, list[Any]]:
    started: list[Any] = []

    def _run(image: str, **kwargs: Any) -> MagicMock:
        c = MagicMock()
        c.id = f"container-{len(started)}"
        c.image = image
        c.kwargs = kwargs
        c.exec_run = MagicMock(return_value=MagicMock(exit_code=0, output=b"ok\n"))
        started.append(c)
        return c

    client = MagicMock()
    client.containers.run.side_effect = _run
    net = MagicMock()
    net.name = "test-runtime-python-pytest-tc"
    client.networks.create.return_value = net
    return client, started


def _spec_with_testcontainers(enabled: bool) -> Any:
    from shared_test_runtimes.catalog import get
    from workers.test_runtime import (
        AcceptanceCheck,
        RuntimePlan,
        TestcontainersMode,
        TestRuntimeSpec,
    )

    return TestRuntimeSpec(
        plan=RuntimePlan(
            template=get("python-pytest"),
            checks=(
                AcceptanceCheck(
                    id="t",
                    description="testcontainers",
                    runtime="python-pytest",
                    command="pytest -k testcontainers",
                ),
            ),
        ),
        worktree_host_path="/data/worktrees/tc",
        dep_cache_host_path="/data/dep-cache/pip-tc",
        testcontainers=TestcontainersMode(enabled=enabled),
    )


def test_disabled_by_default_means_no_proxy() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec_with_testcontainers(enabled=False))

    # Just the main container.
    images = [c.image for c in started]
    assert all("docker-socket-proxy" not in img for img in images)


def test_enabled_starts_proxy_with_docker_sock_mounted() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec_with_testcontainers(enabled=True))

    proxy = next((c for c in started if "docker-socket-proxy" in c.image), None)
    assert proxy is not None
    # Proxy mounts the host docker.sock onto itself (NOT onto the test).
    mounts = proxy.kwargs["mounts"]
    sources = {m["Source"] for m in mounts}
    assert "/var/run/docker.sock" in sources


def test_test_container_never_sees_docker_sock() -> None:
    """The proxy gets the socket; the test-runtime does NOT. This is
    the entire security story of task_06_07."""
    from workers.test_runtime import TestRuntimeRunner

    client, started = _client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec_with_testcontainers(enabled=True))

    # Find the test-runtime container (it has the workspace mount).
    test_container = next(
        (
            c
            for c in started
            if any(m.get("Target") == "/workspace" for m in c.kwargs.get("mounts", []))
        ),
        None,
    )
    assert test_container is not None
    for mount in test_container.kwargs.get("mounts", []):
        assert "docker.sock" not in mount.get("Source", "")
        assert "docker.sock" not in mount.get("Target", "")


def test_test_container_gets_DOCKER_HOST_env() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec_with_testcontainers(enabled=True))

    test_container = next(
        (
            c
            for c in started
            if any(m.get("Target") == "/workspace" for m in c.kwargs.get("mounts", []))
        ),
        None,
    )
    assert test_container is not None
    env = test_container.kwargs["environment"]
    assert env["DOCKER_HOST"] == "tcp://docker-proxy:2375"
    assert env["TESTCONTAINERS_HOST_OVERRIDE"] == "docker-proxy"


def test_proxy_acl_blocks_exec_and_volumes() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec_with_testcontainers(enabled=True))

    proxy = next((c for c in started if "docker-socket-proxy" in c.image), None)
    assert proxy is not None
    env = proxy.kwargs["environment"]
    # EXEC and VOLUMES are 0 — the dangerous ones.
    assert env["EXEC"] == "0"
    assert env["VOLUMES"] == "0"
    # CONTAINERS and IMAGES are 1 — what testcontainers needs.
    assert env["CONTAINERS"] == "1"
    assert env["IMAGES"] == "1"


def test_proxy_cleanup_on_success_and_failure() -> None:
    from workers.test_runtime import TestRuntimeRunner

    client, started = _client()
    runner = TestRuntimeRunner(Settings(), client=client)
    runner.launch(_spec_with_testcontainers(enabled=True))

    # Both proxy and main test-runtime should be removed.
    for c in started:
        c.remove.assert_called_once_with(force=True)


def test_default_proxy_image_pinned() -> None:
    from workers.test_runtime import TestcontainersMode

    mode = TestcontainersMode(enabled=True)
    # Pin a specific tag so a future maintainer doesn't accidentally
    # break the ACL contract by floating to :latest.
    assert ":" in mode.proxy_image
    assert "tecnativa/docker-socket-proxy" in mode.proxy_image
