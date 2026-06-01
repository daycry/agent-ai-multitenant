"""Worker-test: orchestrate test-runtime containers per task (Plan 06 Fase B).

This is the heterogeneous-stacks brother of ``container.py``. While the
agent-runtime container runs the LangGraph loop, the **test-runtime**
container runs *only* the acceptance-criteria commands the agent's
output is supposed to satisfy. Different stacks → different runtimes
(python-pytest, node-jest, php-phpunit, …), all resolved through
:mod:`shared_test_runtimes.catalog`.

The four tasks of Fase B all live here:

  * ``group_tasks_by_runtime`` (task_06_04) — read each task's
    ``acceptance_criteria`` list, keep only ``check_type='automated'``
    entries, and group them by runtime. Each :class:`RuntimePlan`
    becomes one container launch downstream.
  * :class:`TestRuntimeRunner.launch` (task_06_05) — wire the
    template's image + worktree mount + dep-cache mount + aux network
    + ephemeral compose into ``docker.containers.run``, with the same
    hardened envelope ``container.py`` uses (cap-drop ALL, no-new-priv,
    read-only root, non-root uid, ``network=none`` by default).
  * :class:`AuxServiceSpec` + :meth:`TestRuntimeRunner.compose_aux`
    (task_06_06) — describe the postgres-test / redis-test sidecars
    each project can opt into, run them on the task's private bridge
    network, and tear them down at end-of-task.
  * :class:`TestcontainersMode` (task_06_07) — opt-in path that proxies
    Docker API calls through a dedicated DinD socket-proxy container
    (the test container talks to a *restricted* DOCKER_HOST, NEVER the
    host's ``/var/run/docker.sock``).

Implementation note: ``container.py``'s ``AgentContainerRunner`` exists
for *one container per task*. ``TestRuntimeRunner`` exists for *one
container per (task, runtime) pair*, with siblings for aux services
sharing an ephemeral bridge. We keep them separate so the hardening
profile each one applies stays explicit.
"""

from __future__ import annotations

import contextlib
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import structlog
from shared_test_runtimes.catalog import get as get_template
from shared_test_runtimes.types import RuntimeTemplate

import docker
from docker.types import Mount
from workers.config import Settings
from workers.isolation import (
    AGENT_UID_GID,
    DockerSocketLeakError,
    assert_no_docker_socket,
)

_log = structlog.get_logger("workers.test_runtime")

# Labels stamped on every container/network the test-runtime launches.
# Mirrors container.py's _BASE_LABELS so the same reaper sweeps both.
_TEST_LABELS: dict[str, str] = {
    "com.agentic-platform.component": "test-runtime",
    "com.agentic-platform.managed": "true",
}

# Default test-runtime wall-clock cap. Tests longer than this almost
# always indicate a hung process, not legitimate work; the project can
# override per task via ``acceptance_criteria[*].timeout_s``.
DEFAULT_TIMEOUT_S = 600

# ---------------------------------------------------------------------------
# task_06_04 — Grouping
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcceptanceCheck:
    """One ``acceptance_criteria`` entry, normalised.

    The DB column is ``list[Any]`` so projects can pass arbitrary
    extra fields; we only care about a closed set here. Anything we
    don't recognise stays in ``raw`` for the parser/reporter to use.
    """

    id: str
    description: str
    runtime: str
    command: str
    expected_signal: str = "exit_code == 0"
    timeout_s: int | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RuntimePlan:
    """One ``(runtime, [checks])`` group ready to launch.

    The worker creates one ``TestRuntimeRunner.launch`` call per
    :class:`RuntimePlan` — different runtimes can run in parallel, but
    checks of the same runtime go through the same container to save
    pre_install cost.
    """

    template: RuntimeTemplate
    checks: tuple[AcceptanceCheck, ...]


def _coerce_check(entry: Mapping[str, Any]) -> AcceptanceCheck | None:
    """Best-effort coercion of one acceptance_criteria dict.

    Returns ``None`` when the entry is missing required fields or is a
    non-automated check (manual / human). The caller logs these as
    "skipped" so the user sees them in the worker output."""
    if entry.get("check_type", "automated") != "automated":
        return None
    runtime = entry.get("runtime")
    command = entry.get("command")
    if not runtime or not command:
        return None
    return AcceptanceCheck(
        id=str(entry.get("id") or ""),
        description=str(entry.get("description") or ""),
        runtime=str(runtime),
        command=str(command),
        expected_signal=str(entry.get("expected_signal") or "exit_code == 0"),
        timeout_s=int(entry["timeout_s"]) if entry.get("timeout_s") is not None else None,
        raw=dict(entry),
    )


