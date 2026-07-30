"""Post-install execution sandbox (Plan 09 task_09_06).

After a listing is downloaded and statically analysed (task_09_05), the
install flow runs the listing's **smoke check** — a tiny "does this thing
even start?" probe — before the install is trusted. That probe runs
arbitrary third-party code, so it MUST NOT run in the api-server process.
It runs inside an *ephemeral hardened container* that is torn down the
moment the probe finishes.

This module is the policy + orchestration layer for that container. It
deliberately reuses the platform isolation pattern that the test-runtime
worker (:mod:`workers.test_runtime` / :mod:`workers.isolation`) already
applies — the same lockdown the .docx mandates for every container that
runs user/third-party code (CLAUDE.md §2):

  * **cap-drop ALL** — zero Linux capabilities.
  * **security_opt no-new-privileges** — the process can never regain
    privilege through a setuid binary.
  * **read-only root filesystem** — only a size-capped ``/tmp`` (and the
    read-only workspace mount) are present; nothing the probe writes
    survives, and it cannot tamper with its own image.
  * **network policy honored, NUNCA NAT crudo** (ADR 0094 / mitad
    marketplace de task_prod12_net_01) — el bridge es SIEMPRE ``internal``.
    ``none`` (sin egress, el default y la única opción sana para el primer
    run de un listing *experimental*), ``restricted`` (bridge interno —
    el egress a los ``allowed_domains`` consentidos lo impone el proxy de
    la plataforma, nunca entregando internet al contenedor) y ``open``
    (bridge interno + el ``registry-proxy`` allowlistado conectado al
    bridge e inyectado como ``HTTP(S)_PROXY`` — solo alcanzable por
    consentimiento por-permiso explícito; sin proxy configurado, ``open``
    queda OFFLINE, jamás NAT crudo).
  * **mem_limit + pids_limit** — a leak or a fork-bomb cannot exhaust the
    host.
  * **the Docker socket is NEVER mounted** — :func:`assert_no_docker_socket`
    is the defence-in-depth tripwire the runner calls before every launch,
    mirroring :func:`workers.isolation.assert_no_docker_socket`.

**Why a self-contained copy of the isolation helpers** rather than
importing :mod:`workers.isolation`? The api-server package does not depend
on the ``workers`` package (different deployable, different ``pyproject``),
and ``docker`` is not a *required* api-server dependency — it is present in
the dev venv but absent from a minimal api-server install. So this module:

  * imports ``docker`` **lazily** (only inside the real-launch path), so
    importing :mod:`api_server.marketplace.sandbox` never requires the
    ``docker`` wheel — the SandboxSpec builder + result handling are pure
    Python and import anywhere (the xmlsec / semgrep precedent);
  * lets the caller **inject** a Docker client (the tests inject a mock),
    so the spec construction, the hardening flags, the network-policy
    wiring, the timeout, the result capture, and the always-runs teardown
    are all unit-testable with the daemon mocked.

A real-container run against a live daemon is an integration step pending
the sandbox runtime image; the unit tests here pin the SPEC + result
handling with the client mocked.
"""

from __future__ import annotations

import contextlib
import os
import secrets
from dataclasses import dataclass, field
from typing import Any

import structlog

from api_server.marketplace.trust import NetworkPolicy

_log = structlog.get_logger("marketplace.sandbox")

# Defaults del registry-proxy (ADR 0094) — los mismos convenios del compose
# que usa el worker (workers.config): el servicio compartido allowlistado y
# su alias DNS en el bridge per-sandbox. Sobrescribibles por entorno; vacíos
# = 'open' queda offline (nunca NAT crudo).
DEFAULT_REGISTRY_PROXY_URL = os.environ.get("REGISTRY_PROXY_URL", "http://registry-proxy:8888")
DEFAULT_REGISTRY_PROXY_CONTAINER = os.environ.get(
    "REGISTRY_PROXY_CONTAINER", "agentic-registry-proxy"
)
DEFAULT_REGISTRY_PROXY_ALIAS = os.environ.get("REGISTRY_PROXY_ALIAS", "registry-proxy")

