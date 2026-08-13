"""Injectable seams for everything that touches the host.

The installer actually provisions a real stack (Docker, ``pg_*``, Vault) and
writes under ``/data/agent-platform`` — none of which can run in CI or the
test env. To keep the wizard state machine and the install orchestration
testable, every host-touching action is expressed as a *Protocol* here and
injected into the API/app. Tests pass deterministic fakes; the real bindings
(subprocess to ``docker compose``, ``psutil``-style probes, file writes, Vault
init) are wired in Phase B (tasks 15_07-15_09) and exercised only by the
plan's Tests Humanos.

Nothing in this module performs I/O. The concrete "host" implementations live
behind these Protocols so the default app stays import-safe even on a machine
with no Docker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable


class PrereqStatus(StrEnum):
    """Tri-state outcome of a prerequisite check (task 15_02).

    * ``OK``   — the prerequisite is satisfied.
    * ``WARN`` — not satisfied but non-blocking (e.g. an optional GPU is
                 absent, or a soft recommendation is below target). The
                 install can still proceed.
    * ``FAIL`` — a *required* prerequisite is unmet. This is a hard failure
                 that blocks proceeding past the prereq step.

    ``str`` so it serialises as its value over the API.
    """

    OK = "ok"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class PrereqResult:
    """Outcome of a single prerequisite probe (Docker, RAM, disk, GPU, ...).

    ``status`` is the tri-state signal (task 15_02). ``ok`` is the derived
    boolean the wizard uses to gate the install button: a result is "ok" for
    gating purposes when it is not a hard ``FAIL`` (so a non-blocking ``WARN``
    — e.g. GPU absent — does not close the gate). ``remediation`` is a clear,
    actionable message shown to the operator when something is wrong; it is
    empty when the check passes cleanly.
    """

    key: str
    label: str
    status: PrereqStatus
    detail: str = ""
    # Actionable guidance shown when status is WARN/FAIL. Never empty for a
    # non-OK result; the checker is responsible for filling it.
    remediation: str = ""
    # Probes that are informational (e.g. GPU absent on a CPU-only box) are
    # not fatal: a non-required probe never produces a hard FAIL.
    required: bool = True

    @property
    def ok(self) -> bool:
        """True unless this is a hard failure (the install-gate signal)."""

        return self.status is not PrereqStatus.FAIL

    @property
    def blocking(self) -> bool:
        """True when this result must block the operator from proceeding."""

        return self.status is PrereqStatus.FAIL


@dataclass(frozen=True)
class ProgressEvent:
    """One line of install progress, streamed to the UI in step 8.

    ``percent`` is a coarse 0-100 estimate; ``message`` is a human log line.
    The real install runner (task 15_05) emits a stream of these; the fake in
    tests emits a scripted sequence so the streaming contract is asserted
    without a Docker host. Secrets MUST NOT appear in ``message``.
    """

    stage: str
    message: str
    percent: int
    done: bool = False
    failed: bool = False


@runtime_checkable
class PrereqChecker(Protocol):
    """Probes host prerequisites (Docker, Compose v2, RAM, disk, GPU)."""

    def check_all(self) -> list[PrereqResult]:
        """Run every probe and return one result per prerequisite."""
        ...


@runtime_checkable
class InstallRunner(Protocol):
    """Drives the actual provisioning and yields progress events.

    The real implementation shells out to ``docker compose up``, writes config
    and bootstraps Vault. The fake yields a scripted sequence. Either way the
    API consumes it as an iterable of :class:`ProgressEvent`.
    """

    def run(self, config: dict[str, object]) -> list[ProgressEvent]:
        """Provision the stack from *config*; return the progress timeline."""
        ...


@runtime_checkable
class InstallerLifecycle(Protocol):
    """Finalize + self-destruct hooks for the temporary installer container.

    ``self_destruct`` is the "autodestrucción del installer" of step 9 — the
    real binding (task 15_06) signals the bootstrap compose to stop/remove the
    installer container. In tests it just records the call.
    """

    def self_destruct(self) -> None:
        """Request the installer container to stop and remove itself."""
        ...


# ---------------------------------------------------------------------------
# In-memory fakes — the DEFAULT seams the Phase-A app ships with. They make
# the app import-safe and the route tests deterministic on a host with no
# Docker. Real host bindings replace these in Phase B.
# ---------------------------------------------------------------------------
@dataclass
class StubPrereqChecker:
    """A configurable fake checker. Defaults to a single passing probe.

    Real prereq checking (task 15_02) is :class:`installer_backend.prereqs.
    RealPrereqChecker`, which probes the host behind a seam. This stub stays
    as the convenience default for route tests that only care about the gate.
    """

    results: list[PrereqResult] = field(
        default_factory=lambda: [
            PrereqResult(
                key="scaffold",
                label="Installer scaffold ready",
                status=PrereqStatus.OK,
                detail="Phase A shell — real probes added in task 15_02.",
            )
        ]
    )

    def check_all(self) -> list[PrereqResult]:
        return list(self.results)


@dataclass
class StubInstallRunner:
    """A fake runner that emits a minimal scripted progress timeline."""

    events: list[ProgressEvent] = field(
        default_factory=lambda: [
            ProgressEvent(stage="scaffold", message="Installer shell ready.", percent=0),
            ProgressEvent(
                stage="scaffold",
                message="Real provisioning arrives in task 15_05.",
                percent=100,
                done=True,
            ),
        ]
    )

    def run(self, config: dict[str, object]) -> list[ProgressEvent]:  # noqa: ARG002
        return list(self.events)


@dataclass
class StubInstallerLifecycle:
    """Records self-destruct requests instead of touching the host."""

    destroyed: bool = False

    def self_destruct(self) -> None:
        self.destroyed = True
