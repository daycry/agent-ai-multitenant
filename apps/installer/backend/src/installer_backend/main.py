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

import json
import secrets
from collections.abc import Iterator
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from installer_backend import __version__
from installer_backend.config import (
    ConfigValidationResponse,
    InstallerConfig,
    validate_config,
)
from installer_backend.finalize import (
    CredentialsAlreadyRevealedError,
    FinalizeService,
    InstallCredentials,
    InstallNotCompleteError,
    RevealPayload,
)
from installer_backend.install import (
    INSTALL_STEP_ORDER,
    FakeStepExecutor,
    InstallOrchestrator,
    StepExecutor,
)
from installer_backend.seams import (
    InstallerLifecycle,
    PrereqChecker,
    PrereqStatus,
    ProgressEvent,
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
# Install step 8 — progress + logs (task 15_05). The orchestration runs the
# ordered pipeline behind the StepExecutor seam; the API exposes the pipeline
# definition and streams progress events over SSE. Models never carry secrets.
# ---------------------------------------------------------------------------
class InstallStepInfo(BaseModel):
    """One step in the install pipeline, as served to the UI (no run state)."""

    id: str
    index: int
    title_es: str
    title_en: str


class InstallPipelineResponse(BaseModel):
    """The ordered install pipeline definition surfaced to the progress view."""

    steps: list[InstallStepInfo]


class InstallStreamRequest(BaseModel):
    """Body for the install stream: the captured (secret-free) config echo.

    The frontend posts the non-secret normalised config here so the executor
    can provision from it. Secrets are NOT carried in this payload — the real
    secrets reach Vault via Phase B's bootstrap, not over this stream.
    """

    config: dict[str, object] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Finalize step 9 — credentials ONCE + self-destruct (task_15_06). The reveal
# is the ONLY surface that ever carries the generated secret values, and it is
# served exactly once. These models exist solely for that single hand-off.
# ---------------------------------------------------------------------------
class CredentialFieldModel(BaseModel):
    """One labelled credential line in the one-time reveal."""

    key: str
    label_es: str
    label_en: str
    secret: str


class RevealResponse(BaseModel):
    """The one-time reveal payload. Returned by ``/api/finalize/reveal`` ONCE.

    After this is served the in-memory secrets are dropped and the installer
    self-destructs, so this body cannot be fetched again. It is never persisted
    nor logged.
    """

    credentials: list[CredentialFieldModel]
    unseal_keys: list[str]
    warning_es: str
    warning_en: str

    @classmethod
    def from_payload(cls, payload: RevealPayload) -> RevealResponse:
        return cls(
            credentials=[
                CredentialFieldModel(
                    key=f.key, label_es=f.label_es, label_en=f.label_en, secret=f.secret
                )
                for f in payload.credentials
            ],
            unseal_keys=list(payload.unseal_keys),
            warning_es=payload.warning_es,
            warning_en=payload.warning_en,
        )


class FinalizeStatusResponse(BaseModel):
    """Non-secret status of the finalize step (drives the UI gate).

    Carries NO secret values — only whether the install completed, whether the
    one-time reveal is still available, and whether it has already been shown.
    """

    installed: bool
    can_reveal: bool
    revealed: bool


# ---------------------------------------------------------------------------
# Dependency providers for the seams. Overridable via
# `app.dependency_overrides` in tests and replaced by real host bindings in
# Phase B. Module-level singletons keep the stub state (e.g. lifecycle flag)
# stable across requests within one process.
# ---------------------------------------------------------------------------
_default_prereq_checker = StubPrereqChecker()
_default_install_runner = StubInstallRunner()
_default_lifecycle = StubInstallerLifecycle()
_default_step_executor = FakeStepExecutor()
# The finalize service is a PROCESS-WIDE singleton: the install stream arms it
# on terminal success and a later request reveals it exactly once, so the two
# must share the same instance across requests. Defaults to the recording stub
# lifecycle; Phase B swaps in the real self-destruct binding.
_default_finalize_service = FinalizeService(lifecycle=_default_lifecycle)


def get_prereq_checker() -> PrereqChecker:
    return _default_prereq_checker


def get_step_executor() -> StepExecutor:
    """The install :class:`StepExecutor` seam for the HTTP wizard.

    ⚠️ The wizard (``POST /api/install/stream``) still defaults to the in-memory
    :class:`FakeStepExecutor` — it is a **SIMULATION**, it does NOT provision a
    real stack, and the credentials it reveals are NOT real. The REAL install
    path is the CLI (``scripts/install.sh`` → :func:`cli.run_install`), which
    wires the real bindings by default and fails loud on a simulation seam
    (Plan prod-01 task_19). Wiring the real executor into the wizard (per-request
    ``compose_dir``/``cfg``/``secrets`` plumbing + a simulation guard on the
    reveal) is a documented follow-up owned by the installer UI (prod-09). Tests
    override this to script success/failure.
    """

    return _default_step_executor


def get_lifecycle() -> InstallerLifecycle:
    """The installer self-destruct seam (mocked in tests; real in Phase B)."""

    return _default_lifecycle


def get_finalize_service() -> FinalizeService:
    """The process-wide :class:`FinalizeService` for the one-time reveal.

    A singleton so the install stream (which arms it on success) and the later
    reveal request observe the same state. Tests override this to inject a fresh
    service wired to a recording lifecycle fake.
    """

    return _default_finalize_service


def build_install_credentials(config: dict[str, object]) -> InstallCredentials:
    """Build the one-shot credentials produced by a successful install.

    Phase A has no real secret generator (Vault init / password minting land in
    Phase B). To keep the finalize state machine exercisable end-to-end without
    a Docker host, this derives a deterministic-but-non-persisted placeholder
    set from the (secret-free) config echo: the real binding replaces it with
    the actual ``vault operator init`` output + minted admin password. The
    values produced here are NEVER written to disk nor logged.
    """

    tenant = config.get("tenant")
    admin_username = "admin"
    if isinstance(tenant, dict):
        email = tenant.get("admin_email")
        if isinstance(email, str) and email:
            admin_username = email
    # Placeholder secrets — replaced by real Vault output in Phase B. Generated
    # fresh per install; held only in memory by the FinalizeService.
    return InstallCredentials(
        admin_username=admin_username,
        admin_password=secrets.token_urlsafe(18),
        vault_root_token=secrets.token_urlsafe(24),
        vault_unseal_keys=tuple(secrets.token_urlsafe(24) for _ in range(5)),
    )


def _sse_event(event: ProgressEvent) -> str:
    """Encode one :class:`ProgressEvent` as a Server-Sent Events frame.

    The payload is the event's JSON (stage/message/percent/done/failed). No
    secret ever appears here — the orchestrator's events are secret-free.
    """

    payload = json.dumps(
        {
            "stage": event.stage,
            "message": event.message,
            "percent": event.percent,
            "done": event.done,
            "failed": event.failed,
        }
    )
    return f"data: {payload}\n\n"


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

    @app.post(
        "/api/config/validate",
        response_model=ConfigValidationResponse,
        tags=["config"],
    )
    def validate_installer_config(config: InstallerConfig) -> ConfigValidationResponse:
        """Server-side validation of the captured config (wizard steps 2-6).

        FastAPI already ran per-field Pydantic validation (a malformed field
        yields a 422 before this body runs). This adds the cross-field provider
        rules and returns a secret-free result: secrets are NEVER echoed — the
        response carries only normalised non-secret values + ``*_set`` booleans.
        Nothing here is logged.
        """

        return validate_config(config)

    @app.get(
        "/api/install/steps",
        response_model=InstallPipelineResponse,
        tags=["install"],
    )
    def get_install_steps() -> InstallPipelineResponse:
        """The ordered install pipeline definition (step 8 progress view)."""

        from installer_backend.install import (
            INSTALL_STEP_TITLES_EN,
            INSTALL_STEP_TITLES_ES,
        )

        steps = [
            InstallStepInfo(
                id=step.value,
                index=index,
                title_es=INSTALL_STEP_TITLES_ES[step],
                title_en=INSTALL_STEP_TITLES_EN[step],
            )
            for index, step in enumerate(INSTALL_STEP_ORDER)
        ]
        return InstallPipelineResponse(steps=steps)

    @app.post("/api/install/stream", tags=["install"])
    def install_stream(
        req: InstallStreamRequest,
        executor: Annotated[StepExecutor, Depends(get_step_executor)],
        finalize: Annotated[FinalizeService, Depends(get_finalize_service)],
    ) -> StreamingResponse:
        """Run the install pipeline and STREAM progress + logs over SSE.

        Each step transitions pending → running → ok (or failed). A failing
        step halts the pipeline, emits a ``failed`` event and leaves later
        steps untouched so the UI can offer retry/abort. The response is a
        ``text/event-stream`` of secret-free progress events; nothing is logged.

        On terminal success the finalize service is ARMED with the one-shot
        credentials so step 9 can reveal them exactly once. A failed (or
        halted) run never arms it — incomplete installs reveal nothing.
        """

        orchestrator = InstallOrchestrator(executor=executor, config=req.config)

        def event_stream() -> Iterator[str]:
            for event in orchestrator.run():
                yield _sse_event(event)
            # Arm the one-time reveal only when the pipeline fully completed.
            # A failed/halted run leaves the finalize service un-armed.
            if orchestrator.completed:
                finalize.arm(build_install_credentials(req.config))

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    _register_finalize_routes(app)

    return app


def _register_finalize_routes(app: FastAPI) -> None:
    """Register the step-9 finalize routes (one-time reveal + status).

    Split out of :func:`create_app` so the factory stays under the statement
    cap; the routes still close over the shared seam dependency providers.
    """

    @app.get(
        "/api/finalize/status",
        response_model=FinalizeStatusResponse,
        tags=["finalize"],
    )
    def finalize_status(
        finalize: Annotated[FinalizeService, Depends(get_finalize_service)],
    ) -> FinalizeStatusResponse:
        """Non-secret status of the one-time reveal (drives the step-9 gate).

        Carries no secret — only whether the install completed, whether the
        reveal is still available, and whether it has already been shown.
        """

        return FinalizeStatusResponse(
            installed=finalize.installed,
            can_reveal=finalize.can_reveal,
            revealed=finalize.revealed,
        )

    @app.post(
        "/api/finalize/reveal",
        response_model=RevealResponse,
        tags=["finalize"],
    )
    def finalize_reveal(
        finalize: Annotated[FinalizeService, Depends(get_finalize_service)],
    ) -> RevealResponse:
        """Reveal the generated credentials + Vault unseal keys EXACTLY ONCE.

        First call on a completed install returns the secret payload, drops the
        in-memory copy and triggers the installer self-destruct. A second call
        is denied with ``410 Gone`` (the payload is gone — no recovery). An
        incomplete install is refused with ``409 Conflict`` and never reveals
        nor self-destructs. Nothing here is logged.
        """

        from fastapi import HTTPException

        try:
            payload = finalize.reveal()
        except InstallNotCompleteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CredentialsAlreadyRevealedError as exc:
            # 410 Gone: the one-time payload has already been served.
            raise HTTPException(status_code=410, detail=str(exc)) from exc
        return RevealResponse.from_payload(payload)


# Module-level app for `uvicorn installer_backend.main:app`.
app = create_app()
