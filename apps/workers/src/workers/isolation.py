"""Hardened Docker run configuration for agent-runtime containers
(task_02_07 / task_02_09).

Every agent runs inside a container the worker launches with this exact
lockdown — there is no opt-out path. The principles (CLAUDE.md §2):

  * cap-drop ALL + no-new-privileges — zero Linux capabilities, and the
    process can never regain privilege through a setuid binary.
  * read-only root filesystem — only /workspace and /tmp are writable,
    both as size-capped tmpfs (or a bind for /workspace once the worker
    hands the agent a real worktree, Plan 06).
  * Docker default-deny seccomp — we never pass `seccomp=unconfined`, so
    the daemon's SCMP_ACT_ERRNO profile stays in force. A stricter custom
    profile can be pinned via WORKERS_SECCOMP_PROFILE (see ADR 0012).
  * a dedicated, internal network — no host, no platform services
    (Postgres/Redis/Vault), no inter-container traffic.
  * non-root user (uid/gid 1000).
  * pids + memory limits — a fork bomb or a leak cannot reach the host.
  * the Docker socket is NEVER mounted — `assert_no_docker_socket` is the
    defense-in-depth tripwire the runner calls before every launch.

See ADR 0012 — Aislamiento de contenedores agent-runtime.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docker.types import Mount
from workers.config import Settings

# The two filesystem locations the Docker daemon socket lives at, plus
# the Windows named pipe — anything resembling these must never be a
# mount source/target for an agent container.
DOCKER_SOCKET_PATHS: tuple[str, ...] = ("/var/run/docker.sock", "/run/docker.sock")

# uid:gid the agent process runs as — matches the `agent` user baked
# into the agent-runtime image.
AGENT_UID_GID = "1000:1000"


class DockerSocketLeakError(RuntimeError):
    """Raised when a run config would expose the Docker socket to an agent."""


def _looks_like_docker_socket(value: str) -> bool:
    """True for any path/pipe that grants access to the Docker daemon."""
    normalised = value.replace("\\", "/").lower()
    return "docker.sock" in normalised or "docker_engine" in normalised


def assert_no_docker_socket(run_kwargs: dict[str, Any]) -> None:
    """Tripwire: raise if `run_kwargs` would bind the Docker socket.

    A container that can reach the Docker socket can trivially escape to
    the host (mount /, start a privileged sibling, ...). The worker calls
    this before every launch so a future careless edit cannot silently
    re-introduce the socket.
    """
    candidates: list[str] = []

    volumes = run_kwargs.get("volumes")
    if isinstance(volumes, dict):
        for host, spec in volumes.items():
            candidates.append(str(host))
            if isinstance(spec, dict):
                candidates.append(str(spec.get("bind", "")))
    elif isinstance(volumes, list | tuple):
        candidates.extend(str(v) for v in volumes)

    mounts = run_kwargs.get("mounts")
    if isinstance(mounts, list | tuple):
        for mount in mounts:
            if isinstance(mount, dict):  # docker.types.Mount is a dict
                candidates.append(str(mount.get("Source", "")))
                candidates.append(str(mount.get("Target", "")))
            else:
                candidates.append(str(mount))

    leaks = sorted({c for c in candidates if c and _looks_like_docker_socket(c)})
    if leaks:
        raise DockerSocketLeakError(
            "agent container would expose the Docker socket: " + ", ".join(leaks)
        )


def build_hardened_run_kwargs(
    settings: Settings,
    *,
    workspace_host_path: str | None = None,
) -> dict[str, Any]:
    """Build the locked-down kwargs for `docker.containers.run`.

    When `workspace_host_path` is given, /workspace is a read-write bind
    to that host directory; otherwise it is an ephemeral tmpfs. Either
    way the container's root filesystem stays read-only.
    """
    security_opt = ["no-new-privileges:true"]

    seccomp = settings.seccomp_profile_path.strip()
    if seccomp:
        # The Docker SDK forwards the profile *content*, not the path —
        # read it here so the daemon never needs the file.
        security_opt.append("seccomp=" + Path(seccomp).read_text(encoding="utf-8"))

    apparmor = settings.apparmor_profile.strip()
    if apparmor:
        security_opt.append("apparmor=" + apparmor)

    # HOME is the CLI's own size-capped tmpfs OUTSIDE /workspace. The Claude Code
    # CLI writes its config (.claude.json ~25KB, .claude/) into HOME; with
    # HOME=/workspace that landed in the agent's project worktree and the agent
    # read it back, polluting every model_call's context. nosuid like the rest;
    # NOT noexec (the CLI may exec from its own cache), matching /workspace.
    agent_home = "/home/agent"
    tmpfs = {
        "/tmp": f"rw,noexec,nosuid,size={settings.container_tmp_size}",
        agent_home: f"rw,nosuid,size={settings.container_home_size},uid=1000,gid=1000",
    }

    kwargs: dict[str, Any] = {
        "cap_drop": ["ALL"],
        "security_opt": security_opt,
        "read_only": True,
        "network": settings.agent_network,
        "mem_limit": settings.container_mem_limit,
        "pids_limit": settings.container_pids_limit,
        "user": AGENT_UID_GID,
        "working_dir": "/workspace",
        "environment": {"HOME": agent_home, "PYTHONDONTWRITEBYTECODE": "1"},
    }

    if workspace_host_path:
        kwargs["mounts"] = [
            Mount(target="/workspace", source=workspace_host_path, type="bind", read_only=False)
        ]
    else:
        tmpfs["/workspace"] = (
            f"rw,nosuid,size={settings.container_workspace_size},uid=1000,gid=1000"
        )

    kwargs["tmpfs"] = tmpfs
    return kwargs
