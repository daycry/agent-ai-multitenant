"""Integration tests: the agent container has no path to the Docker
socket — no mount, no file, no client (task_02_09).

A container that can reach the Docker daemon can trivially escape to
the host, so this gets its own dedicated test: both the behavioural
fact (the socket is absent inside a real hardened container) and the
`assert_no_docker_socket` tripwire that guards every launch.
"""

from __future__ import annotations

import pytest
from workers.config import Settings
from workers.container import AgentContainerRunner, ContainerSpec
from workers.isolation import (
    DOCKER_SOCKET_PATHS,
    DockerSocketLeakError,
    assert_no_docker_socket,
    build_hardened_run_kwargs,
)

import docker

from ._docker_helpers import (
    BASE_IMAGE,
    docker_client,
    ensure_base_image,
    last_json_line,
    requires_docker,
)

pytestmark = [pytest.mark.integration, requires_docker]

_SOCKET_PROBE = r"""
import json, os

print(json.dumps({
    "var_run_sock": os.path.exists("/var/run/docker.sock"),
    "run_sock": os.path.exists("/run/docker.sock"),
}))
"""


@pytest.fixture(scope="module", autouse=True)
def _base_image() -> None:
    client = docker_client()
    try:
        ensure_base_image(client)
    finally:
        client.close()


def test_docker_socket_is_absent_inside_the_container() -> None:
    result = AgentContainerRunner(Settings()).run(
        ContainerSpec(image=BASE_IMAGE, command=["python", "-c", _SOCKET_PROBE])
    )
    assert result.exit_code == 0, result.logs
    report = last_json_line(result.logs)
    assert report["var_run_sock"] is False
    assert report["run_sock"] is False


def test_hardened_run_has_no_bind_mounts_at_all() -> None:
    # Defense in depth: a tmpfs workspace means zero bind mounts, so
    # there is nowhere the socket could even be smuggled in.
    kwargs = build_hardened_run_kwargs(Settings())
    assert not kwargs.get("mounts")
    assert not kwargs.get("volumes")
    assert_no_docker_socket(kwargs)  # must not raise


def test_inspect_of_a_real_run_shows_no_socket_bind() -> None:
    result = AgentContainerRunner(Settings()).run(
        ContainerSpec(image=BASE_IMAGE, command=["python", "-c", "print('ok')"])
    )
    binds = result.host_config.get("Binds") or []
    mounts = result.host_config.get("Mounts") or []
    assert all("docker.sock" not in str(bind) for bind in binds)
    assert all("docker.sock" not in str(mount) for mount in mounts)


def test_tripwire_catches_a_socket_mount() -> None:
    bad = {
        "mounts": [
            docker.types.Mount(
                target="/var/run/docker.sock",
                source="/var/run/docker.sock",
                type="bind",
            )
        ]
    }
    with pytest.raises(DockerSocketLeakError):
        assert_no_docker_socket(bad)


def test_tripwire_catches_a_socket_volume_dict() -> None:
    bad = {"volumes": {"/var/run/docker.sock": {"bind": "/var/run/docker.sock", "mode": "ro"}}}
    with pytest.raises(DockerSocketLeakError):
        assert_no_docker_socket(bad)


def test_tripwire_catches_the_windows_named_pipe() -> None:
    bad = {"volumes": ["//./pipe/docker_engine://./pipe/docker_engine"]}
    with pytest.raises(DockerSocketLeakError):
        assert_no_docker_socket(bad)


def test_tripwire_allows_a_clean_run_config() -> None:
    clean = {"mounts": [docker.types.Mount(target="/workspace", source="/data/ws", type="bind")]}
    assert_no_docker_socket(clean)  # must not raise


def test_socket_paths_constant_covers_both_unix_locations() -> None:
    assert "/var/run/docker.sock" in DOCKER_SOCKET_PATHS
    assert "/run/docker.sock" in DOCKER_SOCKET_PATHS