# Labels stamped on every container/network the sandbox launches, so the
# same reaper that sweeps the test-runtime can sweep a leaked sandbox.
SANDBOX_LABELS: dict[str, str] = {
    "com.agentic-platform.component": "marketplace-sandbox",
    "com.agentic-platform.managed": "true",
}

# uid:gid the probe runs as — never root. Matches the platform's
# convention (workers.isolation.AGENT_UID_GID).
SANDBOX_UID_GID = "1000:1000"

# Defaults — conservative on purpose. A smoke check is a fast "does it
# start?" probe; anything past these caps is almost certainly a hang, a
# leak, or a fork-bomb and the sandbox kills it.
DEFAULT_SANDBOX_TIMEOUT_S = 60
DEFAULT_SANDBOX_MEM_LIMIT = "256m"
DEFAULT_SANDBOX_PIDS_LIMIT = 128
DEFAULT_SANDBOX_CPU = 1.0
DEFAULT_TMPFS_SIZE = "64m"

# Hard cap on captured log size so a chatty / malicious probe cannot blow
# up the api-server's memory or the audit row. stdout/stderr beyond this
# is truncated with a marker.
MAX_CAPTURED_LOG_BYTES = 64 * 1024

# Conventional "killed by timeout" exit code from GNU ``timeout`` / our
# wrapper. Mirrors workers.test_runtime's use of 124.
TIMEOUT_EXIT_CODE = 124

# The two filesystem locations the Docker daemon socket lives at, plus the
# Windows named pipe — anything resembling these must never be a mount
# source/target for a sandbox container. Mirrors
# workers.isolation.DOCKER_SOCKET_PATHS.
DOCKER_SOCKET_PATHS: tuple[str, ...] = ("/var/run/docker.sock", "/run/docker.sock")


class SandboxError(RuntimeError):
    """The sandbox could not establish a verdict (daemon error, launch
    failure, unparseable result).

    Distinct from a *smoke-check failure* — a probe that runs and exits
    non-zero is a typed :class:`SandboxResult` with ``passed is False``,
    NOT an exception. ``SandboxError`` means we could not even run the
    probe, so the caller MUST fail closed (treat the listing as unsafe).
    """


class DockerSocketLeakError(SandboxError):
    """Raised when a sandbox run config would expose the Docker socket.

    A container that can reach the Docker socket can trivially escape to
    the host. Subclasses :class:`SandboxError` so the install flow's
    fail-closed ``except SandboxError`` also catches a socket leak.
    """


def _looks_like_docker_socket(value: str) -> bool:
    """True for any path/pipe that grants access to the Docker daemon."""
    normalised = value.replace("\\", "/").lower()
    return "docker.sock" in normalised or "docker_engine" in normalised


