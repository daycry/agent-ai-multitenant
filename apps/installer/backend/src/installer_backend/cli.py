"""Unattended CLI install — ``python -m installer_backend.cli`` (Plan 15 task_15_10).

The wizard (Phase A) drives the install interactively over HTTP; this module is
its **headless twin**. ``scripts/install.sh --config install.yaml`` is a thin
shell wrapper over ``python -m installer_backend.cli install --config
install.yaml`` that runs the *same* orchestration the wizard runs — only the
input (a YAML file instead of nine HTTP steps) and the output (stdout log lines
+ a process exit code instead of an SSE stream + a browser reveal) differ.

The shared pipeline
-------------------
Both the wizard and the CLI provision the stack through ONE orchestration so the
two can never drift:

    0. prereqs         — validate Docker / Compose / RAM / disk (the wizard's
                         step 1). A hard FAIL ABORTS before any provisioning.
    1. generate_config — render compose / .env / config + lay out the data tree
                         (tasks 15_07/15_08).
    2. pull_images     — ``docker compose pull``.
    3. start_stack     — ``docker compose up -d`` + wait for health.
    4. bootstrap_vault — `docker compose run --rm bootstrap`: el one-shot de
                         finalización, que corre DENTRO de la red del stack (init
                         + unseal de Vault, KV v2, políticas, siembra y revelado).
    5. seed_tenant     — rinde cuentas de lo que sembró ese one-shot; ya no
                         siembra por su cuenta (ver `real_step_executor`).
    6. finalize        — arm the one-time reveal (task 15_06) and print the
                         credentials + Vault unseal keys ONCE.

Steps 1-5 are exactly :data:`installer_backend.install.INSTALL_STEP_ORDER`, run
by the same :class:`~installer_backend.install.InstallOrchestrator` the
``/api/install/stream`` route uses. The CLI adds the prereq gate in front (the
wizard gates it in step 1) and the finalize reveal at the end (the wizard's
step 9). :func:`headless_pipeline` is the single source of truth for the named
phases so a test can assert the CLI runs the wizard's pipeline.

``generate``: el instalador escribe y sale (ADR 0161, opción D)
---------------------------------------------------------------
``install`` presupone que quien lo ejecuta manda sobre el daemon Docker del
host. Cuando el instalador se distribuye **como contenedor** eso deja de ser
gratis: para provisionar necesitaría el socket del daemon montado dentro, que es
acceso root efectivo al host — exactamente lo que rechazó el ADR 0060 — y no
puede pasar por el socket-proxy, cuya ACL deniega ``VOLUMES``.

El subcomando ``generate`` es la salida que eligió el operador el 2026-08-27: el
contenedor **no habla con Docker**. Se le monta sólo la raíz de datos, ejecuta
ÚNICAMENTE el paso :data:`~installer_backend.install.InstallStep.GENERATE_CONFIG`
—compose, ``.env``, ``config/global.yaml``, el ``Caddyfile`` y los auxiliares que
el compose monta— y sale. El ``up`` y la finalización los ejecuta el operador::

    docker run --rm -v /data/agent-platform:/data/agent-platform \\
      ghcr.io/daycry/installer:v1.0.0 generate --config install.yaml
    cd /data/agent-platform && docker compose up -d --wait
    docker compose run --rm bootstrap

El precio del diseño es que **una línea se convierte en tres**, así que
``generate`` termina imprimiendo los comandos que le quedan al operador: un
instalador que acaba en verde sin decirlo deja un stack que no existe y a alguien
convencido de lo contrario.

Ese banner tiene una regla dura: **sólo puede mandar ejecutar lo que existe**. El
paso 8 del ADR 0161 son dos mitades —este subcomando y el one-shot ``bootstrap``—
y desde el 2026-08-28 las dos están: el compose generado declara el servicio y la
imagen del api-server trae el módulo ``api_server.bootstrap`` que ejecuta
(:data:`BOOTSTRAP_ENTRYPOINT_AVAILABLE`). Por eso el banner vuelve a imprimir los
DOS comandos. La regla no se retira con la deuda que la motivó: un banner que
ordena un comando que falla es peor que no imprimir nada —convierte «falta media
tarea» en «tu Docker está roto»—, así que la bandera se cruza con el árbol del
repositorio en un test y volvería a marcar el paso como PENDIENTE si el módulo
desapareciera.

Y no es sólo el banner. ``generate`` es el único camino que instala sin clonar,
así que también es el único donde **nadie corre la puerta de prerequisitos**: no
puede: el contenedor no habla con Docker y no ve la netns del host. Lo que sí
puede es medir el disco libre de la raíz de datos —que está montada— y **decir**
lo que no puede comprobar, con los mismos umbrales y las mismas frases de
remediación que usa :mod:`installer_backend.prereqs`. Un instalador que sabe la
comprobación y no la enseña es peor que uno que no la tiene.

Y a diferencia de ``install``, ``generate`` **no tiene ``--dry-run``**. Simular la
escritura de un árbol de ficheros no informa de nada —el árbol ES el resultado— y
un ejecutor de simulación cableado aquí produciría el fallo que hoy tiene el
wizard HTTP: log en verde, raíz de datos vacía, y el operador enterándose en el
``up``. La guarda :func:`_assert_real_generate_seams` no tiene puerta trasera.

Exit codes
----------
The CLI maps each failure class to a distinct, documented exit code
(:class:`ExitCode`) so an operator's automation can branch on *why* it failed:

    0  OK            — install completed.
    1  USAGE         — bad CLI args / missing ``--config``.
    2  CONFIG        — the YAML is malformed or fails validation (rejected
                       BEFORE any provisioning).
    3  PREREQ        — a required prerequisite failed (aborts BEFORE provisioning).
    4  PROVISION     — a provisioning step (generate/pull/start/vault/seed) failed.
                       Incluye el fallo del one-shot de finalización: si
                       `docker compose run --rm bootstrap` sale con rc≠0, o sale
                       en verde sin emitir su línea de revelado, el paso 4 muere
                       con un mensaje que nombra el comando y su salida — nunca
                       con una traza.
    5  ABORTED       — the operator declined a destructive confirmation.
    6  GENERATE      — ``generate`` no pudo escribir el árbol de arranque (o se
                       cableó con seams de simulación). Distinto de PROVISION a
                       propósito: un 4 dice «el stack puede haber quedado a
                       medias»; un 6 dice «no se levantó nada, y la raíz de datos
                       puede tener escrituras parciales».
    7  INCOMPLETE    — una acción destructiva se ejecutó sólo en parte y quedan
                       datos en disco (hoy: ``uninstall --purge-data`` cuando el
                       purgador no pudo borrarlo todo). Lo que toca al recogerlo
                       es lo contrario de reintentar: NO dar la máquina por
                       limpia — puede seguir ahí el ``.env`` con los secretos.
    8  UNSAFE        — la raíz de datos tiene una instalación previa que NO se
                       puede releer, y seguir destruiría secretos irrecuperables.
                       NO se ha escrito nada. Reintentar el mismo comando falla
                       igual para siempre: hace falta recuperar el ``.env`` o
                       asumir la pérdida con ``--force-new-secrets``.
    9  UNEXPECTED    — una excepción que nadie previó, ya traducida a un mensaje
                       en stderr. Existe para que NINGÚN fallo salga como traza
                       de Python con un código que la tabla llama «argumentos
                       mal».

Security
--------
Generated secrets + Vault unseal keys are produced by the Phase-B generators
(CSPRNG, never the dev-default markers) and the finalize reveal. The CLI prints
them to stdout EXACTLY ONCE (the operator running an unattended install is
responsible for capturing them, mirroring the wizard's one-time reveal) and
NEVER writes them to a log file nor echoes them into the streamed progress
lines. Every host-touching action (prereq probe, the provisioning executor,
self-destruct) is an injectable seam — mocked in tests, so the whole CLI is
exercised with no Docker host, no real Vault and no writes to ``/data``.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol, TextIO, runtime_checkable

import yaml
from pydantic import ValidationError

from installer_backend.command_runner import SubprocessRunner
from installer_backend.compose_generator import BOOTSTRAP_ENTRYPOINT
from installer_backend.config import InstallerConfig, validate_config
from installer_backend.config_generators import GeneratedSecrets
from installer_backend.finalize import FinalizeService, InstallCredentials, RevealPayload
from installer_backend.install import (
    INSTALL_STEP_ORDER,
    INSTALL_STEP_TITLES_ES,
    FakeStepExecutor,
    InstallOrchestrator,
    InstallStep,
    StepExecutionError,
    StepExecutor,
)
from installer_backend.install_state import (
    FORCE_FLAG,
    DataRootInspector,
    SecretDecision,
    SecretResolver,
    UnsafeOverwriteError,
)
from installer_backend.key_escrow import FileKeyEscrow, KeyEscrow, read_unseal_keys
from installer_backend.prereqs import (
    BYTES_PER_GIB,
    DEFAULT_MIN_DISK_GIB,
    DEFAULT_MIN_RAM_GIB,
    MIN_COMPOSE_VERSION,
    MIN_DOCKER_VERSION,
    REQUIRED_FREE_PORTS,
    RealPrereqChecker,
    SystemHostProbe,
)
from installer_backend.real_bindings import (
    RealDataTreeProvisioner,
    RealEnvFileWriter,
    RealEscrowFile,
    RealFileReader,
)
from installer_backend.real_step_executor import RealStepExecutor
from installer_backend.reinstall import (
    MissingExistingSecretError,
    ReinstallAbortedError,
    Reinstaller,
    ReinstallMode,
    ReinstallRequest,
    ReinstallResult,
    StubExistingSecretLoader,
    StubInstallDetector,
    build_default_reinstaller,
    build_preserve_executor,
    run_preserve_pipeline,
    secrets_from_env,
)
from installer_backend.seams import (
    InstallerLifecycle,
    PrereqChecker,
    StubInstallerLifecycle,
    StubPrereqChecker,
)
from installer_backend.uninstall import (
    Confirmer,
    StubDataPurger,
    StubStackTeardown,
    UninstallAbortedError,
    Uninstaller,
    UninstallRequest,
    build_default_uninstaller,
)

if TYPE_CHECKING:
    from pathlib import Path


class ExitCode(IntEnum):
    """Process exit codes the CLI returns. Documented so automation can branch.

    Each failure class is a distinct code so an operator's wrapper can tell a
    bad config (no provisioning happened) from a failed provisioning step (the
    stack may be half-up) from a declined confirmation.
    """

    OK = 0
    USAGE = 1
    CONFIG = 2
    PREREQ = 3
    PROVISION = 4
    ABORTED = 5
    #: ``generate`` no llegó a dejar el árbol de arranque escrito. Se separa de
    #: PROVISION porque significan cosas distintas para quien recoge el error:
    #: con PROVISION puede haber contenedores levantados a medias; con GENERATE
    #: no se levantó nada y lo único que puede haber quedado a medias son
    #: ficheros bajo la raíz de datos.
    GENERATE = 6
    #: Una acción destructiva se ejecutó sólo EN PARTE: quedan datos en disco.
    #: Hoy lo devuelve `uninstall --purge-data` cuando el purgador no pudo
    #: borrarlo todo (un punto de montaje ocupado, un permiso denegado, un
    #: fichero abierto por un contenedor que sobrevivió al `down`). Es un código
    #: propio y no un PROVISION porque lo que tiene que hacer quien lo recoja es
    #: lo contrario de reintentar: NO dar la máquina por limpia — puede seguir
    #: ahí el `.env` con todos los secretos de la instalación.
    INCOMPLETE = 7
    #: La raíz de datos tiene una instalación previa que NO se puede releer, y
    #: seguir destruiría secretos irrecuperables. NO se ha escrito nada. Es un
    #: código propio y no un PREREQ (que se arregla y se reintenta) ni un ABORTED
    #: (que el operador eligió) porque reintentar el MISMO comando falla igual
    #: para siempre: hace falta que un humano recupere el `.env` o asuma la
    #: pérdida con `--force-new-secrets`.
    UNSAFE = 8
    #: Una excepción imprevista, ya traducida a un mensaje en stderr. Existe para
    #: que ningún fallo del sistema de ficheros —ni ningún otro— vuelva a salir
    #: como traza de Python terminando con un 1, que en esta tabla significa
    #: «argumentos mal» y manda a la automatización del operador al sitio
    #: equivocado.
    UNEXPECTED = 9


#: El one-shot de finalización del compose generado (init de Vault + siembra +
#: revelado), que corre DENTRO de la red del stack ya levantado — que es donde
#: tiene que correr y por lo que ``generate`` no lo hace. El nombre se cruza con
#: ``compose_generator.BOOTSTRAP_SERVICE`` en un test: son dos constantes en dos
#: ficheros, y si divergen el operador recibe un `no such service`.
BOOTSTRAP_SERVICE = "bootstrap"

#: ¿Se puede EJECUTAR ya ese one-shot? Desde el **2026-08-28, sí**.
#:
#: El paso 8 del ADR 0161 son dos mitades. La primera —este subcomando— estaba
#: hecha, y desde el 2026-08-27 el compose generado también DECLARA el servicio.
#: La segunda faltaba: la imagen del api-server no traía el módulo
#: ``api_server.bootstrap`` que el servicio ejecuta, así que
#: ``docker compose run --rm bootstrap`` respondía ``No module named
#: api_server.bootstrap`` y dejaba al operador con un stack ``Up (healthy)``
#: —el healthcheck de Vault acepta a propósito un Vault sellado—, sin Vault
#: inicializado, sin tenant, sin usuario admin y sin credenciales, después de que
#: el instalador le hubiera dicho que ese comando le daba todo eso.
#:
#: Ese módulo ya existe (`apps/api-server/src/api_server/bootstrap/`), así que el
#: banner vuelve a dar la orden sin reservas y las dos mitades se tocan de
#: verdad. Sigue siendo una bandera escrita a mano, y por eso sigue habiendo una
#: guarda que la cruza contra el árbol del repositorio
#: (``test_la_disponibilidad_del_bootstrap_declarada_coincide_con_el_arbol``): si
#: alguien borrase o renombrase el módulo sin tocar esto, el banner mandaría
#: ejecutar un comando que falla — que es exactamente lo que había antes.
BOOTSTRAP_ENTRYPOINT_AVAILABLE = True


def headless_pipeline() -> tuple[str, ...]:
    """Names of the ordered phases the CLI runs (prereqs → pipeline → finalize).

    The middle phases ARE the wizard's install pipeline; a test asserts the CLI
    runs the same provisioning steps in the same order as
    ``/api/install/stream``.
    """

    return ("prereqs", *(step.value for step in INSTALL_STEP_ORDER), "finalize")


class CliError(Exception):
    """A CLI failure carrying the :class:`ExitCode` the process should return.

    The message is shown to the operator (never carries a secret). The orchestration
    raises this; :func:`main` maps it to the exit code + a stderr line.
    """

    def __init__(self, message: str, code: ExitCode) -> None:
        super().__init__(message)
        self.code = code


@runtime_checkable
class CredentialBuilder(Protocol):
    """Builds the one-shot install credentials handed to the finalize reveal.

    The real binding (Phase B) returns the actual ``vault operator init`` output
    + the minted admin password; tests inject a fake that returns scripted
    values so the reveal is asserted with no real Vault.
    """

    def build(self, config: InstallerConfig) -> InstallCredentials:
        """Produce the credentials for *config* (after a successful install)."""
        ...

    def advisories(self) -> tuple[str, ...]:
        """Líneas que hay que imprimir JUNTO al revelado. Nunca un secreto.

        Existe porque el revelado es de una sola vez, sin recuperación y seguido
        de la autodestrucción del instalador: cualquier reserva sobre lo que se
        está enseñando —la más importante: «esta contraseña puede no ser la que
        abre la cuenta»— tiene que viajar pegada al dato o se pierde con él.
        Vacío es la respuesta normal.
        """
        ...


@dataclass
class StubCredentialBuilder:
    """A deterministic fake :class:`CredentialBuilder` (the test default).

    Returns scripted placeholder credentials so the finalize reveal is
    exercisable headlessly. NOT used in a real install — Phase B's binding
    returns the real Vault init output + minted admin password.
    """

    admin_username: str = "admin"
    admin_password: str = "stub-admin-password"  # - placeholder, not a real secret
    vault_root_token: str = "stub-root-token"  # - placeholder, not a real secret
    vault_unseal_keys: tuple[str, ...] = (
        "stub-unseal-1",
        "stub-unseal-2",
        "stub-unseal-3",
        "stub-unseal-4",
        "stub-unseal-5",
    )

    def build(self, config: InstallerConfig) -> InstallCredentials:
        username = self.admin_username
        # Prefer the configured tenant admin email as the username (same shape
        # as the wizard's build_install_credentials).
        if config.tenant.admin_email:
            username = str(config.tenant.admin_email)
        return InstallCredentials(
            admin_username=username,
            admin_password=self.admin_password,
            vault_root_token=self.vault_root_token,
            vault_unseal_keys=self.vault_unseal_keys,
        )

    def advisories(self) -> tuple[str, ...]:
        return ()


@dataclass
class RealCredentialBuilder:
    """Real :class:`CredentialBuilder` — reads what a :class:`RealStepExecutor`
    captured during the install (Plan prod-01 task_17 / secrets-1).

    No credential is minted here — y desde el ADR 0161 tampoco las mintea el
    ejecutor: las acuña el one-shot ``bootstrap`` dentro de la red del stack y el
    paso BOOTSTRAP_VAULT las LEE de su línea de revelado (root token, cinco
    unseal keys y contraseña de admin, las tres en la misma línea). They
    live ONLY in memory (redacted ``repr``s) for the one-time finalize reveal —
    never persisted in the repo tree (the sole secret-bearing write is the ``.env``
    at 0600 under the compose dir). A re-bootstrap (Vault already initialised →
    ``init is None``) or an un-run seed fails loud rather than revealing nothing.
    """

    executor: RealStepExecutor
    #: El depósito de emergencia. No se usa para construir nada: se consulta
    #: cuando NO hay init que revelar, para poder decir dónde quedaron las claves
    #: del intento anterior en vez de dejar al operador con «no hay credenciales
    #: que revelar» y sus cinco shares a un ``cat`` de distancia.
    escrow: KeyEscrow | None = None

    def build(self, config: InstallerConfig) -> InstallCredentials:
        result = self.executor.vault_bootstrap_result
        password = self.executor.seeded_admin_password
        init = result.init if result is not None else None
        if init is None or not password:
            raise CliError(self._nothing_to_reveal(), ExitCode.PROVISION)
        return InstallCredentials(
            admin_username=str(config.tenant.admin_email),
            admin_password=password,
            vault_root_token=init.root_token,
            vault_unseal_keys=init.unseal_keys,
        )

    def advisories(self) -> tuple[str, ...]:
        """Las reservas del ejecutor sobre la contraseña de admin que se revela."""

        return self.executor.admin_password_advisories()

    def _nothing_to_reveal(self) -> str:
        """El mensaje del caso sin init — con el depósito dentro si lo hay.

        El escenario real: un intento anterior inicializó Vault y murió antes del
        revelado (una sesión SSH caída durante la siembra del catálogo, que tarda
        minutos). Al relanzar, ``bootstrap_vault`` ve ``is_initialized()`` y se
        niega —correctamente— a re-inicializar, así que aquí no hay nada que
        enseñar. Sin el depósito, ese mensaje era un callejón sin salida; con él,
        el error ES el procedimiento de recuperación.
        """

        base = (
            "No hay credenciales reales que revelar: el bootstrap de Vault y/o "
            "la siembra del tenant no se completaron en esta ejecución."
        )
        pending = self.escrow.pending_path() if self.escrow is not None else None
        if pending is None:
            return base
        return (
            f"{base} Un intento anterior SÍ llegó a inicializar Vault y dejó sus "
            f"claves en {pending} (0600): cópialas de ahí, bórralo, y reanuda la "
            "instalación con --vault-unseal-keys-from apuntando a ese fichero."
        )


# ---------------------------------------------------------------------------
# Confirmers — supply the uninstall's confirmation gates (task 15_12).
# ---------------------------------------------------------------------------
@dataclass
class FlagConfirmer:
    """A non-interactive :class:`Confirmer` whose answers come from CLI flags.

    Used by ``--yes`` automation: :meth:`confirm_name` returns the typed
    ``--confirm-name`` value (so the double confirmation still requires the
    operator to have spelled the deployment name on the command line), and
    :meth:`confirm_yes` returns ``--yes`` for the second of the double
    confirmation AND for the purge's extra confirmation — but the purge gate
    ALSO requires ``--purge-data`` upstream, so ``--yes`` alone never wipes data
    that the operator did not opt into. Without ``--yes`` every yes/no gate
    answers *no* and the uninstall aborts.
    """

    confirm_name_value: str = ""
    yes: bool = False

    def confirm_name(self, prompt: str) -> str:  # noqa: ARG002 - prompt unused (non-interactive)
        return self.confirm_name_value

    def confirm_yes(self, prompt: str) -> bool:  # noqa: ARG002 - prompt unused (non-interactive)
        return self.yes


@dataclass
class InteractiveConfirmer:
    """A TTY-reading :class:`Confirmer` for an interactive ``uninstall`` run.

    Reads the typed deployment name and the yes/no answers from stdin. Only the
    interactive path uses ``input``; the headless ``--yes`` path uses
    :class:`FlagConfirmer`, so the test suite never blocks on a prompt.
    """

    def confirm_name(self, prompt: str) -> str:  # pragma: no cover - needs a TTY
        return input(prompt)

    def confirm_yes(self, prompt: str) -> bool:  # pragma: no cover - needs a TTY
        answer = input(prompt).strip().lower()
        return answer in ("y", "yes", "s", "si", "sí")


# ---------------------------------------------------------------------------
# Config loading — parse + validate install.yaml into an InstallerConfig.
# ---------------------------------------------------------------------------
def load_install_config(text: str) -> InstallerConfig:
    """Parse + validate the unattended ``install.yaml`` into an InstallerConfig.

    Rejects (with :class:`CliError` / :data:`ExitCode.CONFIG`) a YAML document
    that does not parse, is not a mapping, fails the per-field Pydantic
    validation (e.g. a bad domain, a missing required field), or fails the
    cross-field provider rules (at least one provider enabled + its creds). A
    rejected config means NO provisioning happened — the gate runs before any
    host-touching step. Secrets the file carries (MinIO secret, provider tokens)
    are captured into the model's ``SecretStr`` fields and never echoed back.
    """

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CliError(f"install.yaml no es YAML válido: {exc}", ExitCode.CONFIG) from exc

    if not isinstance(raw, dict):
        raise CliError(
            "install.yaml debe ser un mapping YAML con las claves de configuración "
            "(system, storage, providers, tenant…).",
            ExitCode.CONFIG,
        )

    try:
        config = InstallerConfig.model_validate(raw)
    except ValidationError as exc:
        raise CliError(
            f"install.yaml no pasó la validación de configuración:\n{exc}",
            ExitCode.CONFIG,
        ) from exc

    # Cross-field provider rules (mirrors the wizard's /api/config/validate).
    result = validate_config(config)
    if not result.valid:
        lines = "\n".join(f"  - {e.field}: {e.message}" for e in result.errors)
        raise CliError(
            f"install.yaml no pasó la validación de proveedores:\n{lines}",
            ExitCode.CONFIG,
        )

    return config


def _config_to_executor_payload(config: InstallerConfig) -> dict[str, object]:
    """Build the secret-free config echo the executor + finalize consume.

    Mirrors the secret-free normalised config the wizard posts to
    ``/api/install/stream``: it carries the non-secret tenant info (so the
    finalize step can derive the admin username) but NEVER the captured secrets.
    """

    return {
        "tenant": {
            "tenant_name": config.tenant.tenant_name,
            "admin_email": str(config.tenant.admin_email),
        },
        "system": {
            "domain": config.system.domain,
            "environment": config.system.environment.value,
        },
    }


# ---------------------------------------------------------------------------
# The headless orchestration — the same pipeline the wizard runs.
# ---------------------------------------------------------------------------
@dataclass
class HeadlessInstaller:
    """Runs the unattended install: prereq gate → wizard pipeline → finalize.

    Construct with the injectable seams (defaults are the in-memory stubs so the
    CLI is import-safe + testable with no host): the :class:`PrereqChecker`, the
    :class:`StepExecutor` that runs the provisioning pipeline, the
    :class:`CredentialBuilder` for the one-time reveal, the
    :class:`FinalizeService` that arms/serves it, and the output stream.

    :meth:`run` performs, in order:

      1. the prereq gate — a hard FAIL aborts with :data:`ExitCode.PREREQ`
         BEFORE any provisioning;
      2. the install pipeline — the SAME ordered steps the wizard's
         ``/api/install/stream`` runs, via :class:`InstallOrchestrator`. A step
         failure aborts with :data:`ExitCode.PROVISION`;
      3. finalize — arms the one-time reveal and prints the credentials +
         Vault unseal keys ONCE.

    Records the executed phase names in :attr:`phases` so a test can assert the
    headless run produces the same step pipeline as the wizard.
    """

    prereq_checker: PrereqChecker
    executor: StepExecutor
    credential_builder: CredentialBuilder
    finalize: FinalizeService
    out: TextIO
    #: El depósito de emergencia de las unseal keys. Lo ESCRIBE el ejecutor en
    #: cuanto Vault se inicializa; lo RETIRA :meth:`_run_finalize` en cuanto las
    #: claves están en pantalla. Tiene que ser el mismo objeto que el del
    #: ejecutor o el fichero con los cinco shares se queda en la máquina para
    #: siempre — cambiar una pérdida por una fuga.
    key_escrow: KeyEscrow | None = None
    #: The ordered phase names actually executed (for the same-pipeline assert).
    phases: list[str] = field(default_factory=list)

    def _log(self, message: str) -> None:
        """Emit one operator-facing log line. NEVER carries a secret."""

        print(message, file=self.out)

    def _run_prereqs(self) -> None:
        """Run the prereq gate; abort BEFORE provisioning on a hard FAIL."""

        self.phases.append("prereqs")
        self._log("[prereqs] Validando prerequisitos del host…")
        results = self.prereq_checker.check_all()
        blocking = [r for r in results if r.blocking]
        for r in results:
            mark = r.status.value.upper()
            self._log(f"[prereqs]   [{mark}] {r.label}: {r.detail}")
        if blocking:
            details = "; ".join(f"{r.label}: {r.remediation or r.detail}" for r in blocking)
            raise CliError(
                f"Prerequisitos no satisfechos, se aborta antes de provisionar: {details}",
                ExitCode.PREREQ,
            )
        self._log("[prereqs] Prerequisitos satisfechos.")

    def _run_pipeline(self, payload: dict[str, object]) -> None:
        """Run the wizard's install pipeline via the shared orchestrator."""

        orchestrator = InstallOrchestrator(executor=self.executor, config=payload)
        for event in orchestrator.run():
            # The orchestrator yields one event per step (running/log/ok) plus a
            # terminal event. Record each provisioning step the first time we see
            # its `running` transition so `phases` mirrors INSTALL_STEP_ORDER.
            if event.stage not in self.phases and event.stage != "done":
                self.phases.append(event.stage)
            self._log(f"[{event.stage}] {event.message}")
        if not orchestrator.completed:
            failed = next(
                (s for s in orchestrator.ordered_states if s.status.value == "failed"),
                None,
            )
            reason = failed.error if failed else "fallo desconocido en el pipeline."
            raise CliError(
                f"La instalación falló durante el aprovisionamiento: {reason}",
                ExitCode.PROVISION,
            )

    def _run_finalize(self, config: InstallerConfig) -> RevealPayload:
        """Arm + reveal the one-time credentials (printed to stdout ONCE)."""

        self.phases.append("finalize")
        self.finalize.arm(self.credential_builder.build(config))
        payload = self.finalize.reveal()
        self._log("")
        self._log("=" * 60)
        self._log(payload.warning_es)
        self._log("=" * 60)
        for cred in payload.credentials:
            self._log(f"  {cred.label_es}: {cred.secret}")
        for i, key in enumerate(payload.unseal_keys, start=1):
            self._log(f"  Unseal key #{i}: {key}")
        self._log("=" * 60)
        for note in self.credential_builder.advisories():
            self._log(note)
        # Las claves ya están en pantalla: el depósito de emergencia deja de
        # tener sentido y pasa a ser sólo un riesgo (cinco shares juntos en la
        # misma máquina que Vault). Se retira AQUÍ y no antes, porque hasta esta
        # línea el depósito era lo único que las sostenía.
        if self.key_escrow is not None:
            self.key_escrow.discard()
        return payload

    def run(self, config: InstallerConfig) -> RevealPayload:
        """Run the full unattended install for *config*; return the reveal.

        Raises :class:`CliError` (carrying the right :class:`ExitCode`) on a
        prereq or provisioning failure. On success the one-time reveal has been
        printed and the finalize step has triggered the installer self-destruct.
        """

        self._run_prereqs()
        payload_echo = _config_to_executor_payload(config)
        self._run_pipeline(payload_echo)
        return self._run_finalize(config)


