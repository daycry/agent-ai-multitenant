"""Integration tests: agent containers run under the strict isolation
profile (task_02_07).

A single hardened probe container reports its own sandbox — uid,
capabilities, seccomp mode, no-new-privileges, writable mounts — and the
tests assert on that plus the `docker inspect` snapshot the runner
captures.
"""

from __future__ import annotations

from typing import Any

import pytest
from workers.config import Settings
from workers.container import AgentContainerRunner, ContainerResult, ContainerSpec
from workers.isolation import build_hardened_run_kwargs

from ._docker_helpers import (
    BASE_IMAGE,
    docker_client,
    ensure_base_image,
    last_json_line,
    requires_docker,
)

pytestmark = [pytest.mark.integration, requires_docker]

# Runs inside the hardened container and prints a JSON report of its
# own sandbox.
_PROBE = r"""
import json, os


def can_write(path):
    try:
        with open(path, "w") as handle:
            handle.write("x")
        os.unlink(path)
        return True
    except OSError:
        return False


status = {}
with open("/proc/self/status") as handle:
    for line in handle:
        key, _, value = line.partition(":")
        status[key.strip()] = value.strip()

print(json.dumps({
    "uid": os.getuid(),
    "cap_eff": status.get("CapEff"),
    "seccomp": status.get("Seccomp"),
    "no_new_privs": status.get("NoNewPrivs"),
    "write_workspace": can_write("/workspace/probe"),
    "write_tmp": can_write("/tmp/probe"),
    "write_etc": can_write("/etc/probe"),
    "write_root": can_write("/probe"),
}))
"""


@pytest.fixture(scope="module")
def probe() -> ContainerResult:
    client = docker_client()
    try:
        ensure_base_image(client)
    finally:
        client.close()
    result = AgentContainerRunner(Settings()).run(
        ContainerSpec(image=BASE_IMAGE, command=["python", "-c", _PROBE])
    )
    assert result.exit_code == 0, result.logs
    return result


@pytest.fixture(scope="module")
def report(probe: ContainerResult) -> dict[str, Any]:
    return last_json_line(probe.logs)


def test_runs_as_a_non_root_user(report: dict[str, Any]) -> None:
    assert report["uid"] == 1000


def test_all_capabilities_are_dropped(report: dict[str, Any]) -> None:
    # CapEff is a hex mask; cap-drop ALL on a non-root user => all zeros.
    assert set(report["cap_eff"]) == {"0"}


def test_seccomp_is_engaged(report: dict[str, Any]) -> None:
    # 2 == SECCOMP_MODE_FILTER — Docker's default-deny profile is active.
    assert report["seccomp"] == "2"


def test_no_new_privileges_is_set(report: dict[str, Any]) -> None:
    assert report["no_new_privs"] == "1"


def test_root_filesystem_is_read_only(report: dict[str, Any]) -> None:
    assert report["write_etc"] is False
    assert report["write_root"] is False


def test_workspace_and_tmp_are_writable(report: dict[str, Any]) -> None:
    assert report["write_workspace"] is True
    assert report["write_tmp"] is True


def test_inspect_confirms_the_lockdown(probe: ContainerResult) -> None:
    host_config = probe.host_config
    assert host_config.get("ReadonlyRootfs") is True
    assert host_config.get("CapDrop") == ["ALL"]
    assert host_config.get("PidsLimit") == 256
    assert host_config.get("Privileged") is False
    security_opt = host_config.get("SecurityOpt") or []
    assert any("no-new-privileges" in opt for opt in security_opt)


def test_runs_on_the_dedicated_network(probe: ContainerResult) -> None:
    assert probe.networks == ("agentic-agents",)
    assert "bridge" not in probe.networks
    assert "host" not in probe.networks


def test_hardened_kwargs_never_disable_seccomp() -> None:
    kwargs = build_hardened_run_kwargs(Settings())
    assert "unconfined" not in " ".join(kwargs["security_opt"])


def test_hardened_kwargs_use_a_tmpfs_workspace_by_default() -> None:
    kwargs = build_hardened_run_kwargs(Settings())
    assert "/workspace" in kwargs["tmpfs"]
    assert kwargs["read_only"] is True
    assert "mounts" not in kwargs


def test_hardened_kwargs_bind_workspace_when_a_host_path_is_given() -> None:
    kwargs = build_hardened_run_kwargs(Settings(), workspace_host_path="/data/ws")
    assert "/workspace" not in kwargs["tmpfs"]
    assert len(kwargs["mounts"]) == 1


def test_hardened_kwargs_workspace_is_rw_by_default() -> None:
    # An implementer run binds /workspace read-write so its file writes persist.
    kwargs = build_hardened_run_kwargs(Settings(), workspace_host_path="/data/ws")
    assert kwargs["mounts"][0]["ReadOnly"] is False


def test_hardened_kwargs_workspace_read_only_when_requested() -> None:
    # ADR 0095: a REVIEW run mounts the implementer's worktree READ-ONLY so the
    # reviewer can read the code without mutating it.
    kwargs = build_hardened_run_kwargs(
        Settings(), workspace_host_path="/data/ws", workspace_read_only=True
    )
    assert kwargs["mounts"][0]["ReadOnly"] is True
