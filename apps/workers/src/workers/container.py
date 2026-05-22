"""Launching agent-runtime containers through the Docker SDK (task_02_06).

The worker's one job in Plan 02 Fase B: take a `ContainerSpec`, launch
it under the hardened isolation profile, wait for it to finish (or kill
it past its wall-clock budget), and return the captured `ContainerResult`.

This is the *simple* one-container-per-task model (Plan 02 §Alcance);
the elastic per-plan pool with worktree reuse arrives in Plan 06. The
LangGraph agent loop that decides what the container *does* is wired
inside the image in Fase C — here we only orchestrate the sandbox.
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

import docker
from workers.config import Settings
from workers.isolation import assert_no_docker_socket, build_hardened_run_kwargs

# How often the run loop polls a container's state. Agent runs last
# seconds-to-minutes, so a sub-second poll is plenty responsive without
# hammering the daemon.
_POLL_INTERVAL_S = 0.25

# Labels stamped on every container the worker launches — makes orphans
# easy to find and reap.
_BASE_LABELS = {
    "com.agentic-platform.component": "agent-runtime",
    "com.agentic-platform.managed": "true",
}


@dataclass(frozen=True)
class ContainerSpec:
    """What to run inside an agent container."""

    image: str
    command: list[str] | None = None
    env: dict[str, str] = field(default_factory=dict)
    # When set, /workspace is a read-write bind to this host directory;
    # otherwise /workspace is an ephemeral tmpfs.
    workspace_host_path: str | None = None
    # Extra read-only mounts — e.g. staged secrets (see workers.secrets).
    extra_mounts: tuple[Any, ...] = ()
    name: str | None = None
    labels: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ContainerResult:
    """The outcome of one container run."""

    container_id: str
    exit_code: int
    logs: str
    timed_out: bool
    # Trimmed `docker inspect` output, captured before the container is
    # removed — lets callers (and tests) assert on the applied sandbox.
    host_config: dict[str, Any]
    config_env: tuple[str, ...]
    networks: tuple[str, ...]

    def succeeded(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def as_dict(self) -> dict[str, Any]:
        """JSON-safe summary — this is what the Celery result backend
        stores, so it stays small (no full inspect payload)."""
        return {
            "container_id": self.container_id,
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "logs": self.logs,
            "networks": list(self.networks),
        }


class AgentContainerRunner:
    """Launches and supervises a single agent-runtime container."""

    def __init__(self, settings: Settings, *, client: Any = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def client(self) -> Any:
        """Lazily-built Docker client — `docker.from_env()` is only
        touched when a container is actually launched."""
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def ensure_network(self) -> str:
        """Create the dedicated agent network if it does not exist yet.

        The network is `internal` (no egress) with inter-container
        communication disabled — an agent can reach neither the platform
        services nor a sibling agent.
        """
        name = self._settings.agent_network
        try:
            self.client.networks.get(name)
        except docker.errors.NotFound:
            self.client.networks.create(
                name,
                driver="bridge",
                internal=self._settings.agent_network_internal,
                options={"com.docker.network.bridge.enable_icc": "false"},
                labels=dict(_BASE_LABELS),
            )
        return name

    def run(self, spec: ContainerSpec, *, timeout: int | None = None) -> ContainerResult:
        """Launch `spec`, wait for it, and return the captured result.

        The container is always removed afterwards — even on timeout or
        error — so a crashed run cannot leak a container onto the host.
        """
        budget = timeout if timeout is not None else self._settings.container_run_timeout_s
        self.ensure_network()

        kwargs = build_hardened_run_kwargs(
            self._settings, workspace_host_path=spec.workspace_host_path
        )
        mounts = list(kwargs.pop("mounts", []))
        mounts.extend(spec.extra_mounts)
        if mounts:
            kwargs["mounts"] = mounts

        # Tripwire: never let the Docker socket reach an agent.
        assert_no_docker_socket(kwargs)

        environment = {**kwargs.pop("environment", {}), **spec.env}

        container = self.client.containers.run(
            spec.image,
            command=spec.command,
            environment=environment,
            name=spec.name,
            labels={**_BASE_LABELS, **spec.labels},
            detach=True,
            **kwargs,
        )
        try:
            timed_out = self._await_exit(container, budget)
            return self._capture(container, timed_out=timed_out)
        finally:
            with contextlib.suppress(Exception):
                container.remove(force=True)

    @staticmethod
    def _await_exit(container: Any, budget: int) -> bool:
        """Poll until the container exits or the wall-clock budget runs
        out. Returns True if it had to be killed."""
        deadline = time.monotonic() + budget
        while True:
            with contextlib.suppress(docker.errors.APIError):
                container.reload()
            if container.status in ("exited", "dead"):
                return False
            if time.monotonic() >= deadline:
                with contextlib.suppress(docker.errors.APIError):
                    container.kill()
                with contextlib.suppress(docker.errors.APIError):
                    container.reload()
                return True
            time.sleep(_POLL_INTERVAL_S)

    @staticmethod
    def _capture(container: Any, *, timed_out: bool) -> ContainerResult:
        """Read logs + a trimmed inspect snapshot before removal."""
        raw_logs = container.logs(stdout=True, stderr=True)
        logs = raw_logs.decode("utf-8", errors="replace") if isinstance(raw_logs, bytes) else ""

        attrs = container.attrs or {}
        state = attrs.get("State") or {}
        config = attrs.get("Config") or {}
        net = (attrs.get("NetworkSettings") or {}).get("Networks") or {}

        return ContainerResult(
            container_id=str(container.id),
            exit_code=int(state.get("ExitCode", -1)),
            logs=logs,
            timed_out=timed_out,
            host_config=dict(attrs.get("HostConfig") or {}),
            config_env=tuple(config.get("Env") or ()),
            networks=tuple(net.keys()),
        )
