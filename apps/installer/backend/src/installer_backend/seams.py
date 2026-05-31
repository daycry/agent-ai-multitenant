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
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class PrereqResult:
    """Outcome of a single prerequisite probe (Docker, RAM, disk, GPU, ...).

    Phase A defines the SHAPE; the real probe logic is task 15_02. ``ok`` is
    the pass/fail signal the wizard uses to gate the install button.
    """

    key: str
    label: str
    ok: bool
    detail: str = ""
    # Probes that are informational (e.g. GPU absent on a CPU-only box) are
    # not fatal: a non-required probe that fails does not block the install.
    required: bool = True


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
    """A configurable fake checker. Defaults to a single passing probe."""

    results: list[PrereqResult] = field(
        default_factory=lambda: [
            PrereqResult(
                key="scaffold",
                label="Installer scaffold ready",
                ok=True,
                detail="Phase A shell — real probes arrive in task 15_02.",
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