def group_tasks_by_runtime(
    acceptance_criteria: Iterable[Mapping[str, Any]],
) -> tuple[RuntimePlan, ...]:
    """Group automated acceptance checks by their declared runtime.

    The catalog (:mod:`shared_test_runtimes.catalog`) resolves the
    runtime id to a :class:`RuntimeTemplate`. Unknown runtimes raise
    :class:`KeyError` — the caller is expected to surface this as a
    422 to the user (their task config references a runtime we don't
    ship).

    Plans are returned in the order their runtime first appears in the
    input. That makes the worker's launch order deterministic and
    matches what the user reads in the UI.
    """
    by_runtime: dict[str, list[AcceptanceCheck]] = {}
    for entry in acceptance_criteria:
        check = _coerce_check(entry)
        if check is None:
            continue
        by_runtime.setdefault(check.runtime, []).append(check)

    plans: list[RuntimePlan] = []
    for runtime_id, checks in by_runtime.items():
        template = get_template(runtime_id)
        plans.append(RuntimePlan(template=template, checks=tuple(checks)))
    return tuple(plans)


# ---------------------------------------------------------------------------
# task_06_16_03 — run_* runtime resolution by project stack
# ---------------------------------------------------------------------------

# The runtime the ``run_*`` docker_command tools fall back to when neither the
# project nor the tool pins one. ``run_pytest`` ships with
# ``implementation_ref='python-pytest'`` and the other three (``run_lint`` /
# ``run_typecheck`` / ``run_build``) ship with no ``implementation_ref`` at
# all — this constant is the single, backward-compatible default that keeps
# existing Python projects running pytest in ``python-pytest`` exactly as
# before Plan 06.16.
DEFAULT_RUN_RUNTIME_ID = "python-pytest"


class RuntimeResolutionError(ValueError):
    """A ``run_*`` tool referenced a runtime template we don't ship.

    Raised by :func:`resolve_run_runtime` when the resolved id (the
    project's ``default_runtime_template`` or the tool's
    ``implementation_ref``) is not in :mod:`shared_test_runtimes.catalog`.
    A subclass of :class:`ValueError` so existing ``except ValueError``
    boot-time handlers still catch it, while the message names the
    offending id + the known set so the operator sees a *clear* error
    instead of a bare ``KeyError`` crash.
    """


def resolve_run_runtime_id(
    *,
    project_default_runtime: str | None,
    tool_default_runtime: str | None,
) -> str:
    """Pick the runtime template id a ``run_*`` tool should execute in.

    Precedence (Plan 06.16 task_06_16_03):

      1. ``project_default_runtime`` (``projects.default_runtime_template``)
         when the project pins a stack — a PHP project with
         ``php-phpunit`` runs its ``run_*`` there, not in ``python-pytest``.
      2. the tool's own ``implementation_ref`` default when the project
         pins nothing (NULL) — e.g. ``run_pytest`` → ``python-pytest``.
      3. :data:`DEFAULT_RUN_RUNTIME_ID` as the final fallback for the
         ``run_*`` tools that carry no ``implementation_ref`` at all
         (``run_lint`` / ``run_typecheck`` / ``run_build``).

    Empty strings are treated as "unset" (the chips/UI never sends a tidy
    value). The returned id is NOT validated against the catalog here —
    use :func:`resolve_run_runtime` when you need the resolved template.
    """
    for candidate in (project_default_runtime, tool_default_runtime):
        if candidate and candidate.strip():
            return candidate.strip()
    return DEFAULT_RUN_RUNTIME_ID


def resolve_run_runtime(
    *,
    project_default_runtime: str | None,
    tool_default_runtime: str | None,
) -> RuntimeTemplate:
    """Resolve a ``run_*`` tool's :class:`RuntimeTemplate` from the stack.

    Combines :func:`resolve_run_runtime_id` (precedence: project default →
    tool default → ``python-pytest``) with the catalog lookup. An
    unknown/invalid id surfaces as a :class:`RuntimeResolutionError` with
    the known set spelled out — a clear error the operator can act on,
    never a bare ``KeyError`` taking the boot path down.
    """
    runtime_id = resolve_run_runtime_id(
        project_default_runtime=project_default_runtime,
        tool_default_runtime=tool_default_runtime,
    )
    try:
        return get_template(runtime_id)
    except KeyError as exc:
        # ``catalog.get`` already formats "unknown runtime template 'x';
        # known: a, b, …" — reuse that message verbatim so the operator
        # sees the same wording the rest of the platform uses.
        raise RuntimeResolutionError(str(exc).strip("\"'")) from exc


