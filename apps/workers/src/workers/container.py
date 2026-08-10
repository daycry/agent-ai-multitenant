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

import structlog

import docker
from workers.config import Settings
from workers.isolation import assert_no_docker_socket, build_hardened_run_kwargs

_log = structlog.get_logger(__name__)

# `task_wf_56`: techo del drenaje del pump de logs. Generoso porque el caso
# normal termina en milisegundos —el stream cierra solo al salir el
# contenedor—; existe para que un daemon colgado deje rastro en vez de
# inmovilizar el worker indefinidamente.
_PUMP_DRAIN_TIMEOUT_S = 120.0

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
    # When set, /workspace is a bind to this host directory; otherwise
    # /workspace is an ephemeral tmpfs.
    workspace_host_path: str | None = None
    # ADR 0095: mount /workspace read-only (a REVIEW run reads the
    # implementer's worktree without mutating it). RW by default.
    workspace_read_only: bool = False
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
    # `task_wf_62`: el DIGEST de la imagen que realmente corrió, no la etiqueta
    # con la que se pidió. `agent-runtime-php-phpunit:v1` se reconstruye y
    # cambia en silencio lo que ejecuta toda tarea PHP; sin esto no hay forma de
    # saber qué build produjo un resultado ni de volver a la anterior.
    # `None` cuando el daemon no lo reporta (nunca impide el run).
    image_digest: str | None = None

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


# prod-07 task_prod07_10: `config_env` es una copia del entorno del contenedor
# que sobrevive al contenedor — viaja en el `ContainerResult`, y de ahí a los
# asertos de aislamiento y a cualquier volcado de diagnóstico. La credencial del
# proveedor ya no está en el env, pero el token interno del agente SÍ, y el día
# que alguien añada otra variable sensible este filtro es lo que decide si acaba
# en un log. Regla por SUFIJO del nombre y no lista cerrada: una lista cerrada
# protege lo que ya existe, y el problema son las variables que aún no existen.
_SENSITIVE_ENV_SUFFIXES = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL", "CREDENTIALS")
_REDACTED = "***"


def _scrub_env(env: tuple[str, ...]) -> tuple[str, ...]:
    """El env capturado con los valores sensibles redactados.

    Se conserva el NOMBRE (saber que la variable estaba puesta es justo lo que se
    diagnostica con esto) y se tira el valor. Una entrada sin ``=`` se deja tal
    cual: no es un par, y adivinar sería peor.
    """
    scrubbed: list[str] = []
    for entry in env:
        name, sep, _value = entry.partition("=")
        if sep and name.upper().endswith(_SENSITIVE_ENV_SUFFIXES):
            scrubbed.append(f"{name}={_REDACTED}")
        else:
            scrubbed.append(entry)
    return tuple(scrubbed)


