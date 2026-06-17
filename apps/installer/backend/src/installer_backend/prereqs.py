"""Step 1 prerequisite validation (Plan 15, task 15_02).

The first thing the wizard does is check that this host can actually run the
stack: Docker present and recent enough, Docker Compose v2, enough RAM, enough
free disk, and — optionally — an NVIDIA GPU. Each check returns a tri-state
:class:`~installer_backend.seams.PrereqStatus` (OK / WARN / FAIL) plus a clear
remediation message; any hard ``FAIL`` on a *required* prerequisite blocks the
operator from proceeding.

Design — testable without a host
--------------------------------
The installer runs on a host with real Docker, real RAM/disk, maybe a real
GPU — none of which can be probed in CI. So this module reads the host through
a single injectable seam, :class:`HostProbe`, which returns a plain
:class:`HostReadings` snapshot. The *logic* that turns readings into pass/warn/
fail results lives in pure functions here and is fully unit-testable: tests
inject fake readings, no real host probing happens.

The real probe (subprocess to ``docker version`` / ``docker compose version``,
``os``/``shutil`` for RAM and disk, ``nvidia-smi`` detection) is implemented by
:class:`SystemHostProbe` and exercised only by the plan's Tests Humanos on a
real machine. It never runs in the test suite.

Thresholds (minimum RAM, minimum free disk, minimum Docker/Compose versions)
are named constants on :class:`PrereqThresholds`, configurable per call so the
minimums can be tuned (and so tests can assert that lowering them flips a FAIL
to OK).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from installer_backend.seams import PrereqResult, PrereqStatus

# ---------------------------------------------------------------------------
# Named threshold constants. Single source of truth for the minimums; the
# defaults on PrereqThresholds reference these so tests can assert both the
# constant and the configurability.
# ---------------------------------------------------------------------------
BYTES_PER_GIB = 1024**3

#: Minimum Docker Engine version. Compose v2 + the security flags the stack
#: relies on (cap-drop, seccomp, read-only rootfs) are stable from 24.x.
MIN_DOCKER_VERSION: tuple[int, int] = (24, 0)

#: Minimum Docker Compose version. The stack uses Compose v2 syntax/CLI
#: (``docker compose``). The floor is 2.21: the installer runs
#: ``up -d --wait`` with the one-shot ``migrations`` service as a
#: ``service_completed_successfully`` dependency, and reliable ``--wait``
#: handling of completed (exit-0) one-shots stabilised in later 2.x
#: (task_prod01_16 / 20) — an older Compose can hang/false-fail there.
MIN_COMPOSE_VERSION: tuple[int, int] = (2, 21)

#: Minimum total system RAM. The single-machine stack (PostgreSQL+pgvector,
#: Redis, MinIO, Vault, API, workers) needs headroom; 8 GiB is the floor.
DEFAULT_MIN_RAM_GIB: int = 8

#: Minimum free disk on the data volume. Images + pgdata + object storage.
DEFAULT_MIN_DISK_GIB: int = 50

#: Host ports the published surface (the Caddy reverse proxy) must bind — the
#: ONLY ports the generated stack exposes to the host (ADR 0061). They must be
#: free for the install to succeed (task_prod01_17).
REQUIRED_FREE_PORTS: tuple[int, ...] = (80, 443)


@dataclass(frozen=True)
class PrereqThresholds:
    """Configurable minimums for the prerequisite checks (task 15_02).

    Defaults come from the module-level named constants. Callers (the wizard,
    a CLI profile, or a test) can override any field to tune the gate without
    touching the check logic.
    """

    min_ram_gib: int = DEFAULT_MIN_RAM_GIB
    min_disk_gib: int = DEFAULT_MIN_DISK_GIB
    min_docker_version: tuple[int, int] = MIN_DOCKER_VERSION
    min_compose_version: tuple[int, int] = MIN_COMPOSE_VERSION


@dataclass(frozen=True)
class HostReadings:
    """A plain snapshot of host facts, produced by a :class:`HostProbe`.

    Pure data: no methods, no I/O. ``None`` means "could not detect"
    (e.g. Docker absent, so no version string), which the checks treat as a
    hard failure for required prerequisites.

    * ``docker_version`` — ``(major, minor)`` of the Docker Engine, or ``None``
      if the ``docker`` CLI is missing / not responding.
    * ``compose_version`` — ``(major, minor)`` of Docker Compose v2, or
      ``None`` if Compose v2 is unavailable.
    * ``total_ram_bytes`` — total physical RAM in bytes.
    * ``free_disk_bytes`` — free space in bytes on the install data volume.
    * ``gpu_present`` — True iff an NVIDIA GPU was detected. Informational.
    * ``gpu_name`` — best-effort GPU model string, for the detail line.
    """

    docker_version: tuple[int, int] | None
    compose_version: tuple[int, int] | None
    total_ram_bytes: int
    free_disk_bytes: int
    gpu_present: bool
    gpu_name: str | None = None
    # AppArmor LSM available on the host kernel. Optional: without it the
    # agent/test sandboxes degrade to seccomp-only (task_prod01_10 / sandbox-2).
    apparmor_available: bool = True
    # Host ports (from REQUIRED_FREE_PORTS) found already in use. The reverse
    # proxy is the only published surface (ADR 0061), so 80/443 must be free
    # (task_prod01_17). Empty == all free.
    ports_in_use: tuple[int, ...] = ()


@runtime_checkable
class HostProbe(Protocol):
    """Reads raw host facts. The single seam between checks and the OS.

    Tests inject a fake returning a scripted :class:`HostReadings`; the real
    binding (:class:`SystemHostProbe`) shells out / reads the OS and is run
    only on a real machine (plan's Tests Humanos).
    """

    def read(self) -> HostReadings:
        """Return a fresh snapshot of host prerequisites facts."""
        ...


# ---------------------------------------------------------------------------
# Pure check functions. Each maps the readings + thresholds to one
# PrereqResult with a tri-state status and an actionable remediation message.
# No I/O here — all host access already happened in the probe.
# ---------------------------------------------------------------------------
def _fmt_version(version: tuple[int, int]) -> str:
    return f"{version[0]}.{version[1]}"


def check_docker(readings: HostReadings, thresholds: PrereqThresholds) -> PrereqResult:
    """Docker present and at least the minimum version (required)."""

    key, label = "docker", "Docker Engine"
    if readings.docker_version is None:
        return PrereqResult(
            key=key,
            label=label,
            status=PrereqStatus.FAIL,
            detail="Docker no detectado.",
            remediation=(
                "Instala Docker Engine (>= "
                f"{_fmt_version(thresholds.min_docker_version)}) y asegúrate de que "
                "el demonio está en ejecución: https://docs.docker.com/engine/install/"
            ),
        )
    if readings.docker_version < thresholds.min_docker_version:
        return PrereqResult(
            key=key,
            label=label,
            status=PrereqStatus.FAIL,
            detail=f"Docker {_fmt_version(readings.docker_version)} detectado.",
            remediation=(
                "Actualiza Docker Engine a la versión "
                f"{_fmt_version(thresholds.min_docker_version)} o superior antes de continuar."
            ),
        )
    return PrereqResult(
        key=key,
        label=label,
        status=PrereqStatus.OK,
        detail=f"Docker {_fmt_version(readings.docker_version)} detectado.",
    )


def check_compose(readings: HostReadings, thresholds: PrereqThresholds) -> PrereqResult:
    """Docker Compose v2 present and at least the minimum version (required)."""

    key, label = "compose", "Docker Compose v2"
    if readings.compose_version is None:
        return PrereqResult(
            key=key,
            label=label,
            status=PrereqStatus.FAIL,
            detail="Docker Compose v2 no detectado.",
            remediation=(
                "Instala el plugin Docker Compose v2 (se usa `docker compose`, no el "
                "antiguo `docker-compose` v1): https://docs.docker.com/compose/install/"
            ),
        )
    if readings.compose_version < thresholds.min_compose_version:
        return PrereqResult(
            key=key,
            label=label,
            status=PrereqStatus.FAIL,
            detail=f"Compose {_fmt_version(readings.compose_version)} detectado.",
            remediation=(
                "Actualiza a Docker Compose "
                f"{_fmt_version(thresholds.min_compose_version)} o superior."
            ),
        )
    return PrereqResult(
        key=key,
        label=label,
        status=PrereqStatus.OK,
        detail=f"Compose {_fmt_version(readings.compose_version)} detectado.",
    )


def check_ram(readings: HostReadings, thresholds: PrereqThresholds) -> PrereqResult:
    """Total RAM at least the configured minimum (required)."""

    key, label = "ram", f"RAM >= {thresholds.min_ram_gib} GiB"
    total_gib = readings.total_ram_bytes / BYTES_PER_GIB
    if total_gib < thresholds.min_ram_gib:
        return PrereqResult(
            key=key,
            label=label,
            status=PrereqStatus.FAIL,
            detail=f"{total_gib:.1f} GiB de RAM disponibles.",
            remediation=(
                f"El stack necesita al menos {thresholds.min_ram_gib} GiB de RAM. "
                "Amplía la memoria de la máquina o libera recursos antes de instalar."
            ),
        )
    return PrereqResult(
        key=key,
        label=label,
        status=PrereqStatus.OK,
        detail=f"{total_gib:.1f} GiB de RAM disponibles.",
    )


def check_disk(readings: HostReadings, thresholds: PrereqThresholds) -> PrereqResult:
    """Free disk on the data volume at least the configured minimum (required)."""

    key, label = "disk", f"Disco libre >= {thresholds.min_disk_gib} GiB"
    free_gib = readings.free_disk_bytes / BYTES_PER_GIB
    if free_gib < thresholds.min_disk_gib:
        return PrereqResult(
            key=key,
            label=label,
            status=PrereqStatus.FAIL,
            detail=f"{free_gib:.1f} GiB libres.",
            remediation=(
                f"Se requieren al menos {thresholds.min_disk_gib} GiB libres en el volumen "
                "de datos (/data/agent-platform). Libera espacio o monta un disco mayor."
            ),
        )
    return PrereqResult(
        key=key,
        label=label,
        status=PrereqStatus.OK,
        detail=f"{free_gib:.1f} GiB libres.",
    )


def check_gpu(readings: HostReadings, thresholds: PrereqThresholds) -> PrereqResult:  # noqa: ARG001
    """NVIDIA GPU detection (OPTIONAL — absence is a WARN, never a FAIL)."""

    key, label = "gpu", "GPU NVIDIA (opcional)"
    if readings.gpu_present:
        detail = readings.gpu_name or "GPU NVIDIA detectada."
        return PrereqResult(
            key=key,
            label=label,
            status=PrereqStatus.OK,
            detail=detail,
            required=False,
        )
    return PrereqResult(
        key=key,
        label=label,
        status=PrereqStatus.WARN,
        detail="No se detectó ninguna GPU NVIDIA.",
        remediation=(
            "La GPU es opcional: el stack funciona en CPU. Si quieres aceleración por "
            "GPU (p. ej. Ollama local), instala los drivers NVIDIA y el NVIDIA Container "
            "Toolkit y vuelve a comprobar."
        ),
        required=False,
    )


def check_apparmor(
    readings: HostReadings,
    thresholds: PrereqThresholds,  # noqa: ARG001 — uniform check signature
) -> PrereqResult:
    """AppArmor LSM on the host (OPTIONAL — absence is a WARN, never a FAIL).

    The worker pins ``apparmor=agent-runtime`` onto the UNTRUSTED sandboxes it
    launches (task_prod01_10, ``WORKERS_APPARMOR_PROFILE``). Without AppArmor the
    stack still runs but those sandboxes lose that MAC layer and degrade to
    seccomp-only — less defense-in-depth, so we warn rather than block.
    """

    key, label = "apparmor", "AppArmor (opcional)"
    if readings.apparmor_available:
        return PrereqResult(
            key=key,
            label=label,
            status=PrereqStatus.OK,
            detail="AppArmor disponible en el kernel.",
            required=False,
        )
    return PrereqResult(
        key=key,
        label=label,
        status=PrereqStatus.WARN,
        detail="AppArmor no detectado en el host.",
        remediation=(
            "AppArmor es opcional pero recomendado: el worker pina "
            "apparmor=agent-runtime sobre los sandboxes. Sin AppArmor degradan a "
            "solo-seccomp (menos defensa en profundidad). En hosts con AppArmor, "
            "carga los perfiles con `apparmor_parser -r -W docker/apparmor/"
            "agent-runtime.profile` (ver docs/06-runbooks/apparmor-profiles.md)."
        ),
        required=False,
    )


def check_ports(
    readings: HostReadings,
    thresholds: PrereqThresholds,  # noqa: ARG001 — uniform check signature
) -> PrereqResult:
    """The published surface ports (80/443) must be free (REQUIRED).

    After ADR 0061 the Caddy reverse proxy is the ONLY service that binds host
    ports (80/443). If something else already holds them the stack can't come
    up, so this is a hard FAIL with remediation.
    """

    key, label = "ports", "Puertos publicados libres (80/443)"
    busy = [p for p in REQUIRED_FREE_PORTS if p in readings.ports_in_use]
    if not busy:
        return PrereqResult(
            key=key,
            label=label,
            status=PrereqStatus.OK,
            detail="Los puertos 80 y 443 están libres.",
        )
    busy_str = ", ".join(str(p) for p in busy)
    return PrereqResult(
        key=key,
        label=label,
        status=PrereqStatus.FAIL,
        detail=f"Puertos ya en uso: {busy_str}.",
        remediation=(
            f"El reverse proxy (Caddy) necesita publicar 80/443 (ADR 0061); "
            f"libera el/los puerto(s) {busy_str} (otro servicio web los está "
            "usando) o detén el proceso que los ocupa antes de instalar."
        ),
    )


#: The ordered checks the wizard runs. Required checks first, optional last.
PREREQ_CHECKS = (
    check_docker,
    check_compose,
    check_ram,
    check_disk,
    check_ports,
    check_gpu,
    check_apparmor,
)


@dataclass
class RealPrereqChecker:
    """Runs every prerequisite check against an injected :class:`HostProbe`.

    Implements the :class:`installer_backend.seams.PrereqChecker` Protocol so
    it drops into the existing ``/api/prereqs`` route. Tests construct it with
    a fake probe; production wires :class:`SystemHostProbe`.
    """

    probe: HostProbe
    thresholds: PrereqThresholds = field(default_factory=PrereqThresholds)

    def check_all(self) -> list[PrereqResult]:
        readings = self.probe.read()
        return [check(readings, self.thresholds) for check in PREREQ_CHECKS]

    @property
    def can_proceed(self) -> bool:
        """True when no required prerequisite is a hard FAIL."""

        return not any(r.blocking for r in self.check_all())


class SystemHostProbe:
    """Real host probe — runs ONLY on a real machine (plan's Tests Humanos).

    Shells out to ``docker``/``docker compose`` and reads RAM/disk/GPU from the
    OS. Imported lazily and never exercised by the test suite; the units assert
    the check logic via fake probes instead. Kept thin and dependency-free so
    the installer image needs nothing beyond the stdlib + the Docker CLI.
    """

    def __init__(self, data_path: str = "/data/agent-platform") -> None:
        self._data_path = data_path

    def read(self) -> HostReadings:  # pragma: no cover - host-only, human-tested
        return HostReadings(
            docker_version=self._docker_version(),
            compose_version=self._compose_version(),
            total_ram_bytes=self._total_ram_bytes(),
            free_disk_bytes=self._free_disk_bytes(),
            gpu_present=self._gpu_name() is not None,
            gpu_name=self._gpu_name(),
            apparmor_available=self._apparmor_available(),
            ports_in_use=self._ports_in_use(),
        )

    def _ports_in_use(self) -> tuple[int, ...]:  # pragma: no cover - host-only
        """Of REQUIRED_FREE_PORTS, the ones already bound (LISTENing).

        Only ``EADDRINUSE`` counts as taken. Binding 80/443 needs privilege, so a
        non-root probe gets ``EACCES`` — that is NOT occupancy (the real install
        runs privileged), so we must not report a false positive on it.
        """
        import errno
        import socket

        busy: list[int] = []
        for port in REQUIRED_FREE_PORTS:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("0.0.0.0", port))
            except OSError as exc:
                if exc.errno == errno.EADDRINUSE:
                    busy.append(port)
                # EACCES (no privilege to bind <1024) ≠ occupied — ignore.
            finally:
                sock.close()
        return tuple(busy)

    def _apparmor_available(self) -> bool:  # pragma: no cover - host-only
        """True iff the AppArmor LSM is enabled on this kernel. Best-effort: the
        sysfs flag is the canonical signal; absence/error means 'no AppArmor'."""
        from pathlib import Path

        try:
            flag = Path("/sys/module/apparmor/parameters/enabled")
            if flag.exists():
                return flag.read_text().strip().upper().startswith("Y")
            return Path("/sys/kernel/security/apparmor").exists()
        except OSError:
            return False

    # -- individual real probes (host-only) ---------------------------------
    def _run(self, *args: str) -> str | None:  # pragma: no cover - host-only
        import subprocess  # — host-only, keep import local

        try:
            out = subprocess.run(  # - fixed argv, no shell
                list(args),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if out.returncode != 0:
            return None
        return out.stdout.strip()

    def _parse_major_minor(self, raw: str | None) -> tuple[int, int] | None:  # pragma: no cover
        if not raw:
            return None
        parts = raw.lstrip("v").split(".")
        try:
            return int(parts[0]), int(parts[1])
        except (IndexError, ValueError):
            return None

    def _docker_version(self) -> tuple[int, int] | None:  # pragma: no cover - host-only
        return self._parse_major_minor(
            self._run("docker", "version", "--format", "{{.Server.Version}}")
        )

    def _compose_version(self) -> tuple[int, int] | None:  # pragma: no cover - host-only
        return self._parse_major_minor(self._run("docker", "compose", "version", "--short"))

    def _total_ram_bytes(self) -> int:  # pragma: no cover - host-only
        import os  # — host-only, keep import local
        from collections.abc import Callable  # — host-only, keep import local
        from typing import cast

        # os.sysconf is POSIX-only (the installer runs on Linux); resolve it
        # dynamically so the type checker on non-POSIX dev hosts stays happy.
        sysconf = cast("Callable[[str], int] | None", getattr(os, "sysconf", None))
        if sysconf is None:
            return 0
        try:
            return sysconf("SC_PAGE_SIZE") * sysconf("SC_PHYS_PAGES")
        except (ValueError, OSError):
            return 0

    def _free_disk_bytes(self) -> int:  # pragma: no cover - host-only
        import os  # — host-only, keep import local
        import shutil  # — host-only, keep import local

        target = self._data_path if os.path.exists(self._data_path) else "/"
        try:
            return shutil.disk_usage(target).free
        except OSError:
            return 0

    def _gpu_name(self) -> str | None:  # pragma: no cover - host-only
        return self._run("nvidia-smi", "--query-gpu=name", "--format=csv,noheader") or None