def resolve_run_runtime_image(
    project_default_runtime: str | None,
    tool_default_runtime: str | None,
) -> str:
    """Resolve a ``run_*`` tool's docker image from the project stack.

    The ``(project_default, tool_default) → image`` adapter the worker
    injects into the agent-runtime's ``tool_wiring.WiringContext`` as its
    ``runtime_image_resolver`` (Plan 06.16 task_06_16_03). Keeping the
    catalog lookup here means the agent-runtime never imports
    :mod:`shared_test_runtimes`. Raises :class:`RuntimeResolutionError`
    (a clear error) on an unknown/invalid runtime id.
    """
    template = resolve_run_runtime(
        project_default_runtime=project_default_runtime,
        tool_default_runtime=tool_default_runtime,
    )
    image: str = template.docker_image
    return image


# ---------------------------------------------------------------------------
# task_06_06 — Auxiliary services (postgres-test, redis-test, …)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuxServiceSpec:
    """One service the test-runtime can talk to over the private bridge.

    Defaults match the .docx's "postgres-test / redis-test
    parametrizables por proyecto" requirement: the worker spawns these
    on demand, alias-aliased inside the bridge so the test code can
    reach them via stable hostnames (``postgres-test``, ``redis-test``).
    """

    name: str
    image: str
    # Optional alias inside the bridge network. Defaults to ``name``.
    alias: str | None = None
    env: Mapping[str, str] = field(default_factory=dict)
    # Healthcheck command run via ``docker exec`` after start. None
    # disables the wait — useful for short-lived helpers.
    healthcheck_cmd: tuple[str, ...] | None = None
    # Maximum seconds we'll poll the healthcheck before giving up.
    healthcheck_timeout_s: int = 30
    # Hardening caps (task_06_14_11 / container-isolation-1). When None
    # the runner falls back to the operator-tunable Settings defaults
    # (``aux_postgres_mem_limit`` / ``aux_redis_mem_limit`` /
    # ``aux_default_pids_limit``). Even a transient sidecar on the
    # private bridge gets cap-drop ALL + no-new-privileges + these caps
    # so a leak or fork-bomb cannot reach the host (CLAUDE.md §2).
    mem_limit: str | None = None
    pids_limit: int | None = None

    def resolved_alias(self) -> str:
        return self.alias or self.name


# Curated defaults for the two stacks every project asks for. The
# worker accepts user-provided AuxServiceSpec lists; these are just
# the names we register by default through `default_aux_services()`.
DEFAULT_POSTGRES = AuxServiceSpec(
    name="postgres-test",
    image="postgres:16-alpine",
    env={
        "POSTGRES_USER": "test",
        "POSTGRES_PASSWORD": "test",
        "POSTGRES_DB": "test",
        "POSTGRES_INITDB_ARGS": "--encoding=UTF8",
    },
    healthcheck_cmd=("pg_isready", "-U", "test", "-d", "test"),
    # Postgres needs a touch more headroom than redis for shared_buffers.
    mem_limit="256m",
)

DEFAULT_REDIS = AuxServiceSpec(
    name="redis-test",
    image="redis:7-alpine",
    healthcheck_cmd=("redis-cli", "ping"),
    mem_limit="128m",
)


def default_aux_services() -> tuple[AuxServiceSpec, ...]:
    """The two services every project gets by default."""
    return (DEFAULT_POSTGRES, DEFAULT_REDIS)


# The redis-test stack is the one we recognise by name when an aux spec
# leaves ``mem_limit`` unset, so we can pick the right operator default.
_REDIS_MEM_HINT = "redis"

# Common lockdown applied to every aux sidecar AND the DinD proxy: zero
# Linux capabilities + no privilege escalation through setuid binaries.
# Mirrors :func:`isolation.build_hardened_run_kwargs` (same principles,
# CLAUDE.md §2) without the read-only root / non-root uid bits, which the
# stateful sidecars (postgres/redis write their data dirs as root) can't
# take. The resource caps are what bound a runaway / fork-bomb.
_AUX_SECURITY_OPT = ["no-new-privileges:true"]


