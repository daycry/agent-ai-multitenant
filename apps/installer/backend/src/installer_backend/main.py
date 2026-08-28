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
import os
import secrets
import types
import typing
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, SecretStr

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
# LA SIMULACIÓN SE DECLARA, Y NO CORRE SI NADIE LA HA PEDIDO.
#
# Este backend nunca ha aprovisionado nada: su ejecutor por defecto es un
# `FakeStepExecutor` y las credenciales del paso 9 las fabrica
# `secrets.token_urlsafe`. Eso estaba escrito —en un docstring, en un comentario
# de YAML, en dos README y en un runbook— y no llegaba a la única pantalla que el
# operador miraba, que le decía «Instalación completada. La plataforma está
# instalada» y le daba cinco unseal keys de Vault que no abren nada.
#
# La salida elegida (auditoría del 2026-08-28, opción C) no es cablear el
# ejecutor real —el ADR 0161 §Decisión firmó que este contenedor GENERA y no
# provisiona, y sin socket de Docker cuatro de los cinco pasos del pipeline son
# imposibles— ni retirar el wizard, que se llevaría por delante la mitad que sí
# es real (la captura y validación de config de los pasos 2-7, más las dos
# guardas de cadena de suministro que necesitan que la superficie npm exista).
#
# Es ésta: los dos endpoints que fingen se apagan salvo que alguien encienda
# `INSTALLER_ALLOW_SIMULATION` a propósito, y cuando corren, lo dicen en el
# cuerpo de sus propias respuestas —no en una nota al pie— para que la UI no
# tenga forma de pintarlo bonito.
# ---------------------------------------------------------------------------

#: Los seams que fingen. `cli.py` mantiene su propia tupla para abortar con
#: exit 4 cuando detecta uno sin `--dry-run`; ésta la incluye entera, y
#: `tests/integration/test_installer_simulation_is_declared.py` comprueba que no
#: divergen. Un fake nuevo que sólo entrase en una de las dos sería «real» para
#: el otro camino, y el que se lo tragaría en silencio es el wizard.
SIMULATION_SEAMS: tuple[type, ...] = (
    FakeStepExecutor,
    StubPrereqChecker,
    StubInstallRunner,
    StubInstallerLifecycle,
)

#: Variable de entorno que AUTORIZA la simulación. No la enciende: la simulación
#: ya está cableada. Lo que hace es convertir «levanté el contenedor» en «pedí
#: expresamente una demostración que no instala nada».
SIMULATION_FLAG = "INSTALLER_ALLOW_SIMULATION"

#: Valores que cuentan como «sí». Un flag por PRESENCIA leería
#: `INSTALLER_ALLOW_SIMULATION=0` como un sí, que es justo lo que teclea quien
#: quiere apagarlo.
_TRUTHY = frozenset({"1", "true", "yes", "y", "on", "si", "sí"})

#: El camino que instala de verdad, citado en cada negativa. Negarse sin dar
#: salida es media corrección: quien recibe el 501 tiene que saber qué teclear.
REAL_INSTALL_PATH = "./scripts/install.sh --config install.yaml (installer_backend.cli)"

_SIMULATION_NOTICE_ES = (
    "SIMULACIÓN: este wizard NO instala nada. No se ha arrancado ningún stack, "
    "no se ha inicializado Vault y no existe ningún usuario administrador. Las "
    "credenciales que muestre no abren nada. El camino que instala de verdad es "
    f"el CLI: {REAL_INSTALL_PATH}."
)
_SIMULATION_NOTICE_EN = (
    "SIMULATION: this wizard installs NOTHING. No stack was started, no Vault "
    "was initialised and no admin user exists. Any credentials it shows open "
    f"nothing. The real install path is the CLI: {REAL_INSTALL_PATH}."
)


def simulation_allowed() -> bool:
    """¿Ha autorizado alguien la simulación en ESTE proceso?

    Se lee en cada petición, no al importar: así un test puede encenderla con
    ``monkeypatch.setenv`` sobre una app ya construida, y así el valor que manda
    es el del entorno vivo del contenedor y no el del momento del arranque.
    """

    return os.environ.get(SIMULATION_FLAG, "").strip().lower() in _TRUTHY