def assert_no_docker_socket(run_kwargs: dict[str, Any]) -> None:
    """Tripwire: raise if ``run_kwargs`` would bind the Docker socket.

    Called before every launch so a future careless edit cannot silently
    re-introduce the socket into a sandbox container. Inspects both the
    ``volumes`` dict/list form and the ``mounts`` list of
    :class:`docker.types.Mount` (which are dicts with ``Source`` /
    ``Target`` keys).
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
            "sandbox container would expose the Docker socket: " + ", ".join(leaks)
        )


@dataclass(frozen=True, slots=True)
class SandboxSpec:
    """Everything the sandbox needs to run ONE smoke check.

    ``frozen`` + ``slots`` so a spec is immutable and cheap. The probe is
    ``smoke_command`` run via ``sh -c`` inside the container; the listing's
    consented permissions (plan decision (c)) map onto the network policy +
    allowed domains here.
    """

    # The runtime image the smoke check runs in (e.g. the python-pytest or
    # node-playwright runtime template image, or a listing-specific image).
    image: str
    # The smoke check itself — a shell command. Defaults to a trivial
    # "did the container start?" probe so a spec is always runnable.
    smoke_command: str = "true"

    # Network egress posture for the probe. ``none`` (default) is the only
    # safe choice for an experimental listing's first run; ``restricted`` /
    # ``open`` are only ever reached through explicit per-permission consent.
    network_policy: NetworkPolicy = NetworkPolicy.NONE
    # The domains the project owner consented to (plan decision (c)). Only
    # meaningful under ``restricted``; surfaced to the egress proxy, never
    # by handing the container the open internet.
    allowed_domains: tuple[str, ...] = ()

    # Optional read-only workspace mount: the downloaded listing source, so
    # the probe can import/run it. Always mounted read-only — the probe
    # must not be able to mutate the artifact it is testing.
    workspace_host_path: str | None = None
    workspace_mount_path: str = "/workspace"

    # Resource caps (a leak / fork-bomb cannot reach the host).
    mem_limit: str = DEFAULT_SANDBOX_MEM_LIMIT
    pids_limit: int = DEFAULT_SANDBOX_PIDS_LIMIT
    cpu: float = DEFAULT_SANDBOX_CPU
    tmpfs_size: str = DEFAULT_TMPFS_SIZE

    # Wall-clock cap for the smoke check. Past this the probe is killed and
    # the result is ``timed_out``.
    timeout_s: int = DEFAULT_SANDBOX_TIMEOUT_S

    # Extra environment for the probe (never secrets — those stay in Vault).
    env: dict[str, str] = field(default_factory=dict)

    def is_egress_allowed(self) -> bool:
        """True when the probe gets PROXIED egress (only ``open``).

        Desde ADR 0094 / task_prod12_net_01 esto ya NO significa bridge
        no-interno: el bridge es siempre interno y ``open`` se materializa
        conectando el ``registry-proxy`` allowlistado al bridge (más el env
        ``HTTP(S)_PROXY``). ``restricted`` sigue interno y offline aquí (el
        enforcement por-dominio del proxy de plataforma es upstream).
        """
        return self.network_policy == NetworkPolicy.OPEN


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """Typed outcome of one :meth:`MarketplaceSandbox.run` call.

    A *failed smoke check* is this record with ``passed is False`` (NOT an
    exception) — the install flow records it in the audit log and blocks.
    A non-recoverable launch error is a :class:`SandboxError` instead.

    ``stdout`` / ``stderr`` are already truncated to
    :data:`MAX_CAPTURED_LOG_BYTES`; ``truncated`` flags when that happened
    so the caller knows the logs are partial.
    """

    smoke_command: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    truncated: bool = False
    container_id: str = ""
    network_name: str = ""
    # task_prod12_net_01: la política de red efectiva del run y si el egress
    # fue proxificado (registry-proxy conectado) — el install flow lo pliega
    # en el audit row, de modo que cada uso de 'open' queda registrado.
    network_policy: str = ""
    proxied_egress: bool = False

    @property
    def passed(self) -> bool:
        """``True`` only when the probe ran to completion with rc 0.

        A timeout is a failure even though the exit code may be the
        conventional 124 — we never trust a probe we had to kill.
        """
        return not self.timed_out and self.exit_code == 0


def _truncate(raw: bytes) -> tuple[str, bool]:
    """Decode + cap log bytes, returning ``(text, was_truncated)``."""
    truncated = len(raw) > MAX_CAPTURED_LOG_BYTES
    clipped = raw[:MAX_CAPTURED_LOG_BYTES]
    text = clipped.decode("utf-8", errors="replace")
    if truncated:
        text += "\n…[truncated]…"
    return text, truncated


def _shell_quote(command: str) -> str:
    """Single-quote a command for safe embedding inside ``sh -c``.

    Mirrors workers.test_runtime._shell_quote so the sandbox and the
    test-runtime quote identically."""
    return "'" + command.replace("'", "'\"'\"'") + "'"


def build_sandbox_run_kwargs(
    spec: SandboxSpec, network_name: str, *, proxy_url: str | None = None
) -> dict[str, Any]:
    """Build the hardened ``docker.containers.run`` kwargs for the probe.

    Extracted as a module-level helper (the test-runtime precedent) so the
    hardening envelope is testable without a live daemon: assert cap_drop
    ALL + no-new-privileges + read-only root + mem/pids/cpu caps + the
    private bridge + no docker socket. The container is started detached
    sleeping; the smoke command runs via ``exec_run`` so the runner can
    register the container for teardown BEFORE executing anything.

    ``proxy_url`` (solo bajo ``open`` con registry-proxy conectado) inyecta
    ``HTTP(S)_PROXY`` para que el egress del probe atraviese la allowlist —
    el mismo cableado que el test-runtime hace para ``dep_egress`` (ADR 0094).
    """
    env: dict[str, str] = {"HOME": spec.workspace_mount_path, **dict(spec.env)}
    if proxy_url:
        env.update(
            {
                "HTTP_PROXY": proxy_url,
                "HTTPS_PROXY": proxy_url,
                "http_proxy": proxy_url,
                "https_proxy": proxy_url,
                "NO_PROXY": "localhost,127.0.0.1",
            }
        )

    mounts: list[Any] = []
    if spec.workspace_host_path:
        # Lazy import of docker.types so importing this module — and the
        # common no-mount smoke check — never needs the docker wheel
        # (capability-gap honesty). Only the workspace-mount path needs it.
        from docker.types import Mount

        mounts.append(
            Mount(
                target=spec.workspace_mount_path,
                source=spec.workspace_host_path,
                type="bind",
                # Read-only: the probe must NOT mutate the artifact under test.
                read_only=True,
            )
        )

    kwargs: dict[str, Any] = {
        # Keep the container alive so we can exec the probe with a timeout
        # wrapper and capture rc + logs deterministically.
        "command": ["sleep", "infinity"],
        "detach": True,
        "network": network_name,
        "environment": env,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "read_only": True,
        "tmpfs": {"/tmp": f"rw,noexec,nosuid,size={spec.tmpfs_size}"},
        "user": SANDBOX_UID_GID,
        # nano_cpus rather than --cpus so the value round-trips through JSON
        # as an int (matches workers.test_runtime).
        "nano_cpus": int(spec.cpu * 1_000_000_000),
        "mem_limit": spec.mem_limit,
        "pids_limit": spec.pids_limit,
        "labels": {
            **SANDBOX_LABELS,
            "com.agentic-platform.network-policy": str(spec.network_policy),
        },
    }
    if mounts:
        kwargs["mounts"] = mounts
    return kwargs


class MarketplaceSandbox:
    """Runs a listing's smoke check in an ephemeral hardened container.

    Stateless and cheap to construct. The single entry point
    :meth:`run` creates a one-shot private bridge, launches the probe
    container with the hardened envelope, execs the smoke command with a
    wall-clock cap, captures the (truncated) result, and ALWAYS tears the
    container + network down — even on failure / timeout / daemon error.

    The Docker client is injected so the whole flow is unit-testable with
    the daemon mocked; on a real deployment the client is resolved lazily
    via ``docker.from_env()``.
    """

    def __init__(
        self,
        *,
        client: Any = None,
        registry_proxy_url: str | None = None,
        registry_proxy_container: str | None = None,
        registry_proxy_alias: str | None = None,
    ) -> None:
        self._client = client
        # ADR 0094 / task_prod12_net_01: el único egress posible es el
        # registry-proxy allowlistado. None → defaults del compose; "" →
        # explícitamente sin proxy ('open' queda offline).
        self._registry_proxy_url = (
            DEFAULT_REGISTRY_PROXY_URL if registry_proxy_url is None else registry_proxy_url
        )
        self._registry_proxy_container = (
            DEFAULT_REGISTRY_PROXY_CONTAINER
            if registry_proxy_container is None
            else registry_proxy_container
        )
        self._registry_proxy_alias = registry_proxy_alias or DEFAULT_REGISTRY_PROXY_ALIAS

    @property
    def client(self) -> Any:
        if self._client is None:
            # Lazy: a minimal api-server install without the docker wheel
            # can still import this module; only a real run needs it.
            import docker

            self._client = docker.from_env()
        return self._client

    # --- public ---------------------------------------------------------
    def run(self, spec: SandboxSpec) -> SandboxResult:
        """Run ``spec``'s smoke check and return a typed :class:`SandboxResult`.

        Raises :class:`SandboxError` only when the probe could NOT be run
        (network/container launch failure) — the caller fails closed. A
        probe that runs and exits non-zero, or that we had to kill on
        timeout, is a non-exceptional :class:`SandboxResult` with
        ``passed is False``.
        """
        network = self._create_bridge()
        network_name = getattr(network, "name", "") or ""
        container: Any = None
        proxy: Any = None
        try:
            if spec.is_egress_allowed():
                # 'open' = egress PROXIFICADO por la allowlist, nunca NAT
                # crudo (ADR 0094). Cada uso queda en el log estructurado y
                # en el resultado (→ audit row del install flow).
                proxy = self._attach_registry_proxy(network)
                _log.info(
                    "marketplace.sandbox.egress",
                    network_policy=str(spec.network_policy),
                    proxied=proxy is not None,
                )
            container = self._start(spec, network_name, proxied=proxy is not None)
            exit_code, stdout, stderr, timed_out, truncated = self._exec_smoke(spec, container)
            result = SandboxResult(
                smoke_command=spec.smoke_command,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                timed_out=timed_out,
                truncated=truncated,
                container_id=getattr(container, "id", "") or "",
                network_name=network_name,
                network_policy=str(spec.network_policy),
                proxied_egress=proxy is not None,
            )
            _log.info(
                "marketplace.sandbox.done",
                network_policy=str(spec.network_policy),
                exit_code=result.exit_code,
                timed_out=result.timed_out,
                passed=result.passed,
            )
            return result
        finally:
            self._cleanup(container, network, proxy=proxy)

    # --- bridge ---------------------------------------------------------
    def _create_bridge(self) -> Any:
        """Create a one-shot bridge for this probe.

        ``internal=True`` SIEMPRE (ADR 0094 D1 / task_prod12_net_01): el
        bridge nunca recibe NAT crudo, tampoco bajo ``open`` — el egress de
        ``open`` es el registry-proxy conectado al bridge. The bridge name
        is randomised so two concurrent sandboxes never collide.
        """
        suffix = secrets.token_hex(4)
        name = f"marketplace-sandbox-{suffix}"
        try:
            return self.client.networks.create(
                name,
                driver="bridge",
                internal=True,
                labels=dict(SANDBOX_LABELS),
            )
        except Exception as exc:  # docker.errors.APIError et al.
            raise SandboxError(f"could not create sandbox network: {exc}") from exc

    def _attach_registry_proxy(self, network: Any) -> Any:
        """Conecta el registry-proxy allowlistado al bridge interno del probe
        (mismo patrón que workers.test_runtime._attach_registry_proxy).

        Devuelve el contenedor del proxy, o ``None`` cuando no está
        configurado/alcanzable — el probe queda OFFLINE (fail-safe: nunca se
        compensa con NAT crudo). El proxy es un servicio compartido: aquí
        solo se CONECTA; el teardown lo desconecta y jamás lo borra."""
        if not self._registry_proxy_url or not self._registry_proxy_container:
            _log.warning(
                "marketplace.sandbox.egress_unconfigured",
                detail="registry proxy url/container unset; probe stays offline",
            )
            return None
        try:
            proxy = self.client.containers.get(self._registry_proxy_container)
        except Exception as exc:  # NotFound / APIError — proxy not running
            _log.warning(
                "marketplace.sandbox.registry_proxy_unavailable",
                container=self._registry_proxy_container,
                error=str(exc),
            )
            return None
        network.connect(proxy, aliases=[self._registry_proxy_alias])
        return proxy

    # --- launch ---------------------------------------------------------
    def _start(self, spec: SandboxSpec, network_name: str, *, proxied: bool = False) -> Any:
        """Launch the probe container (sleeping; no smoke command yet)."""
        run_kwargs = build_sandbox_run_kwargs(
            spec, network_name, proxy_url=self._registry_proxy_url if proxied else None
        )
        # Defence-in-depth tripwire — never expose the daemon socket.
        assert_no_docker_socket(run_kwargs)
        try:
            return self.client.containers.run(spec.image, **run_kwargs)
        except Exception as exc:  # docker.errors.APIError / ImageNotFound
            raise SandboxError(f"could not start sandbox container: {exc}") from exc

    def _exec_smoke(self, spec: SandboxSpec, container: Any) -> tuple[int, str, str, bool, bool]:
        """Exec the smoke command with a wall-clock cap, capture rc + logs.

        ``timeout_s`` is enforced by wrapping the command in GNU
        ``timeout`` (``exec_run`` itself has no timeout) — a wedged probe
        cannot run forever. ``timeout`` exits 124 on kill; we surface that
        as ``timed_out``. We capture stdout and stderr separately
        (``demux=True``) so the caller can distinguish them, truncating
        both to :data:`MAX_CAPTURED_LOG_BYTES`.
        """
        wrapped = f"timeout {spec.timeout_s} sh -c {_shell_quote(spec.smoke_command)}"
        try:
            result = container.exec_run(["sh", "-c", wrapped], demux=True)
        except Exception as exc:  # daemon dropped the exec mid-run
            raise SandboxError(f"sandbox exec failed: {exc}") from exc

        exit_code = int(getattr(result, "exit_code", 0) or 0)
        output = getattr(result, "output", (b"", b""))
        # demux=True → (stdout_bytes, stderr_bytes); either may be None.
        if isinstance(output, tuple):
            out_bytes = output[0] or b""
            err_bytes = output[1] or b""
        else:  # demux ignored by some clients → single stream
            out_bytes = output or b""
            err_bytes = b""

        stdout, out_trunc = _truncate(out_bytes)
        stderr, err_trunc = _truncate(err_bytes)
        timed_out = exit_code == TIMEOUT_EXIT_CODE
        return exit_code, stdout, stderr, timed_out, (out_trunc or err_trunc)

    # --- cleanup --------------------------------------------------------
    def _cleanup(self, container: Any, network: Any, *, proxy: Any = None) -> None:
        """Tear down the container + network. ALWAYS runs (finally).

        Every step is best-effort and swallowed: a teardown that itself
        raises must not mask the real result/error from :meth:`run`. El
        registry-proxy compartido se DESCONECTA primero (nunca se borra) —
        con un endpoint aún conectado, ``network.remove()`` fallaría.
        """
        if proxy is not None and network is not None:
            with contextlib.suppress(Exception):
                network.disconnect(proxy, force=True)
        if container is not None:
            with contextlib.suppress(Exception):
                container.remove(force=True)
        if network is not None:
            with contextlib.suppress(Exception):
                network.remove()


__all__ = [
    "DEFAULT_SANDBOX_CPU",
    "DEFAULT_SANDBOX_MEM_LIMIT",
    "DEFAULT_SANDBOX_PIDS_LIMIT",
    "DEFAULT_SANDBOX_TIMEOUT_S",
    "DOCKER_SOCKET_PATHS",
    "MAX_CAPTURED_LOG_BYTES",
    "SANDBOX_LABELS",
    "SANDBOX_UID_GID",
    "TIMEOUT_EXIT_CODE",
    "DockerSocketLeakError",
    "MarketplaceSandbox",
    "SandboxError",
    "SandboxResult",
    "SandboxSpec",
    "assert_no_docker_socket",
    "build_sandbox_run_kwargs",
]
