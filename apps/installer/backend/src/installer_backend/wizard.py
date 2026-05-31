"""The 9-step installer wizard state machine.

The wizard is a strictly linear forward/back flow over nine ordered steps
(Plan 15, Fase A — Alcance):

    1. welcome      — Bienvenida
    2. basics       — Configuración básica del sistema
    3. resources    — Recursos / GPU
    4. storage      — Almacenamiento
    5. providers    — Providers LLM
    6. tenant       — Tenant inicial
    7. summary      — Resumen y confirmación
    8. install      — Instalación (progreso + logs)
    9. done         — Listo (credenciales mostradas UNA vez)

Phase A ships only the *shell*: the ordering, navigation rules and the
identity of each step. The per-step capture forms (steps 2-6) are filled by
tasks 15_02-15_06, the summary preview by 15_04, the install orchestration by
15_05, and the finalize/self-destruct by 15_06. Keeping the state machine
here — pure, side-effect free and fully typed — lets every later task plug a
payload into a step without re-deriving the navigation logic.

Design notes
------------
* The machine is *pure*: ``advance``/``go_back`` return a new
  :class:`WizardState`; they never touch the host or persist anything.
* You cannot skip forward past the next step, and you cannot go back from
  ``welcome``. ``summary`` is the last step a human confirms before the
  irreversible ``install`` step begins.
* No secrets live in this module. Generated credentials / unseal keys are a
  one-shot payload produced by the finalize step (15_06) and are never stored
  in the wizard state.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum


class WizardStep(str, Enum):
    """The nine ordered wizard steps. ``str`` so it serialises as its value."""

    WELCOME = "welcome"
    BASICS = "basics"
    RESOURCES = "resources"
    STORAGE = "storage"
    PROVIDERS = "providers"
    TENANT = "tenant"
    SUMMARY = "summary"
    INSTALL = "install"
    DONE = "done"


# Canonical ordering. The list IS the source of truth for "next"/"previous";
# the enum only names the steps. Keep these in sync.
STEP_ORDER: tuple[WizardStep, ...] = (
    WizardStep.WELCOME,
    WizardStep.BASICS,
    WizardStep.RESOURCES,
    WizardStep.STORAGE,
    WizardStep.PROVIDERS,
    WizardStep.TENANT,
    WizardStep.SUMMARY,
    WizardStep.INSTALL,
    WizardStep.DONE,
)

# Human-facing titles (ES first per docs_language: es). The frontend mirrors
# these; exposed via the API so the wizard shell and any CLI share one source.
STEP_TITLES_ES: dict[WizardStep, str] = {
    WizardStep.WELCOME: "Bienvenida",
    WizardStep.BASICS: "Configuración básica",
    WizardStep.RESOURCES: "Recursos / GPU",
    WizardStep.STORAGE: "Almacenamiento",
    WizardStep.PROVIDERS: "Providers LLM",
    WizardStep.TENANT: "Tenant inicial",
    WizardStep.SUMMARY: "Resumen",
    WizardStep.INSTALL: "Instalación",
    WizardStep.DONE: "Listo",
}

STEP_TITLES_EN: dict[WizardStep, str] = {
    WizardStep.WELCOME: "Welcome",
    WizardStep.BASICS: "Basic configuration",
    WizardStep.RESOURCES: "Resources / GPU",
    WizardStep.STORAGE: "Storage",
    WizardStep.PROVIDERS: "LLM providers",
    WizardStep.TENANT: "Initial tenant",
    WizardStep.SUMMARY: "Summary",
    WizardStep.INSTALL: "Install",
    WizardStep.DONE: "Done",
}

# The step at which the human commits to the irreversible install. Steps after
# this are not "back-navigable" forms — install has side effects and done is
# terminal — but Phase A only needs to know where confirmation lives.
CONFIRMATION_STEP: WizardStep = WizardStep.SUMMARY


def step_index(step: WizardStep) -> int:
    """Position of *step* in the canonical order (0-based)."""

    return STEP_ORDER.index(step)


def next_step(step: WizardStep) -> WizardStep | None:
    """The step that follows *step*, or ``None`` if *step* is the last one."""

    idx = step_index(step)
    if idx + 1 >= len(STEP_ORDER):
        return None
    return STEP_ORDER[idx + 1]


def previous_step(step: WizardStep) -> WizardStep | None:
    """The step that precedes *step*, or ``None`` if *step* is the first one."""

    idx = step_index(step)
    if idx == 0:
        return None
    return STEP_ORDER[idx - 1]


class WizardError(Exception):
    """Raised on an illegal navigation request (skip / out-of-bounds)."""


@dataclass(frozen=True)
class WizardState:
    """Immutable snapshot of where the wizard is and what's been captured.

    ``data`` holds the per-step payloads keyed by step value (e.g.
    ``{"basics": {...}, "tenant": {...}}``). Phase A leaves the shape of each
    payload open — later tasks define and validate it. It NEVER contains
    secrets: generated credentials are a one-shot finalize payload, not state.
    """

    current: WizardStep = WizardStep.WELCOME
    # The furthest step the user has reached. Lets the UI light up the
    # stepper for visited steps while still forbidding a forward skip.
    furthest: WizardStep = WizardStep.WELCOME
    data: dict[str, dict[str, object]] = field(default_factory=dict)

    @property
    def is_first(self) -> bool:
        return self.current is STEP_ORDER[0]

    @property
    def is_last(self) -> bool:
        return self.current is STEP_ORDER[-1]

    @property
    def can_advance(self) -> bool:
        return not self.is_last

    @property
    def can_go_back(self) -> bool:
        return not self.is_first

    def with_step_data(self, step: WizardStep, payload: dict[str, object]) -> WizardState:
        """Return a copy with *payload* stored for *step* (merging keys)."""

        merged = dict(self.data)
        merged[step.value] = {**merged.get(step.value, {}), **payload}
        return replace(self, data=merged)

    def advance(self, payload: dict[str, object] | None = None) -> WizardState:
        """Move to the next step, optionally storing *payload* for the current one.

        Raises :class:`WizardError` if already on the last step.
        """

        nxt = next_step(self.current)
        if nxt is None:
            raise WizardError(f"cannot advance past terminal step {self.current.value!r}")
        state = self.with_step_data(self.current, payload) if payload else self
        new_furthest = nxt if step_index(nxt) > step_index(state.furthest) else state.furthest
        return replace(state, current=nxt, furthest=new_furthest)

    def go_back(self) -> WizardState:
        """Move to the previous step. Raises :class:`WizardError` from the first."""

        prev = previous_step(self.current)
        if prev is None:
            raise WizardError(f"cannot go back from first step {self.current.value!r}")
        return replace(self, current=prev)

    def goto(self, step: WizardStep) -> WizardState:
        """Jump to an already-visited *step* (no forward skipping allowed)."""

        if step_index(step) > step_index(self.furthest):
            raise WizardError(
                f"cannot jump forward to unvisited step {step.value!r} "
                f"(furthest reached: {self.furthest.value!r})"
            )
        return replace(self, current=step)