def build_aux_run_kwargs(
    settings: Settings,
    aux: AuxServiceSpec,
    network_name: str,
) -> dict[str, Any]:
    """Build the hardened ``docker.containers.run`` kwargs for one aux service.

    Extracted as a module-level helper (task_06_14_11) so the hardening
    envelope is testable the same way ``isolation.build_hardened_run_kwargs``
    is — assert cap_drop ALL + no-new-privileges + mem/pids caps without a
    live daemon. The mem/pids caps fall back to the operator-tunable
    Settings when the spec leaves them unset; the per-spec values
    (``DEFAULT_POSTGRES`` 256m / ``DEFAULT_REDIS`` 128m) win when present.
    """
    if aux.mem_limit is not None:
        mem_limit = aux.mem_limit
    elif _REDIS_MEM_HINT in aux.image.lower() or _REDIS_MEM_HINT in aux.name.lower():
        mem_limit = settings.aux_redis_mem_limit
    else:
        mem_limit = settings.aux_postgres_mem_limit
    pids_limit = aux.pids_limit if aux.pids_limit is not None else settings.aux_default_pids_limit
    return {
        "detach": True,
        "environment": dict(aux.env),
        "network": network_name,
        "network_mode": None,
        "hostname": aux.resolved_alias(),
        "cap_drop": ["ALL"],
        "security_opt": list(_AUX_SECURITY_OPT),
        "mem_limit": mem_limit,
        "pids_limit": pids_limit,
        "labels": {**_TEST_LABELS, "com.agentic-platform.role": "aux-service"},
    }


def build_dind_proxy_run_kwargs(
    settings: Settings,
    mode: TestcontainersMode,
    network_name: str,
) -> dict[str, Any]:
    """Build the hardened kwargs for the DinD socket-proxy sidecar.

    The proxy already had cap_drop ALL + read-only root + no-new-privileges;
    task_06_14_11 (container-isolation-2) adds the missing mem/pids caps so a
    misbehaving testcontainer cannot exhaust the host through the proxy. The
    host docker.sock bind onto the *proxy only* (never the test container)
    stays exactly as before — see :class:`TestcontainersMode`.
    """
    return {
        "detach": True,
        "environment": dict(mode.acl),
        "network": network_name,
        "hostname": mode.proxy_alias(),
        "mounts": [
            Mount(
                target="/var/run/docker.sock",
                source="/var/run/docker.sock",
                type="bind",
                read_only=False,
            )
        ],
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": list(_AUX_SECURITY_OPT),
        "mem_limit": settings.dind_proxy_mem_limit,
        "pids_limit": settings.dind_proxy_pids_limit,
        "labels": {**_TEST_LABELS, "com.agentic-platform.role": "dind-proxy"},
    }


# ---------------------------------------------------------------------------
# task_06_07 — Testcontainers opt-in mode
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestcontainersMode:
    """Opt-in: the test-runtime gets a DinD-proxy at ``DOCKER_HOST``.

    Standard testcontainers libraries (java, node, python) need to
    talk to a Docker daemon. We **never** mount ``/var/run/docker.sock``
    into the test container — that would defeat the entire isolation
    story (a container with daemon access trivially escapes to the
    host). Instead, when ``enabled=True``, the worker:

      1. Spawns a docker-socket-proxy sidecar on the task's private
         bridge with a hardened ACL (``CONTAINERS=1, IMAGES=1, ...``
         only — no ``EXEC``, no ``VOLUMES``, no host network).
      2. Exposes its tcp port to the test-runtime as
         ``DOCKER_HOST=tcp://docker-proxy:2375``.
      3. Tears down the proxy at end-of-task.

    This isn't bulletproof — a sufficiently determined testcontainer
    *can* DOS by spawning runaway sibling containers — but it bounds
    the attack surface (no ``--privileged``, no socket on host fs, no
    host network) to "noisy neighbour", not "escape".
    """

    enabled: bool = False
    # Image of the socket-proxy. Defaults to the well-maintained
    # tecnativa one; projects can pin a specific tag for reproducibility.
    proxy_image: str = "tecnativa/docker-socket-proxy:0.3.0"
    # ACL — what subset of the Docker API the proxy exposes. The defaults
    # are the smallest set testcontainers needs (CONTAINERS lets it spin
    # one up, IMAGES lets it pull). EXEC and VOLUMES are *not* in here on
    # purpose — turning them on without thought is the canonical way to
    # void the sandbox.
    acl: Mapping[str, str] = field(
        default_factory=lambda: {
            "CONTAINERS": "1",
            "IMAGES": "1",
            "NETWORKS": "1",
            "POST": "1",
            "EXEC": "0",
            "VOLUMES": "0",
            "INFO": "1",
            "PING": "1",
            "VERSION": "1",
        }
    )

    def proxy_alias(self) -> str:
        return "docker-proxy"

    def docker_host_url(self) -> str:
        return f"tcp://{self.proxy_alias()}:2375"