# ---------------------------------------------------------------------------
# `generate` — escribe el árbol de arranque y sale (ADR 0161, opción D).
# ---------------------------------------------------------------------------
def _free_disk_bytes(path: str) -> int:
    """Espacio libre sobre *path*, en bytes.

    Desde dentro del contenedor del instalador esto mide el sistema de ficheros
    del HOST, porque la raíz de datos está bind-montada: es la única
    comprobación de prerequisitos que sigue siendo válida en el camino sin clon.
    """

    return shutil.disk_usage(path).free


@dataclass
class BootTreeGenerator:
    """Ejecuta ÚNICAMENTE ``GENERATE_CONFIG``, con los seams reales.

    Es deliberadamente la mitad de :class:`HeadlessInstaller`: no hay puerta de
    prerequisitos (no se comprueba Docker porque no se va a usar), no hay
    pipeline (un solo paso, así que no hace falta orquestador ni máquina de
    estados) y no hay finalize (las credenciales nacen en el one-shot de
    finalización, dentro de la red del stack, no aquí).

    Lo que sí hay es la propiedad que sostiene el diseño: **este camino no
    invoca a Docker**. Ni ``pull``, ni ``up``, ni ``run``. Un test lo afirma con
    un ``CommandRunner`` que revienta si alguien lo llama, porque es una
    propiedad invisible: el día que un refactor «aproveche» que el ejecutor ya
    sabe hablar con compose, nada fallaría en la suite y el contenedor
    simplemente dejaría de poder correr sin el socket del daemon.

    Registra el nombre de la fase ejecutada en :attr:`phases` para que un test
    pueda afirmar que fue exactamente una, y cuál.
    """

    executor: StepExecutor
    out: TextIO
    #: Sonda del disco libre sobre la raíz de datos, en bytes. Es la ÚNICA
    #: comprobación de prerequisitos que sigue siendo válida desde dentro del
    #: contenedor —la raíz está montada, así que mide el sistema de ficheros del
    #: host— y por eso se ejecuta de verdad en vez de listarse. Inyectable para
    #: que el test no dependa del disco de quien lo corre.
    #:
    #: Va por ``default_factory`` y no como default directo a propósito: una
    #: función puesta como valor por defecto de un campo sigue siendo un
    #: descriptor, así que ``self.free_disk_probe`` se ataría a la instancia y
    #: recibiría ``self`` como primer argumento.
    free_disk_probe: Callable[[str], int] = field(default_factory=lambda: _free_disk_bytes)
    #: Las fases realmente ejecutadas — debe ser ``["generate_config"]``.
    phases: list[str] = field(default_factory=list)

    def _log(self, message: str) -> None:
        """Emite una línea para el operador. NUNCA lleva un secreto."""

        print(message, file=self.out)

    def _disk_warning(self, data_root: str) -> str | None:
        """Aviso de disco, o ``None`` si hay de sobra o no se pudo medir.

        AVISO y no puerta: escribir el árbol de arranque en una máquina a la que
        todavía se le va a montar el disco de datos es legítimo, y abortar ahí
        sería inventar un bloqueo que el ``install`` desde el host tampoco impone
        en este punto. Y si la sonda no puede contestar, el árbol se escribe
        igual: el entregable de este subcomando son los ficheros, y una sonda
        informativa no puede impedirlos.
        """

        try:
            free_gib = self.free_disk_probe(data_root) / BYTES_PER_GIB
        except OSError:
            return None
        if free_gib >= DEFAULT_MIN_DISK_GIB:
            return None
        return "\n".join(
            (
                f"  AVISO: disco libre en {data_root}: {free_gib:.1f} GiB.",
                f"  Se requieren al menos {DEFAULT_MIN_DISK_GIB} GiB (imágenes + PGDATA +",
                "  almacén de objetos). Libera espacio o monta un disco mayor",
                "  ANTES del `up`.",
            )
        )

    def run(self, config: InstallerConfig) -> list[str]:
        """Escribe el árbol de arranque de *config*; devuelve sus líneas de log.

        Lanza :class:`CliError` con :data:`ExitCode.GENERATE` si la escritura
        falla (permisos sobre la raíz de datos montada, el guardián de secretos
        de producción, un auxiliar que no se puede leer del paquete…). No se
        arrastra a :data:`ExitCode.PROVISION` a propósito: aquí no se ha
        levantado nada, y quien recoja el código de salida necesita saberlo.
        """

        root = config.storage.data_root
        step = InstallStep.GENERATE_CONFIG
        self.phases.append(step.value)
        self._log(f"[{step.value}] {INSTALL_STEP_TITLES_ES[step]}…")
        try:
            lines = self.executor.execute(step, _config_to_executor_payload(config))
        except StepExecutionError as exc:
            raise CliError(
                f"No se pudo generar el árbol de arranque: {exc}",
                ExitCode.GENERATE,
            ) from exc
        for line in lines:
            self._log(f"[{step.value}] {line}")
        self._log(_next_steps_banner(config, disk_warning=self._disk_warning(root)))
        return lines