@dataclass(frozen=True)
class InstallerMode:
    """Qué es este backend ahora mismo, mirando los seams REALMENTE cableados.

    Sale de los seams y no de una constante, de modo que el día que alguien
    conecte implementaciones reales esto pasa a ``False`` solo y los avisos
    desaparecen sin que haya que acordarse de borrarlos.

    **Las dos mitades van separadas a propósito.** Un ejecutor real con un
    comprobador de prerequisitos en stub SÍ instala: marcar eso como «simulado»
    apagaría una instalación de verdad, que es un fallo peor que el que se está
    arreglando. Al revés también: un ejecutor falso no se vuelve honesto porque
    los prerequisitos se midan bien.

    Sólo se reconocen los fakes de :data:`SIMULATION_SEAMS` —el mismo criterio
    que usa el CLI para abortar con exit 4—, así que un ejecutor inventado a mano
    (los tests tienen varios) cuenta como real. Es un contrato por registro, no
    una adivinación por comportamiento.
    """

    install_simulated: bool
    prereqs_simulated: bool
    allow_simulation: bool

    @property
    def simulated(self) -> bool:
        """¿Finge ALGUNA parte? Es lo que decide el aviso permanente de la UI."""

        return self.install_simulated or self.prereqs_simulated

    @property
    def install_enabled(self) -> bool:
        """¿Pueden correr los endpoints que aprovisionan/revelan?

        Un ejecutor real corre siempre. Uno simulado, sólo si lo han autorizado.
        """

        return (not self.install_simulated) or self.allow_simulation


def secret_field_paths(model: type[BaseModel], prefix: str = "") -> frozenset[str]:
    """Rutas punteadas de los campos ``SecretStr`` de un modelo, recursivamente.

    Se DERIVA del modelo en vez de escribirse a mano por una razón medida: la
    lista literal (`storage.minio_secret_key`, los dos `oauth_token`, la
    `api_key`) envejece el día que alguien añade un proveedor con credencial, y
    envejece **en silencio** — el campo nuevo viajaría por el stream y ninguna
    guarda se enteraría. Con la derivación, marcar el campo como ``SecretStr``
    —que es lo que ya hay que hacer para que no salga en un ``repr``— basta.
    """

    paths: set[str] = set()
    for name, field in model.model_fields.items():
        dotted = f"{prefix}{name}"
        annotation = field.annotation
        # `SecretStr | None` llega como Union: hay que mirar dentro, o los
        # campos opcionales (que son justo los tokens de proveedor) se escapan.
        options = (
            typing.get_args(annotation)
            if typing.get_origin(annotation) in (typing.Union, types.UnionType)
            else (annotation,)
        )
        for option in options:
            if isinstance(option, type) and issubclass(option, SecretStr):
                paths.add(dotted)
            elif isinstance(option, type) and issubclass(option, BaseModel):
                paths |= secret_field_paths(option, prefix=f"{dotted}.")
    return frozenset(paths)


def secrets_present_in(config: object, paths: frozenset[str], prefix: str = "") -> list[str]:
    """Las rutas de ``paths`` que el ``config`` posteado trae con valor.

    Devuelve **rutas**, jamás valores: un mensaje de error que repite el secreto
    lo deja en el historial del navegador, en el log del proxy y en la consola de
    quien esté mirando la pantalla.
    """

    if not isinstance(config, dict):
        return []
    found: list[str] = []
    for key, value in config.items():
        dotted = f"{prefix}{key}"
        if isinstance(value, dict):
            found.extend(secrets_present_in(value, paths, prefix=f"{dotted}."))
        elif dotted in paths and value not in (None, ""):
            found.append(dotted)
    return sorted(found)


#: Los campos que NO pueden viajar por `/api/install/stream`, derivados del
#: modelo una sola vez al importar. Hoy son cuatro (la clave de MinIO y las tres
#: credenciales de proveedor); mañana, los que el modelo declare.
_SECRET_FIELDS = secret_field_paths(InstallerConfig)


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
    # True cuando el checker cableado es un STUB: las filas de abajo no son
    # medidas de esta máquina. Sin este campo la pantalla pintaba una única fila
    # verde inventada («Installer scaffold ready») y abría la puerta en un host
    # sin Docker, con 4 GiB de RAM y los puertos 80/443 ocupados.
    simulated: bool = False