# ---------------------------------------------------------------------------
# task_06_05 — Launching the test-runtime
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TestRuntimeSpec:
    """Everything the runner needs for one container launch."""

    plan: RuntimePlan
    # Host path of the task's worktree. Mounted at
    # ``plan.template.workspace_mount_path`` (default /workspace).
    worktree_host_path: str
    # Host path of the (shared) dep-cache. Only mounted if the template
    # declares ``dep_cache_mount``.
    dep_cache_host_path: str | None = None
    # Aux services to bring up on the task's bridge.
    aux_services: tuple[AuxServiceSpec, ...] = ()
    # Opt-in DinD proxy. Disabled by default — projects with
    # testcontainers tests turn this on per-task.
    testcontainers: TestcontainersMode = field(default_factory=TestcontainersMode)
    # Override the template's default cpu/memory caps.
    cpu: float | None = None
    memory_mb: int | None = None
    # Override the template's default network policy.
    network_policy: str | None = None


@dataclass(frozen=True)
class TestRuntimeResult:
    """Outcome of one :meth:`TestRuntimeRunner.launch` call.

    The per-check breakdown is what feeds Plan 06 Fase D's TestReport.
    ``logs`` is the *concatenated* stdout/stderr of every check command
    in the order they ran.
    """

    runtime: str
    exit_codes: tuple[int, ...]
    logs: str
    container_id: str
    timed_out: bool
    network_name: str

    def all_passed(self) -> bool:
        return not self.timed_out and all(rc == 0 for rc in self.exit_codes)