def _prereq_advisories() -> list[str]:
    """Lo que este camino NO puede comprobar, y el operador sí.

    El contenedor no habla con Docker (es LA propiedad de la opción D) y tiene su
    propia netns, así que la versión del daemon, la de Compose y los puertos
    publicados del host son invisibles desde aquí; la RAM que ve ``/proc`` puede
    ser la del host o la de un cgroup, así que tampoco es una respuesta fiable.
    Todas esas comprobaciones EXISTEN en :mod:`installer_backend.prereqs`, con
    mensajes de remediación buenos, y en este camino no las corre nadie: el
    operador se enteraba de que otro servicio tenía el 443 al ejecutar
    ``up -d --wait``, con parte del stack ya levantada.

    Los umbrales salen de las constantes de ``prereqs``, no de literales: si
    alguien sube un mínimo, esta lista lo sigue sola.
    """

    ports = " y ".join(str(p) for p in REQUIRED_FREE_PORTS)
    return [
        f"    - Puertos {ports} LIBRES: el reverse proxy (Caddy) es el único",
        "      servicio que publica puertos (ADR 0061) y no arrancará si otro",
        "      proceso los tiene.",
        f"    - Docker Engine >= {MIN_DOCKER_VERSION[0]}.{MIN_DOCKER_VERSION[1]}"
        " (cap-drop, seccomp, rootfs de solo lectura).",
        f"    - Docker Compose >= {MIN_COMPOSE_VERSION[0]}.{MIN_COMPOSE_VERSION[1]}:"
        " por debajo, el `--wait` sobre el",
        "      one-shot `migrations` puede colgarse o dar un falso fallo.",
        f"    - RAM total >= {DEFAULT_MIN_RAM_GIB} GiB"
        " (PostgreSQL+pgvector, Redis, MinIO, Vault, API, workers).",
    ]


