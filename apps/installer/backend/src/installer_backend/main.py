"""Minimal FastAPI backend for the temporary installer container.

Phase A (task 15_01) ships the shell:

  * ``GET  /healthz``        — liveness, no auth (used by the bootstrap
                               compose healthcheck and the Next.js wizard to
                               know the backend is up).
  * ``GET  /api/wizard/steps`` — the 9-step flow metadata (ids + ES/EN
                               titles + which step confirms the install). The
                               wizard shell renders its stepper from this.
  * ``POST /api/wizard/advance`` — pure state-machine transition. Stateless:
                               the client posts its current state, the server
                               returns the next one. Lets the frontend keep the
                               authoritative ordering server-side without the
                               installer needing a database (it has none).
  * ``POST /api/wizard/back``    — reverse transition.
  * ``GET  /api/prereqs``    — runs the injected prereq checker (a stub in
                               Phase A; the real probes are task 15_02).

The host-touching pieces (docker compose up, prereq probes, /data writes,
Vault bootstrap) are behind the seams in :mod:`installer_backend.seams` and
default to in-memory stubs so the app imports and serves on any machine. The
real bindings + the install/finalize routes arrive in tasks 15_02-15_06.

Security: this backend never logs secrets. Generated credentials / Vault
unseal keys are shown ONCE by the finalize step (15_06) and are never
persisted in plaintext nor written to the log.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from installer_backend import __version__
from installer_backend.seams import (
    PrereqChecker,
    PrereqStatus,
    StubInstallerLifecycle,
    StubInstallRunner,
    StubPrereqChecker,
)
from installer_backend.wizard import (
    CONFIRMATION_STEP,
    STEP_ORDER,
    STEP_TITLES_EN,
    STEP_TITLES_ES,
    WizardError,
    WizardState,
    WizardStep,
)

_logger = structlog.get_logger("installer_backend")


# ---------------------------------------------------------------------------
# Wire-level models. Pydantic so FastAPI documents + validates them. They
# mirror the pure dataclasses in `wizard.py` but never carry secrets.
# ---------------------------------------------------------------------------
class StepInfo(BaseModel):
    """One step in the wizard flow, as served to the UI."""

    id: str
    index: int
    title_es: str
    title_en: str
    is_confirmation: bool = False


class StepsResponse(BaseModel):
    steps: list[StepInfo]
    confirmation_step: str


class WizardStateModel(BaseModel):
    """Serialisable wizard state posted by the client and returned by the API."""

    current: WizardStep = WizardStep.WELCOME
    furthest: WizardStep = WizardStep.WELCOME
    data: dict[str, dict[str, object]] = Field(default_factory=dict)

    def to_state(self) -> WizardState:
        return WizardState(current=self.current, furthest=self.furthest, data=dict(self.data))

    @classmethod
    def from_state(cls, state: WizardState) -> WizardStateModel:
        return cls(current=state.current, furthest=state.furthest, data=state.data)


class AdvanceRequest(BaseModel):
    state: WizardStateModel = Field(default_factory=WizardStateModel)
    # Optional payload to store against the CURRENT step before advancing.
    # Phase A leaves its shape open; later tasks validate per step.
    payload: dict[str, object] | None = None


class WizardTransitionResponse(BaseModel):
    state: WizardStateModel
    can_advance: bool
    can_go_back: bool


class PrereqItem(BaseModel):
    key: str
    label: str
    # Tri-state outcome (task 15_02): ok / warn / fail.
    status: PrereqStatus
    ok: bool
    detail: str = ""
    # Actionable guidance shown when status is warn/fail; empty when ok.
    remediation: str = ""
    required: bool = True


class PrereqResponse(BaseModel):
    results: list[PrereqItem]
    # True only when no REQUIRED prerequisite is a hard FAIL — the gate for the
    # install button. Informational (non-required) WARNs don't block.
    all_required_ok: bool
    # Mirror of all_required_ok kept as an explicit "can the wizard advance
    # past step 1" signal for the frontend gate.
    can_proceed: bool


# ---------------------------------------------------------------------------
# Dependency providers for the seams. Overridable via
# `app.dependency_overrides` in tests and replaced by real host bindings in
# Phase B. Module-level singletons keep the stub state (e.g. lifecycle flag)
# stable across requests within one process.
# ---------------------------------------------------------------------------
_default_prereq_checker = StubPrereqChecker()
_default_install_runner = StubInstallRunner()
_default_lifecycle = StubInstallerLifecycle()


def get_prereq_checker() -> PrereqChecker:
    return _default_prereq_checker


def create_app() -> FastAPI:
    """Build the installer FastAPI app.

    A factory (not a module-level singleton) so tests can build a fresh app
    and inject fakes, and so Phase B can construct it with real seams without
    import-time side effects.
    """

    app = FastAPI(
        title="agentic-platform · installer",
        description=(
            "Temporary bootstrap installer backend. Self-destructs after install "
            "completes (Plan 15, Fase A). NOT part of the runtime stack."
        ),
        version=__version__,
    )

    # The wizard UI is served from a sibling origin (Next.js dev :3000 / the
    # installer container's own port). Allow it explicitly; the installer is
    # short-lived and reachable only on the install host's loopback/LAN.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/healthz", tags=["meta"])
    def healthz() -> dict[str, str]:
        """Liveness probe — no host access, safe to call before provisioning."""

        return {"status": "ok", "service": "installer", "version": __version__}

    @app.get("/api/wizard/steps", response_model=StepsResponse, tags=["wizard"])
    def get_steps() -> StepsResponse:
        """The ordered 9-step flow with bilingual titles."""

        steps = [
            StepInfo(
                id=step.value,
                index=index,
                title_es=STEP_TITLES_ES[step],
                title_en=STEP_TITLES_EN[step],
                is_confirmation=step is CONFIRMATION_STEP,
            )
            for index, step in enumerate(STEP_ORDER)
        ]
        return StepsResponse(steps=steps, confirmation_step=CONFIRMATION_STEP.value)

    @app.post(
        "/api/wizard/advance",
        response_model=WizardTransitionResponse,
        tags=["wizard"],
    )
    def advance(req: AdvanceRequest) -> WizardTransitionResponse:
        """Advance the wizard one step (pure transition; stateless on server)."""

        try:
            new_state = req.state.to_state().advance(req.payload)
        except WizardError as exc:
            from fastapi import HTTPException

            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return WizardTransitionResponse(
            state=WizardStateModel.from_state(new_state),
            can_advance=new_state.can_advance,
            can_go_back=new_state.can_go_back,
        )

    @app.post(
        "/api/wizard/back",
        response_model=WizardTransitionResponse,
        tags=["wizard"],
    )
    def go_back(req: AdvanceRequest) -> WizardTransitionResponse:
        """Step the wizard back one step."""

        try:
            new_state = req.state.to_state().go_back()
        except WizardError as exc:
            from fastapi import HTTPException

            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return WizardTransitionResponse(
            state=WizardStateModel.from_state(new_state),
            can_advance=new_state.can_advance,
            can_go_back=new_state.can_go_back,
        )

    @app.get("/api/prereqs", response_model=PrereqResponse, tags=["prereqs"])
    def prereqs(checker: Annotated[PrereqChecker, Depends(get_prereq_checker)]) -> PrereqResponse:
        """Run the prerequisite probes (stubbed in Phase A; real in task 15_02)."""

        results = checker.check_all()
        items = [
            PrereqItem(
                key=r.key,
                label=r.label,
                status=r.status,
                ok=r.ok,
                detail=r.detail,
                remediation=r.remediation,
                required=r.required,
            )
            for r in results
        ]
        # A hard FAIL on any (required) check blocks; a non-blocking WARN
        # (e.g. GPU absent) does not. `blocking` is only true for required
        # FAILs by construction.
        can_proceed = not any(r.blocking for r in results)
        return PrereqResponse(results=items, all_required_ok=can_proceed, can_proceed=can_proceed)

    return app


# Module-level app for `uvicorn installer_backend.main:app`.
app = create_app()