class InstallerModeResponse(BaseModel):
    """Qué es este backend, servido a la UI para que pueda avisar.

    Existe porque el aviso tiene que llegar a la PANTALLA. Mientras esto vivió
    sólo en docstrings y comentarios, el operador recorría nueve pasos, leía
    «Instalación completada» y apuntaba cinco unseal keys que no abren nada.
    """

    #: Los seams cableados son fakes: nada de lo que haga este backend instala.
    simulated: bool
    #: Alguien ha autorizado la simulación (`INSTALLER_ALLOW_SIMULATION`).
    allow_simulation: bool
    #: ¿Responden `/api/install/stream` y `/api/finalize/reveal`, o dan 501?
    install_enabled: bool
    #: El comando que instala de verdad, para que la negativa tenga salida.
    real_path: str
    #: El texto exacto del aviso, en los dos idiomas soportados (ES + EN).
    notice_es: str
    notice_en: str


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
    """Body for the install stream: the captured config echo, SIN secretos.

    ⚠️ Esta frase fue FALSA hasta el 2026-08-28, y conviene que conste porque el
    daño de una afirmación falsa está en dónde se lee, no en qué rompe. Decía
    «Secrets are NOT carried in this payload — the real secrets reach Vault via
    Phase B's bootstrap, not over this stream», y mientras tanto ``toWireConfig``
    del wizard metía aquí ``storage.minio_secret_key`` en claro y, por cada
    proveedor habilitado, el ``oauth_token`` (Claude SDK, Copilot) y la
    ``api_key`` (Azure Foundry). El backend no los registraba y el
    ``FakeStepExecutor`` los ignoraba, así que no había daño observable: sólo un
    documento de diseño mintiendo en el sitio donde se diseña.

    Ahora es cierta **por construcción y no por buena voluntad del cliente**: la
    ruta rechaza con ``400`` cualquier cuerpo que traiga uno de los campos
    ``SecretStr`` del :class:`~installer_backend.config.InstallerConfig`
    (:func:`secret_field_paths`), nombrando el campo y nunca el valor. El
    ejecutor real no los necesita por aquí — los recibe por petición, junto al
    ``cfg`` y al ``compose_dir``.
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
    #: Estas credenciales son de mentira. Viaja en el MISMO cuerpo que las
    #: reparte, no en una ruta aparte, para que también se entere quien llegue
    #: aquí con un `curl` o desde otro cliente.
    simulated: bool = False

    @classmethod
    def from_payload(cls, payload: RevealPayload, *, simulated: bool = False) -> RevealResponse:
        # En simulación el aviso se REESCRIBE, no se complementa: el original
        # («guárdalas ahora, no hay forma de recuperarlas») es exactamente la
        # frase que hace que un operador las apunte en su gestor de contraseñas.
        warning_es = _SIMULATION_NOTICE_ES if simulated else payload.warning_es
        warning_en = _SIMULATION_NOTICE_EN if simulated else payload.warning_en
        return cls(
            credentials=[
                CredentialFieldModel(
                    key=f.key, label_es=f.label_es, label_en=f.label_en, secret=f.secret
                )
                for f in payload.credentials
            ],
            unseal_keys=list(payload.unseal_keys),
            warning_es=warning_es,
            warning_en=warning_en,
            simulated=simulated,
        )


class FinalizeStatusResponse(BaseModel):
    """Non-secret status of the finalize step (drives the UI gate).

    Carries NO secret values — only whether the install completed, whether the
    one-time reveal is still available, and whether it has already been shown.
    """

    installed: bool
    can_reveal: bool
    revealed: bool
    #: `installed: true` sobre una simulación significa «la simulación terminó»,
    #: no «la plataforma está instalada». Sin este campo la UI no puede
    #: distinguir las dos cosas, que es justo lo que pintaba mal.
    simulated: bool = False


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
    (Plan prod-01 task_19). Tests override this to script success/failure.

    Cablear aquí el ejecutor real NO es cambiar este ``return``: el ADR 0161
    §Decisión firmó que este contenedor **genera y no provisiona**, y sin socket
    de Docker cuatro de los cinco pasos del pipeline (`pull_images`,
    `start_stack`, `bootstrap_vault`, `seed_tenant`) son imposibles desde dentro.
    Lo que sí se hizo el 2026-08-28 es dejar de fingir: :func:`get_installer_mode`
    detecta este fake y los dos endpoints que dependen de él se apagan salvo que
    alguien encienda ``INSTALLER_ALLOW_SIMULATION``.
    """

    return _default_step_executor