def _remaining_commands(root: str) -> list[str]:
    """Los comandos que le quedan al operador — sólo los que EXISTEN.

    **Desde el 2026-08-28 son dos**, porque la segunda mitad del paso 8 del ADR
    0161 ya está: la imagen del api-server trae el módulo
    :data:`BOOTSTRAP_ENTRYPOINT`, así que el one-shot de finalización se puede
    ejecutar de verdad y el banner vuelve a ordenarlo sin reservas.

    La rama de abajo se conserva —y con ella :data:`BOOTSTRAP_ENTRYPOINT_AVAILABLE`—
    porque la regla no era «esperar a ese módulo», era «no mandar ejecutar lo que
    no existe». Si alguien borrara o renombrara el módulo, la guarda que cruza la
    bandera con el árbol se pone roja y el banner vuelve a decir la verdad: sin
    él, el comando responde ``No module named …`` y deja al operador con un stack
    ``Up (healthy)`` —el healthcheck de Vault acepta a propósito un Vault
    sellado— sin Vault inicializado, sin tenant, sin admin y sin credenciales,
    creyendo que ha terminado. Se nombra el módulo que falta a propósito: es lo
    que convierte el error en un diagnóstico en vez de en una sospecha sobre el
    Docker del operador.

    **Lo que este banner dejó de decir el 2026-08-28**, y por qué importa: remitía
    al ``install`` desde el host como «el camino que SÍ termina hoy». No
    terminaba. El paso 4 del ``install`` hablaba con Vault contra
    ``127.0.0.1:8200`` y el servicio ``vault`` del compose generado **no publica
    ningún puerto** —el único que publica es Caddy, ADR 0061—, así que moría con
    una traza cruda. La salida de emergencia estaba tan rota como el camino del
    que se salía, y decirlo mal es peor que no decir nada: manda al operador a
    gastar una instalación entera para llegar al mismo sitio. Desde que el
    ``install`` delega en este mismo one-shot, las dos mitades comparten destino:
    o llegan las dos, o no llega ninguna.
    """

    if BOOTSTRAP_ENTRYPOINT_AVAILABLE:
        return [
            "  Quedan DOS comandos, y los ejecutas TÚ:",
            "",
            f"    1) cd {root} && docker compose up -d --wait",
            f"    2) docker compose run --rm {BOOTSTRAP_SERVICE}",
            "",
            "  El (2) inicializa Vault, siembra el tenant inicial y muestra las",
            "  credenciales UNA sola vez. Corre dentro de la red del stack, que es",
            "  donde tiene que correr. GUARDA lo que imprime antes de cerrar la",
            "  terminal: las unseal keys y el root token no tienen recuperación.",
        ]
    return [
        "  Queda UN comando que ejecutas TÚ:",
        "",
        f"    1) cd {root} && docker compose up -d --wait",
        "",
        "  Y falta la FINALIZACIÓN —init de Vault, siembra del tenant inicial y",
        "  revelado de credenciales—, que hoy está NO DISPONIBLE por NINGÚN camino.",
        f"  El compose declara el servicio `{BOOTSTRAP_SERVICE}`, pero esta imagen",
        f"  del api-server no trae el módulo `{BOOTSTRAP_ENTRYPOINT}` que ejecuta:",
        f"  `docker compose run --rm {BOOTSTRAP_SERVICE}` responde",
        f"  «No module named {BOOTSTRAP_ENTRYPOINT}» y no hace nada. Es la segunda",
        "  mitad del paso 8 del ADR 0161, y aterrizó el 2026-08-28: si estás",
        "  leyendo esto, tu imagen es anterior. NO lo ejecutes; reconstrúyela.",
        "",
        "  El `install` desde el host TAMPOCO lo suple: desde el 2026-08-28",
        "  ejecuta ese mismo one-shot en vez de hablar con Vault por su cuenta,",
        "  porque el servicio `vault` no publica ningún puerto y sólo es",
        "  alcanzable desde dentro de la red del stack (ADR 0061). Antes de eso",
        "  no es que funcionara: moría en el paso 4 con una traza.",
        "",
        "  Lo que el (1) SÍ te deja, y es real: el stack levantado y sano sobre",
        "  el árbol de arranque de este install.yaml. Lo que falta es la",
        "  finalización, y llega con el módulo de arriba. Ver el runbook",
        "  docs/06-runbooks/01-installation-from-scratch.md.",
    ]


