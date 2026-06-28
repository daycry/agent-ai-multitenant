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
import threading
import time
from collections.abc import Callable
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

        La red sigue `internal` (sin egress directo al host ni a
        internet). ICC habilitado para que el agente pueda alcanzar al
        `egress-proxy` cuando éste está en la misma red — ADR 0019 /
        task_02_35. El aislamiento del sandbox sigue viviendo en el
        perfil endurecido (cap-drop, FS read-only, seccomp, sin socket
        Docker), no en la red.
        """
        name = self._settings.agent_network
        try:
            self.client.networks.get(name)
        except docker.errors.NotFound:
            self.client.networks.create(
                name,
                driver="bridge",
                internal=self._settings.agent_network_internal,
                options={"com.docker.network.bridge.enable_icc": "true"},
                labels=dict(_BASE_LABELS),
            )
        return name

    def _start(self, spec: ContainerSpec) -> Any:
        """Apply the hardened isolation profile and launch `spec` detached."""
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

        # ADR 0019 / task_02_35: si hay un egress-proxy configurado,
        # los clientes HTTP del agente lo usan transparentemente vía las
        # variables estándar. Sin proxy configurado, el sandbox queda
        # sin red de salida — sólo el ScriptedModelClient funciona.
        proxy_url = self._settings.egress_proxy_url
        if proxy_url:
            for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
                environment.setdefault(key, proxy_url)

        return self.client.containers.run(
            spec.image,
            command=spec.command,
            environment=environment,
            name=spec.name,
            labels={**_BASE_LABELS, **spec.labels},
            detach=True,
            **kwargs,
        )

    def run(self, spec: ContainerSpec, *, timeout: int | None = None) -> ContainerResult:
        """Launch `spec`, wait for it, and return the captured result.

        The container is always removed afterwards — even on timeout or
        error — so a crashed run cannot leak a container onto the host.
        """
        budget = timeout if timeout is not None else self._settings.container_run_timeout_s
        container = self._start(spec)
        try:
            timed_out = self._await_exit(container, budget)
            return self._capture(container, timed_out=timed_out)
        finally:
            with contextlib.suppress(Exception):
                container.remove(force=True)

    def run_streamed(
        self,
        spec: ContainerSpec,
        on_line: Callable[[str], None],
        *,
        timeout: int | None = None,
    ) -> ContainerResult:
        """Launch `spec` and call `on_line` with each stdout/stderr line
        as it is produced, then return the captured result.

        The worker (task_02_30) uses this to forward an agent-runtime's
        JSON step stream onto the per-execution Redis stream live —
        rather than waiting for the run to finish. A background thread
        pumps the log stream while the main thread runs the same
        wall-clock poll loop as `run()`; the container is always reaped.
        """
        budget = timeout if timeout is not None else self._settings.container_run_timeout_s
        container = self._start(spec)
        pump = threading.Thread(target=self._pump_logs, args=(container, on_line), daemon=True)
        try:
            pump.start()
            timed_out = self._await_exit(container, budget)
            # F17/P1.1: capture the *complete* logs + inspect snapshot BEFORE
            # the container is removed. `.logs` is the authoritative record the
            # worker falls back on, so it must never be truncated by teardown.
            result = self._capture(container, timed_out=timed_out)
            # The follow stream closes (EOF) once the container has exited or
            # been killed, so the pump terminates on its own. Drain it fully —
            # NOT on a short timeout — before reaping: a cut mid-read drops the
            # live tail (e.g. the final `execution.finished` line) for the UI.
            pump.join()
            return result
        finally:
            with contextlib.suppress(Exception):
                container.remove(force=True)

    def kill_by_label(self, execution_id: str) -> int:
        """Force-kill any container tagged with ``execution_id`` (cooperative
        cancellation). Killing the container makes the in-flight ``run_streamed``
        exit, so the worker can finalise the row as ``cancelled``. It is the
        container — not the worker process — that burns LLM budget, so this is
        the authoritative stop. Best-effort and idempotent; returns the count
        killed (0 if already gone). Reused by the zombie-container sweeper."""
        label = f"com.agentic-platform.execution-id={execution_id}"
        killed = 0
        with contextlib.suppress(Exception):
            for container in self.client.containers.list(filters={"label": label}):
                with contextlib.suppress(Exception):
                    container.kill()
                killed += 1
        return killed

    @staticmethod
    def _pump_logs(container: Any, on_line: Callable[[str], None]) -> None:
        """Forward the container's STDOUT (the structured JSON channel) line by
        line to ``on_line``, live.

        F21/P1.2: the agent-runtime emits ALL its structured events
        (``execution.started`` / ``step`` / ``execution.finished`` /
        ``execution.error`` …) as JSON lines on STDOUT (``_emit`` →
        ``print(..., flush=True)``); free-text library noise goes to STDERR. We
        follow STDOUT ALONE so a newline-less stderr fragment can never splice
        into a JSON stdout line (the corruption F21 named). We read it WITHOUT
        ``demux``: a demultiplexed ``follow`` stream delivered NO live lines on the
        daemon (the regression that left the per-execution Redis stream empty),
        and the structured channel is single-stream anyway. STDERR is still
        captured in full by ``_capture`` into ``ContainerResult.logs`` for the
        audit record + the worker's fallback re-parse.

        Best-effort: a streaming hiccup is swallowed — ``_capture`` reads the
        complete logs afterwards, so the persisted record loses nothing even if
        the live tail drops a line.
        """
        buffer = b""
        with contextlib.suppress(Exception):
            for chunk in container.logs(stream=True, follow=True, stdout=True, stderr=False):
                if chunk:
                    buffer = AgentContainerRunner._emit_lines(buffer + chunk, on_line)
        # Flush any newline-less tail.
        tail = buffer.decode("utf-8", errors="replace").strip()
        if tail:
            with contextlib.suppress(Exception):
                on_line(tail)

    @staticmethod
    def _emit_lines(buffer: bytes, on_line: Callable[[str], None]) -> bytes:
        """Emit every complete `\\n`-terminated line in `buffer`, returning the
        unterminated remainder to carry into the next chunk."""
        while b"\n" in buffer:
            raw, buffer = buffer.split(b"\n", 1)
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                on_line(line)
        return buffer

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
