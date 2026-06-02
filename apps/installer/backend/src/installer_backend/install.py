"""Install orchestration — wizard step 8 (Plan 15 task_15_05).

Step 8 of the wizard is the *irreversible* install: it runs an ordered
sequence of provisioning steps and streams progress + logs to the UI in real
time. This module owns the **orchestration** — the pipeline definition, the
per-step state machine, and the event stream the API surfaces over SSE. It does
NOT do any real provisioning: every host-touching action goes through an
injectable :class:`StepExecutor` seam (mocked in tests; the real generators
land in Phase B, tasks 15_07-15_09).

The ordered pipeline
--------------------
The installer provisions the stack in this fixed order (each later step assumes
the earlier ones succeeded):

    1. generate_config  — render docker-compose.yml + .env + config/global.yaml
                          and lay out /data/agent-platform (Phase B fills the
                          real generators; here it's a seamed executor step).
    2. pull_images      — docker compose pull.
    3. start_stack      — docker compose up -d + wait for health.
    4. bootstrap_vault  — vault operator init + unseal + KV v2 + policies.
    5. seed_tenant      — create the initial tenant + admin user.

Step state machine
------------------
Each step is ``pending`` until the orchestrator reaches it, ``running`` while
the executor works, then ``ok`` or ``failed``. A failure HALTS the pipeline:
every later step stays ``pending`` and the run ends in a ``failed`` terminal
state that surfaces the error so the UI can offer *retry* (re-run from the
failed step) or *abort*.

Streaming
---------
:meth:`InstallOrchestrator.run` is a generator of :class:`ProgressEvent` (the
shared event type in :mod:`installer_backend.seams`). The API adapts that into
a Server-Sent Events response. The fake executor in tests yields a scripted
sequence, so the streaming contract — ordering, per-step status transitions,
halt-on-failure, terminal event — is asserted without a Docker host.

Security
--------
Nothing here logs secrets, and :class:`ProgressEvent.message` must never carry a
credential or unseal key. Generated credentials / Vault unseal keys are a
one-shot payload produced by the *finalize* step (task 15_06) — never streamed
here and never persisted in plaintext.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

from installer_backend.seams import ProgressEvent


class StepStatus(str, Enum):
    """Lifecycle of a single install step. ``str`` so it serialises as its value.

    * ``PENDING``  — not started yet (the initial state, and the state of every
                     step after a failed one).
    * ``RUNNING``  — the executor is currently working this step.
    * ``OK``       — the step finished successfully.
    * ``FAILED``   — the step raised / reported a failure; the pipeline halts.
    """

    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"


class InstallStep(str, Enum):
    """The ordered provisioning steps. ``str`` for stable serialisation."""

    GENERATE_CONFIG = "generate_config"
    PULL_IMAGES = "pull_images"
    START_STACK = "start_stack"
    BOOTSTRAP_VAULT = "bootstrap_vault"
    SEED_TENANT = "seed_tenant"


#: Canonical execution order. The tuple IS the source of truth for the
#: pipeline; the enum only names the steps. Keep these in sync.
INSTALL_STEP_ORDER: tuple[InstallStep, ...] = (
    InstallStep.GENERATE_CONFIG,
    InstallStep.PULL_IMAGES,
    InstallStep.START_STACK,
    InstallStep.BOOTSTRAP_VAULT,
    InstallStep.SEED_TENANT,
)

#: Human-facing titles (ES first per docs_language: es), surfaced to the UI so
#: the progress view and any CLI share one source of truth.
INSTALL_STEP_TITLES_ES: dict[InstallStep, str] = {
    InstallStep.GENERATE_CONFIG: "Generar configuración",
    InstallStep.PULL_IMAGES: "Descargar imágenes",
    InstallStep.START_STACK: "Arrancar el stack",
    InstallStep.BOOTSTRAP_VAULT: "Inicializar Vault",
    InstallStep.SEED_TENANT: "Crear tenant inicial",
}

INSTALL_STEP_TITLES_EN: dict[InstallStep, str] = {
    InstallStep.GENERATE_CONFIG: "Generate configuration",
    InstallStep.PULL_IMAGES: "Pull images",
    InstallStep.START_STACK: "Start the stack",
    InstallStep.BOOTSTRAP_VAULT: "Bootstrap Vault",
    InstallStep.SEED_TENANT: "Seed initial tenant",
}


def install_step_index(step: InstallStep) -> int:
    """Position of *step* in the canonical pipeline order (0-based)."""

    return INSTALL_STEP_ORDER.index(step)


class StepExecutionError(Exception):
    """Raised by a :class:`StepExecutor` when a step fails.

    The orchestrator catches this, marks the step ``FAILED`` and halts. The
    message is shown to the operator, so it must NOT contain any secret.
    """


@runtime_checkable
class StepExecutor(Protocol):
    """The single injectable seam for everything that touches the host.

    One method per pipeline step. The real implementation (Phase B) shells out
    to ``docker compose``, writes config under ``/data/agent-platform`` and
    bootstraps Vault. Each method returns the log lines it produced (so the
    orchestrator can stream them) and raises :class:`StepExecutionError` on
    failure. Tests inject a fake that yields scripted lines / failures, so the
    orchestration is exercised with no Docker host.
    """

    def execute(self, step: InstallStep, config: dict[str, object]) -> list[str]:
        """Run *step* against *config*; return its log lines.

        Raise :class:`StepExecutionError` to signal a failure that halts the
        pipeline. Returned lines and the error message must be secret-free.
        """
        ...


@dataclass
class StepState:
    """Mutable per-step state tracked by the orchestrator while a run proceeds."""

    step: InstallStep
    status: StepStatus = StepStatus.PENDING
    #: A non-empty error message iff ``status`` is ``FAILED``. Secret-free.
    error: str = ""

    @property
    def title_es(self) -> str:
        return INSTALL_STEP_TITLES_ES[self.step]

    @property
    def title_en(self) -> str:
        return INSTALL_STEP_TITLES_EN[self.step]


@dataclass
class InstallOrchestrator:
    """Drives the ordered install pipeline and yields a progress event stream.

    Construct with an injected :class:`StepExecutor` (a fake in tests, the real
    host binding in Phase B) and the captured wizard config. :meth:`run` walks
    the steps in order, transitioning each through RUNNING → OK, streaming the
    executor's log lines as :class:`ProgressEvent`. The first failing step is
    marked FAILED, a failure event is emitted, and the pipeline HALTS — every
    later step stays PENDING. ``run`` can be called again to *retry* from the
    first not-yet-OK step (already-OK steps are skipped), which backs the UI's
    retry button.
    """

    executor: StepExecutor
    config: dict[str, object] = field(default_factory=dict)
    states: dict[InstallStep, StepState] = field(init=False)

    def __post_init__(self) -> None:
        self.states = {step: StepState(step=step) for step in INSTALL_STEP_ORDER}

    # -- introspection ------------------------------------------------------
    @property
    def ordered_states(self) -> list[StepState]:
        """Per-step states in pipeline order (for the API's steps listing)."""

        return [self.states[step] for step in INSTALL_STEP_ORDER]

    @property
    def failed(self) -> bool:
        """True iff any step is in the FAILED state."""

        return any(s.status is StepStatus.FAILED for s in self.states.values())

    @property
    def completed(self) -> bool:
        """True iff every step finished OK."""

        return all(s.status is StepStatus.OK for s in self.states.values())

    def _percent_for(self, completed_steps: int) -> int:
        """Coarse 0-100 progress from the count of completed steps."""

        total = len(INSTALL_STEP_ORDER)
        return int(round(completed_steps / total * 100)) if total else 0

    # -- the run ------------------------------------------------------------
    def run(self) -> Iterator[ProgressEvent]:
        """Run the pipeline in order, yielding a stream of progress events.

        Yields, per step: a ``running`` event, then one event per executor log
        line, then an ``ok`` event — or, on failure, a single ``failed`` event
        (with ``failed=True``) after which the generator stops, leaving later
        steps PENDING. A final terminal event (``done=True`` on success) closes
        a fully-successful run.

        Re-running after a failure resumes from the first non-OK step, so the
        UI's *retry* re-attempts only what hasn't succeeded.
        """

        completed = sum(1 for s in self.states.values() if s.status is StepStatus.OK)

        for step in INSTALL_STEP_ORDER:
            state = self.states[step]
            if state.status is StepStatus.OK:
                # Already done on a previous (partial) run — skip on retry.
                continue

            # Reset any prior FAILED marker before re-attempting.
            state.status = StepStatus.RUNNING
            state.error = ""
            yield ProgressEvent(
                stage=step.value,
                message=f"{state.title_es}…",
                percent=self._percent_for(completed),
            )

            try:
                lines = self.executor.execute(step, self.config)
            except StepExecutionError as exc:
                state.status = StepStatus.FAILED
                state.error = str(exc)
                yield ProgressEvent(
                    stage=step.value,
                    message=str(exc),
                    percent=self._percent_for(completed),
                    failed=True,
                )
                # Halt: do not touch later steps; they stay PENDING.
                return

            for line in lines:
                yield ProgressEvent(
                    stage=step.value,
                    message=line,
                    percent=self._percent_for(completed),
                )

            state.status = StepStatus.OK
            completed += 1
            yield ProgressEvent(
                stage=step.value,
                message=f"{state.title_es}: completado.",
                percent=self._percent_for(completed),
            )

        # All steps OK — terminal success event.
        yield ProgressEvent(
            stage="done",
            message="Instalación completada.",
            percent=100,
            done=True,
        )


# ---------------------------------------------------------------------------
# In-memory fake executor — the DEFAULT seam the Phase-A app ships with and the
# one tests use. Emits a scripted line per step and can be told to fail at a
# chosen step, so the orchestration (ordering, halt-on-failure, retry) is
# asserted with no Docker host. Real host binding replaces this in Phase B.
# ---------------------------------------------------------------------------
@dataclass
class FakeStepExecutor:
    """A deterministic fake :class:`StepExecutor`.

    By default every step succeeds, emitting one scripted log line. Set
    ``fail_at`` to a step to make that step raise :class:`StepExecutionError`
    (with ``fail_message``) the first time it runs; after ``clear_failure()``
    the same step succeeds, which lets a test drive the retry path.
    """

    #: The step that should fail, or ``None`` for an all-green run.
    fail_at: InstallStep | None = None
    fail_message: str = "El paso de instalación falló (simulado)."
    #: Records the order in which steps were executed (for ordering asserts).
    executed: list[InstallStep] = field(default_factory=list)

    _scripts: dict[InstallStep, list[str]] = field(
        default_factory=lambda: {
            InstallStep.GENERATE_CONFIG: [
                "Renderizando docker-compose.yml + .env + config/global.yaml.",
                "Creando estructura en /data/agent-platform.",
            ],
            InstallStep.PULL_IMAGES: ["Descargando imágenes con docker compose pull."],
            InstallStep.START_STACK: [
                "docker compose up -d.",
                "Esperando a que los servicios estén saludables.",
            ],
            InstallStep.BOOTSTRAP_VAULT: [
                "vault operator init + unseal.",
                "Habilitando KV v2 y políticas iniciales.",
            ],
            InstallStep.SEED_TENANT: ["Creando tenant inicial y usuario administrador."],
        }
    )

    def clear_failure(self) -> None:
        """Stop failing (used to drive the retry path in a test)."""

        self.fail_at = None

    def execute(self, step: InstallStep, config: dict[str, object]) -> list[str]:  # noqa: ARG002
        self.executed.append(step)
        if self.fail_at is not None and step is self.fail_at:
            raise StepExecutionError(self.fail_message)
        return list(self._scripts.get(step, []))