def _next_steps_banner(config: InstallerConfig, *, disk_warning: str | None = None) -> str:
    """Lo que le queda al operador: los comandos, y lo que tiene que comprobar él.

    La opción D convierte una línea en tres. Si el instalador se limitara a
    terminar en verde, el modo de fallo sería el peor de todos: no un error, sino
    un operador convencido de que el stack está levantado cuando lo único que
    existe es un directorio con ficheros. El banner es, literalmente, la parte de
    la interfaz que paga el precio del diseño — y por eso tiene dos reglas: sólo
    manda ejecutar lo que existe (:func:`_remaining_commands`), y dice lo que
    no ha podido comprobar (:func:`_prereq_advisories`).
    """

    root = config.storage.data_root
    lines = [
        "",
        "=" * 68,
        "  Árbol de arranque generado. El instalador NO ha tocado Docker:",
        "  ni pull, ni up, ni bootstrap de Vault, ni siembra (ADR 0161, opción D:",
        "  montar el socket del daemon sería acceso root al host).",
        "",
        *_remaining_commands(root),
        "",
        "-" * 68,
        "  Este camino NO ejecuta la puerta de prerequisitos: desde dentro del",
        "  contenedor no se ven ni el daemon Docker ni los puertos del host.",
        "  COMPRUEBA a mano, antes del `up`:",
        "",
        *_prereq_advisories(),
    ]
    if disk_warning is not None:
        lines += ["", disk_warning]
    lines.append("=" * 68)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Seam factory — built fresh per run so the in-memory stubs don't leak state.
# Phase B / tests override these with real / fake bindings.
# ---------------------------------------------------------------------------
#: The simulation seams (used by ``--dry-run``). The no-silent-stubs guard
#: (:func:`_assert_real_install_seams`) rejects these when ``--dry-run`` is absent
#: so a fake install can never masquerade as a real one (task_prod01_19 / deploy-1).
#: :func:`_assert_real_generate_seams` lee la MISMA lista —sin la salida del
#: ``--dry-run``, que ``generate`` no tiene— para que añadir un fake nuevo aquí
#: cubra los dos caminos y no haya que acordarse del segundo.
_SIMULATION_INSTALL_SEAMS = (FakeStepExecutor, StubPrereqChecker)
_SIMULATION_UNINSTALL_SEAMS = (StubStackTeardown, StubDataPurger)
#: Los seams de simulación del `reinstall`. Incluye los dos del uninstall porque
#: la mitad destructiva de una reinstalación limpia ES la del uninstall, más los
#: dos propios: el detector (que respondía SIEMPRE «no hay instalación previa»)
#: y el cargador de secretos existentes.
_SIMULATION_REINSTALL_SEAMS = (
    StubInstallDetector,
    StubExistingSecretLoader,
    StubStackTeardown,
    StubDataPurger,
)

_SIMULATION_BANNER = (
    "====================================================================\n"
    "  SIMULACIÓN (--dry-run): NO se aprovisiona NADA. No se arranca el\n"
    "  stack, no se migra, no se siembra. Las credenciales mostradas son\n"
    "  FALSAS (placeholders). NO uses esto como instalación real.\n"
    "===================================================================="
)


def _default_inspector() -> DataRootInspector:
    """El inspector con los bindings reales. Construirlo no toca el host."""

    return DataRootInspector(reader=RealFileReader(), writer=RealEnvFileWriter())


def _resolve_secrets(
    out: TextIO,
    config: InstallerConfig,
    inspector: SecretResolver | None,
    *,
    force_new: bool,
) -> GeneratedSecrets:
    """Decide con qué secretos se instala, lo dice, y aborta si no es seguro.

    Ésta es la línea que faltaba. Hasta el 2026-08-27 los dos constructores
    llamaban a ``generate_secrets()`` a secas, así que **una segunda ejecución
    sobre la misma raíz de datos acuñaba secretos nuevos encima de datos
    viejos**: el PGDATA se quedaba con la contraseña del primer intento y el
    ``.env`` con la del segundo, ``up --wait`` no terminaba nunca, y cada
    reintento empeoraba la situación. La contraseña original vivía sólo en el
    ``.env`` recién pisado, así que no había reconciliación posible.

    Se hace aquí, en la construcción, y no dentro del pipeline, por una razón
    concreta: en este punto todavía no se ha escrito nada, así que negarse deja
    la máquina exactamente como estaba.
    """

    decision: SecretDecision
    try:
        decision = (inspector or _default_inspector()).resolve_secrets(
            config.storage.data_root, force_new=force_new
        )
    except UnsafeOverwriteError as exc:
        raise CliError(str(exc), ExitCode.UNSAFE) from exc
    for note in decision.notes:
        print(f"[secretos] {note}", file=out)
    return decision.secrets


def _load_unseal_keys(path: str | None) -> tuple[str, ...]:
    """Lee las unseal keys que aporta el operador (``--vault-unseal-keys-from``).

    De un FICHERO y no de un flag con la clave dentro: un share de Shamir en
    ``argv`` queda a la vista de cualquier usuario del host en ``ps`` y en el
    historial del shell. El formato es el del depósito de emergencia, así que
    reanudar una instalación interrumpida es apuntar este flag al fichero que el
    propio instalador dejó.
    """

    if not path:
        return ()
    from pathlib import Path

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(
            f"No se pudo leer el fichero de unseal keys {path!r}: {exc}",
            ExitCode.CONFIG,
        ) from exc
    keys = read_unseal_keys(text)
    if not keys:
        raise CliError(
            f"El fichero de unseal keys {path!r} no contiene ninguna clave. Se "
            "espera una por línea (o líneas `unseal_key: <share>`); los "
            "comentarios `#` se ignoran.",
            ExitCode.CONFIG,
        )
    return keys


def build_default_installer(
    out: TextIO,
    config: InstallerConfig,
    *,
    dry_run: bool = False,
    force_new_secrets: bool = False,
    inspector: SecretResolver | None = None,
    unseal_keys_path: str | None = None,
) -> HeadlessInstaller:
    """Build a :class:`HeadlessInstaller` with the REAL host bindings by default.

    ``dry_run=True`` wires the in-memory simulation seams instead (FakeStepExecutor
    / StubPrereqChecker / StubCredentialBuilder) for an explicitly-marked dry run.
    Otherwise it wires the real provisioner: a :class:`RealStepExecutor` writing
    config + driving ``docker compose`` under ``{data_root}`` (the compose dir),
    real prereq probes, and a :class:`RealCredentialBuilder` reading the captured
    Vault init + seeded admin password.

    Dos cosas ocurren aquí y no dentro del pipeline, y las dos por el mismo
    motivo —que en este punto no se ha escrito nada todavía—: la resolución de
    secretos (:func:`_resolve_secrets`, que puede NEGARSE) y la lectura de las
    unseal keys aportadas. El resto de seams reales sólo tocan el host cuando
    :meth:`HeadlessInstaller.run` se ejecuta.
    """

    lifecycle: InstallerLifecycle = StubInstallerLifecycle()
    if dry_run:
        return HeadlessInstaller(
            prereq_checker=StubPrereqChecker(),
            executor=FakeStepExecutor(),
            credential_builder=StubCredentialBuilder(),
            finalize=FinalizeService(lifecycle=lifecycle),
            out=out,
        )

    compose_dir = config.storage.data_root
    secrets = _resolve_secrets(out, config, inspector, force_new=force_new_secrets)
    escrow = FileKeyEscrow(data_root=compose_dir, store=RealEscrowFile())
    executor = RealStepExecutor(
        compose_dir=compose_dir,
        runner=SubprocessRunner(),
        env_writer=RealEnvFileWriter(),
        tree=RealDataTreeProvisioner(),
        cfg=config,
        secrets=secrets,
        key_escrow=escrow,
        existing_unseal_keys=_load_unseal_keys(unseal_keys_path),
    )
    return HeadlessInstaller(
        prereq_checker=RealPrereqChecker(probe=SystemHostProbe(data_path=compose_dir)),
        executor=executor,
        credential_builder=RealCredentialBuilder(executor, escrow=escrow),
        finalize=FinalizeService(lifecycle=lifecycle),
        out=out,
        # El MISMO objeto que el del ejecutor: uno deposita, el otro retira. Con
        # dos instancias distintas el fichero con los cinco shares se quedaría en
        # la máquina para siempre.
        key_escrow=escrow,
    )