def _image_digest(attrs: dict[str, Any]) -> str | None:
    """El digest de la imagen que este contenedor corrió DE VERDAD (`task_wf_62`).

    Se lee del propio `inspect` del contenedor —el campo ``Image``, que el
    daemon rellena con el id resuelto— en vez de preguntar por la etiqueta
    después: entre el lanzamiento y la consulta la etiqueta puede haberse
    reasignado a otra build, y entonces se registraría la imagen equivocada
    justamente en el caso que esta trazabilidad existe para detectar.

    `None` si el daemon no lo reporta. Es información de trazabilidad: su
    ausencia no puede impedir un run ni cambiar su resultado.
    """
    image_id = attrs.get("Image")
    return str(image_id) if image_id else None


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
            self._settings,
            workspace_host_path=spec.workspace_host_path,
            workspace_read_only=spec.workspace_read_only,
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
            #
            # `task_wf_56`: pero con techo. El `join()` sin timeout apuesta a que
            # el stream SIEMPRE cierra; si el daemon se cuelga o la conexión se
            # queda a medias, el worker se queda ahí para siempre con el slot de
            # la cola ocupado y sin un solo log que lo explique. El techo es
            # generoso a propósito —el caso normal drena en milisegundos— y lo
            # que aporta no es cortar antes, es que la anomalía DEJE RASTRO.
            pump.join(timeout=_PUMP_DRAIN_TIMEOUT_S)
            if pump.is_alive():
                _log.warning(
                    "workers.container.log_pump_did_not_drain",
                    execution_id=spec.labels.get("com.agentic-platform.execution-id"),
                    timeout_s=_PUMP_DRAIN_TIMEOUT_S,
                    detail=(
                        "el stream de logs no cerró tras salir el contenedor; "
                        "la cola del log en vivo puede estar incompleta"
                    ),
                )
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

    def list_exited_managed(self) -> list[tuple[str, str]]:
        """(container_id, execution_id) de los contenedores gestionados en estado
        ``exited``. F0.6 (auditoría 2026-07-02): ``run_streamed`` solo limpia su
        contenedor si el proceso worker sigue vivo, y ``kill_by_label`` solo mata
        running — los exited de runs superseded/crasheados se acumulaban en un
        host que duerme a diario. Best-effort: lista vacía si Docker no responde."""
        exited: list[tuple[str, str]] = []
        with contextlib.suppress(Exception):
            containers = self.client.containers.list(
                all=True,
                filters={"label": "com.agentic-platform.managed=true", "status": "exited"},
            )
            for container in containers:
                execution_id = (container.labels or {}).get("com.agentic-platform.execution-id", "")
                exited.append((container.id, execution_id))
        return exited

    def remove_container(self, container_id: str) -> bool:
        """Elimina un contenedor por id (force). Best-effort e idempotente."""
        with contextlib.suppress(Exception):
            self.client.containers.get(container_id).remove(force=True)
            return True
        return False

    def list_managed_execution_ids(self) -> set[str] | None:
        """Execution-ids con contenedor gestionado EXISTENTE (cualquier estado).

        Sweep de huérfanos (2026-07-03): una fila ``running`` cuyo contenedor ya
        NO EXISTE (engine-restart, `docker rm` externo) no puede terminar jamás —
        el umbral de 7 h del sweep la dejaba horas de zombi vetando el
        re-despacho de su task. Este listado (una sola llamada al daemon) permite
        detectarlas al momento. Devuelve ``None`` si Docker no responde — el
        caller debe distinguir «daemon caído» (no barrer nada) de «sin
        contenedores» (set vacío legítimo)."""
        try:
            containers = self.client.containers.list(
                all=True,
                filters={"label": "com.agentic-platform.managed=true"},
            )
        except Exception:
            return None
        ids: set[str] = set()
        for container in containers:
            execution_id = (container.labels or {}).get("com.agentic-platform.execution-id", "")
            if execution_id:
                ids.add(execution_id)
        return ids

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
        out. Returns True if it had to be killed.

        R1: a container that VANISHES mid-run is TERMINAL, not a timeout. When a
        runtime crashes at startup its ``--rm`` removes it, so ``reload()`` 404s
        (``docker.errors.NotFound``). Previously that 404 was suppressed and the
        loop polled the ghost on ``GET /containers/<id>/json`` until the whole
        per-provider budget elapsed (huge for claude_sdk) — hanging the worker
        (its beat + reconciler froze). Treat NotFound as "exited" at once; only a
        transient ``APIError`` is retried.
        """
        deadline = time.monotonic() + budget
        while True:
            try:
                container.reload()
            except docker.errors.NotFound:
                # Gone from the daemon — crashed + auto-removed. Terminal, and not
                # a wall-clock kill, so the caller finalises it as a failed run.
                return False
            except docker.errors.APIError:
                pass  # transient daemon hiccup — retry on the next tick
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
        """Read logs + a trimmed inspect snapshot before removal.

        R1: a container that vanished mid-run (``--rm`` after a startup crash)
        makes ``logs()`` 404. Fall back to a minimal result (exit_code -1, empty
        logs) so the run finalises ``failed`` ("exited with no result") instead of
        the NotFound propagating and crashing the worker thread.
        """
        try:
            raw_logs = container.logs(stdout=True, stderr=True)
        except docker.errors.NotFound:
            return ContainerResult(
                container_id=str(getattr(container, "id", "") or ""),
                exit_code=-1,
                logs="",
                timed_out=timed_out,
                host_config={},
                config_env=(),
                networks=(),
            )
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
            config_env=_scrub_env(tuple(config.get("Env") or ())),
            networks=tuple(net.keys()),
            image_digest=_image_digest(attrs),
        )