class TestRuntimeRunner:
    """Launches one test-runtime per :class:`RuntimePlan`.

    The runner owns the *Docker side* — creating the task's private
    bridge, starting aux services, starting the optional DinD proxy,
    starting the main test container, executing each check command in
    sequence, then tearing the whole compose down. It deliberately
    does NOT own the parsing of test output: that's task_06_14's job
    (each runtime's output parsers).
    """

    def __init__(self, settings: Settings, *, client: Any = None) -> None:
        self._settings = settings
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    # --- public ---------------------------------------------------------

    def launch(self, spec: TestRuntimeSpec) -> TestRuntimeResult:
        """Launch the test-runtime for one :class:`RuntimePlan`.

        Always tears down every container + network it created, even
        on failure / timeout. The bridge name is randomised so two
        concurrent tasks on the same worker host never share a network.
        """
        network = self._create_bridge(spec)
        aux_containers: list[Any] = []
        proxy_container: Any = None
        main_container: Any = None
        try:
            aux_containers = self._start_aux_services(spec, network.name)
            if spec.testcontainers.enabled:
                proxy_container = self._start_dind_proxy(spec, network.name)
            main_container = self._start_main(spec, network.name)
            exit_codes, combined_logs, timed_out = self._run_checks(spec, main_container)
            return TestRuntimeResult(
                runtime=spec.plan.template.id,
                exit_codes=tuple(exit_codes),
                logs=combined_logs,
                container_id=getattr(main_container, "id", "") or "",
                timed_out=timed_out,
                network_name=network.name,
            )
        finally:
            self._cleanup(main_container, proxy_container, aux_containers, network)

    # --- bridge ---------------------------------------------------------

    def _create_bridge(self, spec: TestRuntimeSpec) -> Any:
        """Create a one-shot internal bridge for this task.

        ``internal=True`` removes egress to the host's default gateway;
        the only connectivity the test container has is to the
        sidecars sharing the bridge. When the template asks for
        ``network_policy='open'`` the caller is expected to override
        through Settings — we don't honor it silently here."""
        policy = spec.network_policy or spec.plan.template.network_policy
        suffix = secrets.token_hex(4)
        name = f"test-runtime-{spec.plan.template.id}-{suffix}"
        return self.client.networks.create(
            name,
            driver="bridge",
            internal=policy != "open",
            labels=dict(_TEST_LABELS),
        )

    # --- aux services ---------------------------------------------------

    def _start_aux_services(
        self,
        spec: TestRuntimeSpec,
        network_name: str,
    ) -> list[Any]:
        """Bring up each aux service on the task's bridge.

        Each sidecar gets the hardened envelope (cap_drop ALL +
        no-new-privileges + mem/pids caps) via
        :func:`build_aux_run_kwargs` — task_06_14_11."""
        started: list[Any] = []
        for aux in spec.aux_services:
            run_kwargs = build_aux_run_kwargs(self._settings, aux, network_name)
            container = self.client.containers.run(aux.image, **run_kwargs)
            started.append(container)
            if aux.healthcheck_cmd is not None:
                self._wait_healthy(container, aux)
        return started

    def _wait_healthy(self, container: Any, aux: AuxServiceSpec) -> None:
        """Poll ``healthcheck_cmd`` until it returns 0 or we time out."""
        import time

        if aux.healthcheck_cmd is None:
            return
        cmd = list(aux.healthcheck_cmd)
        deadline = time.monotonic() + aux.healthcheck_timeout_s
        last_rc: int | None = None
        while time.monotonic() < deadline:
            exec_result = container.exec_run(cmd)
            last_rc = getattr(exec_result, "exit_code", None)
            if last_rc == 0:
                return
            time.sleep(0.5)
        raise RuntimeError(
            f"aux service {aux.name!r} did not become healthy within "
            f"{aux.healthcheck_timeout_s}s (last rc={last_rc})"
        )

    # --- DinD proxy -----------------------------------------------------

    def _start_dind_proxy(self, spec: TestRuntimeSpec, network_name: str) -> Any:
        """Spawn the docker-socket-proxy sidecar.

        We mount the host's ``/var/run/docker.sock`` *into the proxy
        only* — the test container never sees it. The proxy's ACL
        environment variables enforce the API subset the test
        container can use. See :class:`TestcontainersMode` for the
        rationale."""
        # The proxy itself needs the docker socket — but ONLY the
        # proxy, never the test container. Assert this is intentional
        # by labeling it differently from the test container.
        mode = spec.testcontainers
        run_kwargs = build_dind_proxy_run_kwargs(self._settings, mode, network_name)
        return self.client.containers.run(mode.proxy_image, **run_kwargs)

    # --- main container -------------------------------------------------

    def _start_main(self, spec: TestRuntimeSpec, network_name: str) -> Any:
        """Launch the test-runtime container (no checks yet).

        Splitting *start* from *run* is what lets ``launch`` register
        the container for cleanup BEFORE we ``exec_run`` anything; an
        ``exec_run`` that raises mid-sequence still leaves the
        container in our finally block.
        """
        template = spec.plan.template
        run_kwargs = self._build_test_kwargs(spec, network_name)
        assert_no_docker_socket(run_kwargs)
        return self.client.containers.run(template.docker_image, **run_kwargs)

    def _run_checks(
        self,
        spec: TestRuntimeSpec,
        container: Any,
    ) -> tuple[list[int], str, bool]:
        """Run pre_install + each check, return ``(exit_codes, logs, timed_out)``."""
        template = spec.plan.template
        all_logs: list[str] = []
        exit_codes: list[int] = []
        timed_out = False

        # Pre-install (cold cache only — the caller checks the dep-cache
        # hash and skips this when warm; we always run it here, the
        # caching machinery in Fase C is what decides whether to call us).
        for cmd in template.default_pre_install:
            exec_rc, exec_logs = self._exec(container, cmd, timeout_s=DEFAULT_TIMEOUT_S)
            all_logs.append(f"--- pre_install: {cmd}\n{exec_logs}\n")
            if exec_rc != 0:
                # If a pre_install fails we mark every check as failed
                # (couldn't even run them). Test reporter shows the
                # failed install in the report instead of fake test
                # failures.
                exit_codes.extend([exec_rc] * len(spec.plan.checks))
                return exit_codes, "".join(all_logs), False

        for check in spec.plan.checks:
            budget = check.timeout_s or DEFAULT_TIMEOUT_S
            exec_rc, exec_logs = self._exec(container, check.command, timeout_s=budget)
            all_logs.append(
                f"--- check {check.id or check.description!r}: {check.command}\n" f"{exec_logs}\n"
            )
            exit_codes.append(exec_rc)
            if exec_rc == 124:
                # 124 is the conventional "timeout" exit code from GNU
                # timeout / our exec wrapper. Stop running further
                # checks — something is wedged.
                timed_out = True
                break

        return exit_codes, "".join(all_logs), timed_out

    def _build_test_kwargs(
        self,
        spec: TestRuntimeSpec,
        network_name: str,
    ) -> dict[str, Any]:
        """Build ``docker.containers.run`` kwargs for the main test
        container.

        Mirrors :func:`isolation.build_hardened_run_kwargs` but with
        the *bridge* of this task (so the test container can reach the
        aux services and the optional DinD proxy)."""
        template = spec.plan.template
        cpu = spec.cpu if spec.cpu is not None else template.default_resources.cpu
        mem_mb = (
            spec.memory_mb if spec.memory_mb is not None else template.default_resources.memory_mb
        )

        mounts: list[Mount] = [
            Mount(
                target=template.workspace_mount_path,
                source=spec.worktree_host_path,
                type="bind",
                read_only=False,
            )
        ]
        if template.dep_cache_mount and spec.dep_cache_host_path:
            mounts.append(
                Mount(
                    target=template.dep_cache_mount,
                    source=spec.dep_cache_host_path,
                    type="bind",
                    read_only=False,
                )
            )

        env: dict[str, str] = {"HOME": template.workspace_mount_path}
        if spec.testcontainers.enabled:
            env["DOCKER_HOST"] = spec.testcontainers.docker_host_url()
            # testcontainers java/node libs respect TESTCONTAINERS_HOST_OVERRIDE
            env["TESTCONTAINERS_HOST_OVERRIDE"] = spec.testcontainers.proxy_alias()

        return {
            "command": ["sleep", "infinity"],
            "detach": True,
            "network": network_name,
            "mounts": mounts,
            "environment": env,
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "read_only": True,
            "tmpfs": {"/tmp": "rw,nosuid,size=64m"},
            "user": AGENT_UID_GID,
            # Use nano_cpus rather than --cpus so we round-trip safely
            # through json: int suffix vs float decimals.
            "nano_cpus": int(cpu * 1_000_000_000),
            "mem_limit": f"{mem_mb}m",
            "labels": {**_TEST_LABELS, "com.agentic-platform.runtime": template.id},
        }

    def _exec(self, container: Any, command: str, *, timeout_s: int) -> tuple[int, str]:
        """Run one shell command inside the container, return rc + logs.

        We use ``exec_run`` rather than spawning a fresh container per
        check so the pre_install cost is amortised over all checks of
        the same runtime. ``timeout_s`` is not honored by ``exec_run``
        directly — we wrap the command in ``timeout`` so the test
        cannot wedge indefinitely."""
        wrapped = f"timeout {timeout_s} sh -c {_shell_quote(command)}"
        result = container.exec_run(["sh", "-c", wrapped], demux=False)
        rc = getattr(result, "exit_code", 0) or 0
        out_bytes: bytes = getattr(result, "output", b"") or b""
        return rc, out_bytes.decode("utf-8", errors="replace")

    # --- cleanup --------------------------------------------------------

    def _cleanup(
        self,
        main_container: Any,
        proxy_container: Any,
        aux_containers: list[Any],
        network: Any,
    ) -> None:
        for container in [main_container, proxy_container, *aux_containers]:
            if container is None:
                continue
            with contextlib.suppress(Exception):
                container.remove(force=True)
        with contextlib.suppress(Exception):
            network.remove()


def _shell_quote(command: str) -> str:
    """Single-quote a command for safe embedding inside ``sh -c``."""
    return "'" + command.replace("'", "'\"'\"'") + "'"


__all__ = [
    "AcceptanceCheck",
    "AuxServiceSpec",
    "DEFAULT_POSTGRES",
    "DEFAULT_REDIS",
    "DEFAULT_RUN_RUNTIME_ID",
    "RuntimePlan",
    "RuntimeResolutionError",
    "TestRuntimeResult",
    "TestRuntimeRunner",
    "TestRuntimeSpec",
    "TestcontainersMode",
    "build_aux_run_kwargs",
    "build_dind_proxy_run_kwargs",
    "default_aux_services",
    "group_tasks_by_runtime",
    "resolve_run_runtime",
    "resolve_run_runtime_id",
    "resolve_run_runtime_image",
    # Re-exported for tests
    "DockerSocketLeakError",
]