def build_default_generator(
    out: TextIO,
    config: InstallerConfig,
    *,
    force_new_secrets: bool = False,
    inspector: SecretResolver | None = None,
) -> BootTreeGenerator:
    """Construye el :class:`BootTreeGenerator` con el ejecutor REAL. Sin variantes.

    No acepta ``dry_run``: ``generate`` no tiene simulación (ver el docstring del
    módulo). El :class:`RealStepExecutor` se cablea entero —incluido el ``runner``
    de subprocesos, que este camino no usará— porque el ejecutor es una pieza
    compartida con ``install``; lo que garantiza que no se toque Docker no es
    amputar el ejecutor, sino que :meth:`BootTreeGenerator.run` sólo le pide
    ``GENERATE_CONFIG``.

    La resolución de secretos es la misma que la de ``install`` y es
    especialmente necesaria AQUÍ: el contenedor no deja rastro, así que relanzar
    ``generate`` parece gratis — y era la forma más fácil de pisar el ``.env`` de
    una instalación a medio hacer.
    """

    compose_dir = config.storage.data_root
    executor = RealStepExecutor(
        compose_dir=compose_dir,
        runner=SubprocessRunner(),
        env_writer=RealEnvFileWriter(),
        tree=RealDataTreeProvisioner(),
        cfg=config,
        secrets=_resolve_secrets(out, config, inspector, force_new=force_new_secrets),
    )
    return BootTreeGenerator(executor=executor, out=out)


def _assert_real_generate_seams(generator: BootTreeGenerator) -> None:
    """Aborta si ``generate`` se cableó con un ejecutor de SIMULACIÓN.

    Sin ``dry_run`` que la abra, a diferencia de la guarda de ``install``: una
    generación simulada no informa de nada, porque el entregable ES el árbol de
    ficheros. Lo que produciría es el fallo que hoy tiene el wizard HTTP — log
    en verde, raíz de datos vacía— y el operador lo descubriría en el ``up``.
    """

    if isinstance(generator.executor, _SIMULATION_INSTALL_SEAMS):
        raise CliError(
            "Abortado: `generate` tiene un ejecutor de SIMULACIÓN cableado "
            f"({type(generator.executor).__name__}). No existe --dry-run para "
            "`generate`: simular la escritura del árbol de arranque no informa de "
            "nada, y dejaría la raíz de datos vacía con un log en verde.",
            ExitCode.GENERATE,
        )


def _assert_real_install_seams(installer: HeadlessInstaller, *, dry_run: bool) -> None:
    """Fail loud if a simulation seam is wired without ``--dry-run`` (deploy-1).

    Inspects the attributes that actually exist on :class:`HeadlessInstaller`
    (``prereq_checker`` / ``executor``); a stub there without ``--dry-run`` means
    the run would silently fake an install, so we abort with a clear message.
    """

    if dry_run:
        return
    offenders = sorted(
        type(seam).__name__
        for seam in (installer.prereq_checker, installer.executor)
        if isinstance(seam, _SIMULATION_INSTALL_SEAMS)
    )
    if offenders:
        raise CliError(
            "Abortado: el instalador tiene seams de SIMULACIÓN cableados sin "
            f"--dry-run ({', '.join(offenders)}). Usa --dry-run para una "
            "simulación explícita (no instala nada, credenciales FALSAS), o "
            "ejecuta con los bindings reales.",
            ExitCode.PROVISION,
        )


def _assert_real_uninstall_seams(uninstaller: Uninstaller, *, dry_run: bool) -> None:
    """Fail loud if an uninstall simulation seam is wired without ``--dry-run``."""

    if dry_run:
        return
    offenders = sorted(
        type(seam).__name__
        for seam in (uninstaller.teardown, uninstaller.purger)
        if isinstance(seam, _SIMULATION_UNINSTALL_SEAMS)
    )
    if offenders:
        raise CliError(
            "Abortado: el desinstalador tiene seams de SIMULACIÓN cableados sin "
            f"--dry-run ({', '.join(offenders)}). Usa --dry-run para simular.",
            ExitCode.PROVISION,
        )


def _assert_real_reinstall_seams(reinstaller: Reinstaller, *, dry_run: bool) -> None:
    """Fail loud if a reinstall simulation seam is wired without ``--dry-run``.

    La MISMA forma que las de install y uninstall, y existe por la misma razón
    llevada al extremo: `reinstall` se quedó cableado a los cuatro stubs y salía
    en verde sin detectar, sin parar, sin cargar los secretos y sin reinstalar,
    mientras dos runbooks lo señalaban como el camino para actualizar. Un
    subcomando destructivo que simula en silencio es peor que uno que falla.
    """

    if dry_run:
        return
    offenders = sorted(
        type(seam).__name__
        for seam in (
            reinstaller.detector,
            reinstaller.secret_loader,
            reinstaller.teardown,
            reinstaller.purger,
        )
        if isinstance(seam, _SIMULATION_REINSTALL_SEAMS)
    )
    if offenders:
        raise CliError(
            "Abortado: la reinstalación tiene seams de SIMULACIÓN cableados sin "
            f"--dry-run ({', '.join(offenders)}). Usa --dry-run para una "
            "simulación explícita (no detecta, no para, no borra y no "
            "reinstala), o ejecuta con los bindings reales.",
            ExitCode.PROVISION,
        )