def get_installer_mode(
    executor: Annotated[StepExecutor, Depends(get_step_executor)],
    checker: Annotated[PrereqChecker, Depends(get_prereq_checker)],
) -> InstallerMode:
    """Si lo cableado finge, dilo — y decide si se le deja correr.

    ``simulated`` se calcula mirando los seams REALMENTE inyectados, no una
    constante: el día que alguien conecte el ejecutor real, los avisos se apagan
    solos y nadie tiene que acordarse de borrarlos. Es la misma comprobación con
    la que el CLI aborta con exit 4 (``cli._assert_real_install_seams``),
    trasladada al camino HTTP, que era el que no refutaba nada.
    """

    return InstallerMode(
        install_simulated=isinstance(executor, SIMULATION_SEAMS),
        prereqs_simulated=isinstance(checker, SIMULATION_SEAMS),
        allow_simulation=simulation_allowed(),
    )


def _refuse_to_simulate(what: str) -> None:
    """Aborta con 501 cuando se pide fingir sin autorización.

    501 y no 403: lo que falta no es un permiso del cliente, es la propia
    funcionalidad. El cuerpo nombra el camino que SÍ instala porque negarse sin
    dar salida deja al operador exactamente donde estaba.
    """

    from fastapi import HTTPException

    raise HTTPException(
        status_code=501,
        detail=(
            f"{what} no está implementado en el wizard HTTP: sus seams son de "
            "SIMULACIÓN (FakeStepExecutor), así que no aprovisionaría nada y las "
            "credenciales que devolviera no abrirían nada. El camino que instala "
            f"de verdad es el CLI: {REAL_INSTALL_PATH}. Para revisar el flujo de "
            f"pantallas sin instalar, arranca con {SIMULATION_FLAG}=1 y lee el "
            "aviso que sale en pantalla."
        ),
    )


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

    @app.get("/api/mode", response_model=InstallerModeResponse, tags=["meta"])
    def installer_mode(
        mode: Annotated[InstallerMode, Depends(get_installer_mode)],
    ) -> InstallerModeResponse:
        """Qué es este backend — la ruta que le permite a la UI avisar.

        La pide el shell del wizard en cada carga. Si esta ruta no responde, la
        UI **asume simulación**: equivocarse avisando de más deja a un operador
        molesto, y equivocarse avisando de menos le deja apuntando unas unseal
        keys que no abren nada.
        """

        return InstallerModeResponse(
            simulated=mode.simulated,
            allow_simulation=mode.allow_simulation,
            install_enabled=mode.install_enabled,
            real_path=REAL_INSTALL_PATH,
            notice_es=_SIMULATION_NOTICE_ES,
            notice_en=_SIMULATION_NOTICE_EN,
        )

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
    def prereqs(
        checker: Annotated[PrereqChecker, Depends(get_prereq_checker)],
        mode: Annotated[InstallerMode, Depends(get_installer_mode)],
    ) -> PrereqResponse:
        """Run the prerequisite probes (stubbed in Phase A; real in task 15_02).

        Con el stub cableado la respuesta sale marcada ``simulated``: la fila
        verde «Installer scaffold ready» no es una medida de esta máquina, y
        ``can_proceed`` calculado sobre ella abre la puerta en un host sin Docker.
        Se marca en vez de cerrar el paso a la fuerza porque cerrar convertiría en
        inútil lo único que el wizard sirve hoy —revisar el flujo—, y porque quien
        pueda instalar de verdad no llega por aquí.
        """

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
        return PrereqResponse(
            results=items,
            all_required_ok=can_proceed,
            can_proceed=can_proceed,
            simulated=mode.prereqs_simulated,
        )

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
        mode: Annotated[InstallerMode, Depends(get_installer_mode)],
    ) -> StreamingResponse:
        """Run the install pipeline and STREAM progress + logs over SSE.

        Each step transitions pending → running → ok (or failed). A failing
        step halts the pipeline, emits a ``failed`` event and leaves later
        steps untouched so the UI can offer retry/abort. The response is a
        ``text/event-stream`` of secret-free progress events; nothing is logged.

        On terminal success the finalize service is ARMED with the one-shot
        credentials so step 9 can reveal them exactly once. A failed (or
        halted) run never arms it — incomplete installs reveal nothing.

        Dos negativas antes de empezar, en este orden:

        1. **501** si los seams son de simulación y nadie la ha autorizado. Un
           pipeline falso que emite «Instalación completada» al 100 % es la
           mentira entera; no basta con matizarla en el paso siguiente.
        2. **400** si el cuerpo trae secretos. El eco de config que llega aquí es
           NO-secreto por contrato, y hasta hoy sólo lo era por costumbre del
           cliente: ``toWireConfig`` metía la clave de MinIO y los tokens de
           proveedor en claro mientras tres docstrings juraban lo contrario.
        """

        if not mode.install_enabled:
            _refuse_to_simulate("Aprovisionar desde el wizard")

        viajan = secrets_present_in(req.config, _SECRET_FIELDS)
        if viajan:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=400,
                detail=(
                    "El eco de configuración de este stream es NO-secreto por "
                    "contrato, y trae campos secretos: "
                    f"{', '.join(viajan)}. Quítalos del cuerpo: el ejecutor real "
                    "recibe las credenciales por petición (junto al `cfg` y al "
                    "`compose_dir`), no por aquí, y el simulado no las usa para "
                    "nada. Se nombra el campo y nunca el valor a propósito."
                ),
            )

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
        mode: Annotated[InstallerMode, Depends(get_installer_mode)],
    ) -> FinalizeStatusResponse:
        """Non-secret status of the one-time reveal (drives the step-9 gate).

        Carries no secret — only whether the install completed, whether the
        reveal is still available, and whether it has already been shown… y si
        todo eso es una simulación, que es la diferencia entre «la plataforma
        está instalada» y «la demostración terminó».
        """

        return FinalizeStatusResponse(
            installed=finalize.installed,
            can_reveal=finalize.can_reveal,
            revealed=finalize.revealed,
            simulated=mode.install_simulated,
        )

    @app.post(
        "/api/finalize/reveal",
        response_model=RevealResponse,
        tags=["finalize"],
    )
    def finalize_reveal(
        finalize: Annotated[FinalizeService, Depends(get_finalize_service)],
        mode: Annotated[InstallerMode, Depends(get_installer_mode)],
    ) -> RevealResponse:
        """Reveal the generated credentials + Vault unseal keys EXACTLY ONCE.

        First call on a completed install returns the secret payload, drops the
        in-memory copy and triggers the installer self-destruct. A second call
        is denied with ``410 Gone`` (the payload is gone — no recovery). An
        incomplete install is refused with ``409 Conflict`` and never reveals
        nor self-destructs. Nothing here is logged.

        Y **501 antes que nada** si los seams fingen y nadie ha autorizado la
        simulación: ésta era la pantalla que servía cinco unseal keys de Vault
        recién inventadas con el mismo contrato que el camino real, bajo el aviso
        de que no había forma de recuperarlas. Cuando la simulación sí está
        autorizada, el cuerpo sale marcado ``simulated`` y con el aviso
        reescrito — el original es precisamente el que hace que se apunten.
        """

        from fastapi import HTTPException

        if not mode.install_enabled:
            _refuse_to_simulate("Revelar credenciales desde el wizard")

        try:
            payload = finalize.reveal()
        except InstallNotCompleteError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except CredentialsAlreadyRevealedError as exc:
            # 410 Gone: the one-time payload has already been served.
            raise HTTPException(status_code=410, detail=str(exc)) from exc
        return RevealResponse.from_payload(payload, simulated=mode.install_simulated)


# Module-level app for `uvicorn installer_backend.main:app`.
app = create_app()