# ---------------------------------------------------------------------------
# Argument parsing + the `install` / `uninstall` subcommands.
# ---------------------------------------------------------------------------
def _add_force_new_secrets(parser: argparse.ArgumentParser) -> None:
    """La puerta de emergencia de la reutilización de secretos.

    Va en los DOS subcomandos que escriben el ``.env`` (``install`` y
    ``generate``) y en ninguno más. Apagada por defecto: una puerta de emergencia
    abierta no es una puerta, y lo que hay al otro lado es la pérdida de los
    datos que ya haya en disco.
    """

    parser.add_argument(
        FORCE_FLAG,
        dest="force_new_secrets",
        action="store_true",
        help=(
            "Acuña secretos NUEVOS aunque ya exista un .env bajo la raíz de "
            "datos. DESTRUCTIVO en la práctica: PostgreSQL, MinIO y las columnas "
            "cifradas con Fernet quedan inaccesibles con la configuración nueva. "
            "Sin este flag, una raíz de datos con una instalación previa que no "
            "se puede releer aborta con código 8 (UNSAFE) sin escribir nada. La "
            "copia del .env anterior se hace igualmente."
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (``generate`` / ``install`` / ``uninstall`` /
    ``reinstall``).

    ``install`` provisiona de punta a punta desde el host; ``generate`` sólo
    escribe el árbol de arranque y sale, que es la forma que puede vivir dentro
    de un contenedor sin el socket del daemon (ADR 0161, opción D).
    """

    parser = argparse.ArgumentParser(
        prog="installer_backend.cli",
        description=(
            "Instalador desatendido de la plataforma agéntica. Ejecuta la misma "
            "orquestación que el wizard de forma headless desde un install.yaml."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    install = sub.add_parser(
        "install",
        help="Instala el stack de forma desatendida desde un fichero de configuración.",
    )
    install.add_argument(
        "--config",
        required=True,
        metavar="install.yaml",
        help="Ruta al fichero YAML de configuración de la instalación.",
    )
    install.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "SIMULACIÓN explícita: no aprovisiona nada (no docker, no migración, "
            "no Vault, no seed) y muestra credenciales FALSAS. Sin este flag, un "
            "instalador con seams de simulación aborta con error (no se permite "
            "una instalación falsa silenciosa)."
        ),
    )
    _add_force_new_secrets(install)
    install.add_argument(
        "--vault-unseal-keys-from",
        default=None,
        metavar="FICHERO",
        help=(
            "Fichero con las unseal keys de un Vault YA inicializado (una por "
            "línea; se ignoran los comentarios `#`), para reanudar una "
            "instalación interrumpida en vez de morir con «Vault ya está "
            "inicializado y sellado». Se pide un FICHERO y no la clave en la "
            "línea de comandos porque un share de Shamir en argv queda a la "
            "vista en `ps` y en el historial del shell. Sirve directamente el "
            "fichero que el propio instalador deja si se interrumpe."
        ),
    )

    generate = sub.add_parser(
        "generate",
        help=(
            "Escribe el árbol de arranque (compose, .env, config, Caddyfile y "
            "auxiliares) bajo la raíz de datos y SALE. No toca Docker: el `up` y "
            "la finalización los ejecuta el operador (ADR 0161, opción D)."
        ),
        description=(
            "Genera la configuración de la plataforma sin provisionar nada. "
            "Pensado para ejecutarse dentro del contenedor del instalador con "
            "sólo la raíz de datos montada: no necesita el socket del daemon "
            "Docker, que es acceso root al host (ADR 0060). Al terminar imprime "
            "los dos comandos que quedan por ejecutar."
        ),
    )
    generate.add_argument(
        "--config",
        required=True,
        metavar="install.yaml",
        help="Ruta al fichero YAML de configuración de la instalación.",
    )
    _add_force_new_secrets(generate)
    # NOTA: `generate` NO tiene --dry-run, y es intencionado. Ver
    # `_assert_real_generate_seams`: el entregable de este subcomando es el árbol
    # de ficheros, así que simularlo sólo produce un log en verde sobre una raíz
    # de datos vacía — el defecto que hoy tiene el wizard HTTP.

    uninstall = sub.add_parser(
        "uninstall",
        help=(
            "Detiene y elimina el stack (DESTRUCTIVO). Requiere doble "
            "confirmación; conserva los datos salvo --purge-data."
        ),
    )
    uninstall.add_argument(
        "--deployment-name",
        default="agentic-platform",
        metavar="NAME",
        help=(
            "Nombre del despliegue (proyecto compose) a eliminar. Hay que "
            "teclearlo en --confirm-name para confirmar."
        ),
    )
    uninstall.add_argument(
        "--data-root",
        default="/data/agent-platform",
        metavar="PATH",
        help="Raíz de datos que --purge-data eliminaría (por defecto se conserva).",
    )
    uninstall.add_argument(
        "--confirm-name",
        default="",
        metavar="NAME",
        help=(
            "Primera confirmación: teclea el nombre EXACTO del despliegue. Debe "
            "coincidir con --deployment-name."
        ),
    )
    uninstall.add_argument(
        "--yes",
        action="store_true",
        help=(
            "Segunda confirmación: confirma explícitamente el borrado del stack "
            "(la doble confirmación necesita --confirm-name Y --yes)."
        ),
    )
    uninstall.add_argument(
        "--purge-data",
        action="store_true",
        help=(
            "TAMBIÉN borra de forma irreversible los datos bajo --data-root. "
            "Requiere su propia confirmación extra (--yes); por defecto los "
            "datos se conservan."
        ),
    )
    uninstall.add_argument(
        "--interactive",
        action="store_true",
        help="Pide las confirmaciones por terminal en lugar de derivarlas de los flags.",
    )
    uninstall.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "SIMULACIÓN explícita: no detiene el stack ni borra datos (seams "
            "stub). Sin este flag se ejecuta el teardown/purga REAL (tras la "
            "doble confirmación)."
        ),
    )

    reinstall = sub.add_parser(
        "reinstall",
        help=(
            "Reinstala sobre un despliegue existente. Por defecto PRESERVA los "
            "datos y reutiliza los secretos; --fresh borra todo (doble confirmación)."
        ),
    )
    reinstall.add_argument(
        "--deployment-name",
        default="agentic-platform",
        metavar="NAME",
        help=(
            "Nombre del despliegue (proyecto compose) a detectar/reinstalar. En "
            "modo --fresh hay que teclearlo en --confirm-name para confirmar."
        ),
    )
    reinstall.add_argument(
        "--config",
        required=True,
        metavar="install.yaml",
        help=(
            "Ruta al fichero YAML con el que reinstalar. Obligatorio: una "
            "reinstalación REGENERA la configuración y el compose, así que "
            "necesita el mismo install.yaml que una instalación. La raíz de "
            "datos sale de ahí (storage.data_root), no de un flag aparte."
        ),
    )
    reinstall.add_argument(
        "--fresh",
        action="store_true",
        help=(
            "Reinstalación LIMPIA: borra los datos existentes y regenera todos los "
            "secretos. Requiere doble confirmación (--confirm-name Y --yes). Sin "
            "--fresh se PRESERVAN los datos y se reutilizan los secretos existentes."
        ),
    )
    reinstall.add_argument(
        "--confirm-name",
        default="",
        metavar="NAME",
        help=(
            "Primera confirmación de --fresh: teclea el nombre EXACTO del "
            "despliegue. Debe coincidir con --deployment-name."
        ),
    )
    reinstall.add_argument(
        "--yes",
        action="store_true",
        help="Segunda confirmación de --fresh: confirma explícitamente el borrado.",
    )
    reinstall.add_argument(
        "--interactive",
        action="store_true",
        help="Pide las confirmaciones por terminal en lugar de derivarlas de los flags.",
    )
    reinstall.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "SIMULACIÓN explícita: no detecta, no para el stack, no borra y no "
            "reinstala (seams stub). Sin este flag se ejecuta la reinstalación "
            "REAL. Una reinstalación con seams de simulación y sin este flag "
            "aborta con error: no se permite una reinstalación falsa silenciosa."
        ),
    )
    return parser


def _read_config_file(path: str) -> str:
    """Read the install.yaml from disk; raise :class:`CliError` if unreadable."""

    from pathlib import Path  # local import: only the file read touches disk

    p: Path = Path(path)
    try:
        return p.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliError(
            f"No se pudo leer el fichero de configuración {path!r}: {exc}",
            ExitCode.CONFIG,
        ) from exc


def run_install(
    config_path: str,
    *,
    installer: HeadlessInstaller | None = None,
    out: TextIO | None = None,
    dry_run: bool = False,
    force_new_secrets: bool = False,
    unseal_keys_path: str | None = None,
    inspector: SecretResolver | None = None,
) -> ExitCode:
    """Load *config_path* and run the unattended install.

    Returns the :class:`ExitCode`. ``installer`` is injectable (tests pass one
    wired to fakes / failure scenarios); when omitted a default installer is
    built with the REAL host bindings (``dry_run=True`` wires the simulation
    seams). The config gate runs FIRST, so a malformed config returns
    :data:`ExitCode.CONFIG` with no provisioning attempted. The no-silent-stubs
    guard then aborts a simulation-wired run unless ``--dry-run`` was passed.
    """

    stream = out if out is not None else sys.stdout

    text = _read_config_file(config_path)
    config = load_install_config(text)

    inst = (
        installer
        if installer is not None
        else build_default_installer(
            stream,
            config,
            dry_run=dry_run,
            force_new_secrets=force_new_secrets,
            inspector=inspector,
            unseal_keys_path=unseal_keys_path,
        )
    )
    _assert_real_install_seams(inst, dry_run=dry_run)
    if dry_run:
        print(_SIMULATION_BANNER, file=stream)
    inst.run(config)
    return ExitCode.OK


def run_generate(
    config_path: str,
    *,
    generator: BootTreeGenerator | None = None,
    out: TextIO | None = None,
    force_new_secrets: bool = False,
    inspector: SecretResolver | None = None,
) -> ExitCode:
    """Carga *config_path* y escribe el árbol de arranque. NO toca Docker.

    Mismo orden de puertas que :func:`run_install`: primero la configuración (un
    YAML malo devuelve :data:`ExitCode.CONFIG` sin haber escrito nada), después la
    guarda anti-simulación, y sólo entonces la escritura. ``generator`` es
    inyectable para los tests; sin él se construye el cableado real.

    Devuelve :data:`ExitCode.OK` tras imprimir los dos comandos que le quedan al
    operador; lanza :class:`CliError` con :data:`ExitCode.GENERATE` si la
    generación falla.
    """

    stream = out if out is not None else sys.stdout

    text = _read_config_file(config_path)
    config = load_install_config(text)

    gen = (
        generator
        if generator is not None
        else build_default_generator(
            stream, config, force_new_secrets=force_new_secrets, inspector=inspector
        )
    )
    _assert_real_generate_seams(gen)
    gen.run(config)
    return ExitCode.OK


def run_uninstall(
    *,
    deployment_name: str,
    data_root: str,
    purge_data: bool,
    confirm_name: str,
    yes: bool,
    interactive: bool = False,
    uninstaller: Uninstaller | None = None,
    out: TextIO | None = None,
    dry_run: bool = False,
) -> ExitCode:
    """Run the gated uninstall; return the :class:`ExitCode`.

    Resolves the confirmation source: an interactive run reads the gates from a
    TTY (:class:`InteractiveConfirmer`); the default ``--yes`` automation path
    derives them from the flags (:class:`FlagConfirmer` — the operator must have
    typed ``--confirm-name`` AND passed ``--yes``). ``uninstaller`` is injectable
    (tests pass one wired to recording / scripted fakes); when omitted a default
    stub-wired uninstaller is built. A failed confirmation maps to
    :data:`ExitCode.ABORTED` with NOTHING removed.
    """

    stream = out if out is not None else sys.stdout

    if uninstaller is None:
        confirmer: Confirmer
        if interactive:
            confirmer = InteractiveConfirmer()
        else:
            confirmer = FlagConfirmer(confirm_name_value=confirm_name, yes=yes)
        uninstaller = build_default_uninstaller(
            stream, confirmer, dry_run=dry_run, compose_dir=data_root
        )
    _assert_real_uninstall_seams(uninstaller, dry_run=dry_run)
    if dry_run:
        print(_SIMULATION_BANNER, file=stream)

    req = UninstallRequest(
        deployment_name=deployment_name,
        data_root=data_root,
        purge_data=purge_data,
    )
    try:
        result = uninstaller.run(req)
    except UninstallAbortedError as exc:
        raise CliError(str(exc), ExitCode.ABORTED) from exc

    if result.leftovers:
        # El log ya ha dicho qué sobrevivió; aquí lo que importa es el CÓDIGO,
        # porque un 0 haría que un script de decomiso diese la máquina por
        # limpia con los datos —y el .env— todavía dentro.
        survivors = "; ".join(f"{left.path} ({left.reason})" for left in result.leftovers)
        raise CliError(
            f"Desinstalación con purga INCOMPLETA: quedan datos en disco: {survivors}. "
            "La máquina NO está limpia: revisa esas rutas antes de darla de baja, "
            "reasignarla o venderla.",
            ExitCode.INCOMPLETE,
        )
    return ExitCode.OK


def run_reinstall(
    config_path: str,
    *,
    deployment_name: str,
    fresh: bool,
    confirm_name: str,
    yes: bool,
    interactive: bool = False,
    reinstaller: Reinstaller | None = None,
    installer: HeadlessInstaller | None = None,
    preserve_executor: StepExecutor | None = None,
    out: TextIO | None = None,
    dry_run: bool = False,
) -> ExitCode:
    """Reinstall over an existing deployment; return the :class:`ExitCode`.

    Two halves, and until 2026-08-27 only the first existed:

    1. **decide** — detect the existing deployment and either preserve it,
       wipe it (FRESH, gated by the same double confirmation as the uninstall)
       or fall through to a first install. The confirmation source mirrors the
       uninstall: a TTY with ``--interactive``, otherwise the flags
       (:class:`FlagConfirmer` — ``--confirm-name`` must match AND ``--yes``).
    2. **reinstall** — actually run the pipeline the decision calls for. A
       PRESERVE runs :func:`~installer_backend.reinstall.run_preserve_pipeline`
       with the secrets read back from the existing ``.env``; a FRESH or a first
       install runs the ordinary install pipeline with fresh secrets. Without
       this half the subcommand detected, stopped the stack and exited 0 having
       reinstalled nothing.

    The data root is taken from *config_path* (``storage.data_root``) rather
    than a flag of its own: the tree the reinstall inspects and the tree the
    install writes to are by definition the same one, and two sources for it is
    a divergence waiting to happen.

    ``reinstaller`` / ``installer`` / ``preserve_executor`` are injectable for
    tests. A failed FRESH confirmation, a PRESERVE that cannot reuse the
    existing secrets, or an incomplete wipe map to :data:`ExitCode.ABORTED`.
    """

    stream = out if out is not None else sys.stdout

    # Config gate FIRST, exactly like `install`: a malformed YAML returns
    # CONFIG without having touched the machine.
    config = load_install_config(_read_config_file(config_path))
    data_root = config.storage.data_root

    if reinstaller is None:
        confirmer: Confirmer
        if interactive:
            confirmer = InteractiveConfirmer()
        else:
            confirmer = FlagConfirmer(confirm_name_value=confirm_name, yes=yes)
        reinstaller = build_default_reinstaller(
            stream, confirmer, data_root=data_root, dry_run=dry_run
        )
    _assert_real_reinstall_seams(reinstaller, dry_run=dry_run)
    if dry_run:
        print(_SIMULATION_BANNER, file=stream)

    req = ReinstallRequest(
        preserve=not fresh,
        deployment_name=deployment_name,
        data_root=data_root,
    )
    try:
        result = reinstaller.run(req)
    except ReinstallAbortedError as exc:
        raise CliError(str(exc), ExitCode.ABORTED) from exc

    if result.mode is ReinstallMode.PRESERVE:
        return _run_preserve_reinstall(result, config, stream, preserve_executor, dry_run=dry_run)
    return _run_fresh_reinstall(config, stream, installer, dry_run=dry_run)


def _run_preserve_reinstall(
    result: ReinstallResult,
    config: InstallerConfig,
    stream: TextIO,
    executor: StepExecutor | None,
    *,
    dry_run: bool,
) -> ExitCode:
    """Regenerate over the preserved data, REUSING the deployment's secrets.

    ``dry_run`` picks the simulation executor. It matters even though the stub
    detector never resolves to PRESERVE today: relying on that would be
    depending on a coincidence of the default wiring, and the cost of getting it
    wrong is a ``--dry-run`` that writes a real ``.env`` and runs a real
    ``docker compose pull`` — which is exactly what it did before this argument
    existed.
    """

    if result.existing_secrets is None:  # pragma: no cover - Reinstaller._preserve lo garantiza
        raise CliError(
            "Error interno: una reinstalación con preservación llegó sin los "
            "secretos existentes. No se ha regenerado nada.",
            ExitCode.ABORTED,
        )
    # `monitoring` sigue el valor del instalador (hoy siempre False: no hay flag
    # que lo active en el CLI). Si algún día lo hay, tiene que llegar aquí, o la
    # contraseña de Grafana se regeneraría sin decirlo.
    try:
        secrets, regenerated = secrets_from_env(result.existing_secrets.env_values)
    except MissingExistingSecretError as exc:
        # Nada se ha regenerado todavía: el stack está parado y los datos
        # intactos. Parar aquí es lo que impide dejarlos huérfanos.
        raise CliError(str(exc), ExitCode.ABORTED) from exc

    if regenerated:
        print(
            "[reinstall] AVISO: estos secretos no estaban en el .env anterior y "
            f"se han generado NUEVOS: {', '.join(regenerated)}. No hay datos "
            "atados a ellos, pero rotar el secreto JWT cierra todas las sesiones "
            "abiertas.",
            file=stream,
        )

    if executor is not None:
        exec_ = executor
    elif dry_run:
        exec_ = FakeStepExecutor()
    else:
        exec_ = build_preserve_executor(config, secrets)
    try:
        run_preserve_pipeline(exec_, _config_to_executor_payload(config), stream)
    except StepExecutionError as exc:
        raise CliError(
            f"La reinstalación con preservación falló: {exc}. Los datos NO se han "
            "tocado; el stack puede haber quedado a medio levantar.",
            ExitCode.PROVISION,
        ) from exc
    return ExitCode.OK


def _run_fresh_reinstall(
    config: InstallerConfig,
    stream: TextIO,
    installer: HeadlessInstaller | None,
    *,
    dry_run: bool,
) -> ExitCode:
    """Install from scratch after a FRESH wipe (or on a machine with nothing).

    There is no data underneath in either case — a FRESH just wiped it, a first
    install never had any — so this is the ordinary install pipeline: fresh
    secrets, fresh Vault, and the one-time credential reveal at the end.
    """

    inst = (
        installer
        if installer is not None
        else build_default_installer(stream, config, dry_run=dry_run)
    )
    _assert_real_install_seams(inst, dry_run=dry_run)
    inst.run(config)
    return ExitCode.OK


def _dispatch(args: argparse.Namespace, out: TextIO | None) -> ExitCode:
    """Encamina los args ya parseados al runner del subcomando.

    Separado de :func:`main` para que el punto de entrada se quede sólo con las
    dos responsabilidades que NO son por-comando: normalizar la salida propia de
    argparse y mapear un :class:`CliError` a su código documentado. Todo runner
    devuelve un :class:`ExitCode` o lanza :class:`CliError`.
    """

    if args.command == "install":
        return run_install(
            args.config,
            out=out,
            dry_run=args.dry_run,
            force_new_secrets=args.force_new_secrets,
            unseal_keys_path=args.vault_unseal_keys_from,
        )
    if args.command == "generate":
        return run_generate(args.config, out=out, force_new_secrets=args.force_new_secrets)
    if args.command == "uninstall":
        return run_uninstall(
            deployment_name=args.deployment_name,
            data_root=args.data_root,
            purge_data=args.purge_data,
            confirm_name=args.confirm_name,
            yes=args.yes,
            interactive=args.interactive,
            out=out,
            dry_run=args.dry_run,
        )
    if args.command == "reinstall":
        return run_reinstall(
            args.config,
            deployment_name=args.deployment_name,
            fresh=args.fresh,
            confirm_name=args.confirm_name,
            yes=args.yes,
            interactive=args.interactive,
            out=out,
            dry_run=args.dry_run,
        )
    # Inalcanzable (el subparser es `required=True`); se deja por exhaustividad.
    raise CliError("comando desconocido.", ExitCode.USAGE)  # pragma: no cover


def main(argv: Sequence[str] | None = None, *, out: TextIO | None = None) -> int:
    """CLI entry point. Returns a process exit code (see :class:`ExitCode`).

    ``scripts/install.sh`` execs ``python -m installer_backend.cli install
    --config install.yaml`` and propagates this return value as ``$?``. All
    failures are caught and mapped to their documented exit code with a clear
    stderr line; nothing here logs a secret.

    **Nada sale de aquí como traza de Python.** El ``except Exception`` final no
    es defensivo por gusto: este proceso escribe en el sistema de ficheros de una
    máquina que no controla, y la lista de cosas que pueden fallar ahí no se
    puede enumerar por adelantado. Traducir EACCES y ENOSPC uno a uno (lo hace
    ``real_step_executor``) arregla los modos de fallo conocidos; esto es lo que
    impide que el siguiente imprevisto vuelva a salir como veinte líneas de
    traceback terminadas con un exit 1 que la tabla llama «argumentos mal».
    """

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 on bad args; normalise to our USAGE code.
        return int(ExitCode.USAGE) if exc.code not in (0, None) else int(ExitCode.OK)

    try:
        return int(_dispatch(args, out))
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return int(exc.code)
    except KeyboardInterrupt:
        # Un Ctrl-C NO es un error inesperado: es el operador. Se distingue para
        # que su automatización no lo confunda con una avería.
        print("error: interrumpido por el operador.", file=sys.stderr)
        return int(ExitCode.ABORTED)
    except Exception as exc:
        print(
            f"error inesperado: {type(exc).__name__}: {exc}\n"
            "Esto es un fallo que el instalador no supo clasificar. La raíz de "
            "datos puede tener escrituras parciales; revísala antes de volver a "
            "ejecutar.",
            file=sys.stderr,
        )
        return int(ExitCode.UNEXPECTED)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
