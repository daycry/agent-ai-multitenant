"""Reinstall over an existing deployment (Plan 15 task_15_13).

A reinstall re-runs the installer over a machine that may ALREADY hold a
deployment (a ``/data/agent-platform`` data tree and/or a running compose
stack). Unlike a first install, it must FIRST decide what to do with the data
that is already there. This module owns that decision + the orchestration; it
performs NO real I/O — detecting the existing install, tearing down the stack,
wiping data and reusing the existing Vault material all go through injectable
seams (mocked in tests, real bindings exercised only by the plan's Tests
Humanos).

The two modes
-------------
``PRESERVE`` (the safe default)
    Keep the existing data volumes + database + object store. The stack is
    stopped and re-created with freshly-generated **config/compose** (so an
    upgraded installer rewrites the deployment topology), but the **data is
    never wiped** and — critically — the **existing secrets and Vault unseal
    material are REUSED**, never regenerated. Regenerating DB/MinIO/JWT secrets
    or re-initialising Vault would *orphan the existing encrypted data*: the
    Postgres role passwords, the MinIO access keys and the Vault-encrypted
    secret tree are all bound to the original material, so a preserve that
    minted new secrets would leave the kept data unreadable. PRESERVE therefore
    loads the existing ``.env`` secrets + the existing Vault keys and feeds them
    back into the regenerated config. This is the constraint :func:`reinstall`
    enforces and documents.

``FRESH`` (destructive — double-confirmed)
    Wipe the existing data tree and start over as if this were a first install:
    regenerate every secret, re-initialise Vault from scratch. Because this
    DESTROYS tenant data it is gated by the SAME double confirmation the
    uninstall uses (type the deployment name + an explicit yes) before anything
    is wiped. Without both confirmations the wipe is refused and the reinstall
    aborts with nothing removed.

No prior install
----------------
When the detector finds NO existing deployment (no data tree, no running
stack), there is nothing to preserve or wipe: the reinstall degrades to a plain
first install — fresh secrets, fresh Vault, no confirmation needed (there is no
data to destroy). This is the :attr:`ReinstallMode.FIRST_INSTALL` outcome.

Security
--------
The existing secrets reused in PRESERVE mode are loaded behind a seam and held
only in memory; nothing here logs a secret or an unseal key. FRESH mode mints
CSPRNG secrets via :mod:`installer_backend.config_generators` (never the
dev-default markers). The data wipe is the only destructive action and it is
double-confirmed, exactly like the uninstall.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, TextIO, runtime_checkable

from installer_backend.compose_generator import PROJECT_NAME
from installer_backend.uninstall import (
    Confirmer,
    DataPurger,
    StackTeardown,
    StubDataPurger,
    StubStackTeardown,
)


class ReinstallAbortedError(Exception):
    """Raised when a FRESH reinstall's destructive confirmation was not given.

    Carries an operator-facing message (never a secret). When raised NOTHING
    destructive has run: the existing stack + data are intact. The CLI maps it
    to the ``ABORTED`` exit code.
    """


class ReinstallMode(StrEnum):
    """How a reinstall treats the data already on the machine.

    * ``PRESERVE``      — keep data + reuse existing secrets/Vault (the default).
    * ``FRESH``         — wipe data + regenerate everything (double-confirmed).
    * ``FIRST_INSTALL`` — no prior deployment was detected; behave like a first
                          install (fresh secrets, no confirmation needed because
                          there is no data to destroy). This is an *outcome*, not
                          a flag the operator sets — it is derived from detection.

    ``str`` so it serialises as its value over the API / in logs.
    """

    PRESERVE = "preserve"
    FRESH = "fresh"
    FIRST_INSTALL = "first_install"


# ---------------------------------------------------------------------------
# Injectable seams — everything that inspects or reuses host state.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ExistingInstall:
    """What the detector found about a pre-existing deployment.

    ``data_dir_present`` is True iff the data root exists on disk; ``stack_running``
    is True iff a compose stack with the project name is up. ``present`` is the
    derived "there IS something here" signal — True if either is set, which is
    what gates the preserve/fresh decision. When neither is set the reinstall is
    a first install.
    """

    data_dir_present: bool
    stack_running: bool

    @property
    def present(self) -> bool:
        """True iff ANY prior deployment artifact was found (data or stack)."""

        return self.data_dir_present or self.stack_running


@runtime_checkable
class InstallDetector(Protocol):
    """Detects whether a prior deployment exists on this machine.

    The real binding ``os.path.isdir``s the data root and shells out to
    ``docker compose -p <project> ps`` to see if the stack is up. The fake
    returns a scripted :class:`ExistingInstall` so the detection branch is
    exercised with no Docker host and no filesystem.
    """

    def detect(self, *, data_root: str, project_name: str) -> ExistingInstall:
        """Probe *data_root* + the *project_name* stack; return what was found."""
        ...


@dataclass(frozen=True)
class ExistingSecrets:
    """The existing secret material a PRESERVE reinstall must REUSE.

    Loaded from the deployment's on-disk ``.env`` + the operator's stored Vault
    unseal keys. Reusing these is the whole point of PRESERVE: the kept
    Postgres / MinIO data and the Vault-encrypted secret tree are bound to this
    material, so regenerating it would orphan the data. ``__repr__`` is redacted
    so a stray log line cannot leak the values.

    ``env_values`` is the parsed ``KEY=value`` map of the existing ``.env``
    (carries the DB/MinIO/JWT secrets). ``vault_unseal_keys`` are the operator's
    stored unseal-key shares — fed back to the Vault bootstrap so it UNSEALS the
    already-initialised vault instead of re-initialising it (no-recovery: a
    re-init would discard the keys that decrypt the data).
    """

    env_values: dict[str, str]
    vault_unseal_keys: tuple[str, ...]

    def __repr__(self) -> str:  # pragma: no cover - security-load-bearing, trivial
        return "ExistingSecrets(<redacted: reused on preserve, never logged>)"

    __str__ = __repr__


@runtime_checkable
class ExistingSecretLoader(Protocol):
    """Loads the existing secrets a PRESERVE reinstall reuses.

    The real binding reads the deployment's ``.env`` and the operator-supplied
    Vault unseal keys (kept out of the repo). The fake returns scripted values
    so the reuse contract is asserted with no disk access. Returns ``None`` when
    the existing material cannot be found (which makes a PRESERVE impossible —
    :func:`reinstall` raises rather than silently regenerating + orphaning data).
    """

    def load(self, *, data_root: str) -> ExistingSecrets | None:
        """Load the existing secrets under *data_root*; ``None`` if unavailable."""
        ...


# ---------------------------------------------------------------------------
# In-memory fakes — the DEFAULT seams. Import-safe, no host access, testable.
# ---------------------------------------------------------------------------
@dataclass
class StubInstallDetector:
    """A scripted :class:`InstallDetector` (test default).

    Defaults to "no prior install" (a first install). Preset ``data_dir_present``
    / ``stack_running`` to model an existing deployment without touching disk or
    Docker. Records the probe so a test can assert it targeted the right paths.
    """

    data_dir_present: bool = False
    stack_running: bool = False
    #: Records the (data_root, project_name) probed (for assertions).
    probed: tuple[str, str] | None = None

    def detect(self, *, data_root: str, project_name: str) -> ExistingInstall:
        self.probed = (data_root, project_name)
        return ExistingInstall(
            data_dir_present=self.data_dir_present,
            stack_running=self.stack_running,
        )


@dataclass
class StubExistingSecretLoader:
    """A scripted :class:`ExistingSecretLoader` (test default).

    Returns a scripted :class:`ExistingSecrets` (obviously-fake placeholder
    values) so a PRESERVE test can assert the existing material was reused. Set
    ``available`` to ``False`` to model the "can't find the old secrets" case
    that makes PRESERVE impossible.
    """

    available: bool = True
    env_values: dict[str, str] = field(
        default_factory=lambda: {
            "POSTGRES_PASSWORD": "existing-pg-password",  # - placeholder, not real
            "MINIO_ROOT_USER": "minio-existing",  # - placeholder, not real
            "MINIO_ROOT_PASSWORD": "existing-minio-password",  # - placeholder
            "API_SERVER_JWT_SECRET": "existing-jwt-secret",  # - placeholder
        }
    )
    vault_unseal_keys: tuple[str, ...] = (
        "existing-unseal-1",  # - placeholder, not real
        "existing-unseal-2",  # - placeholder, not real
        "existing-unseal-3",  # - placeholder, not real
    )
    #: True once :meth:`load` has been called (the existing material was read).
    loaded: bool = False

    def load(self, *, data_root: str) -> ExistingSecrets | None:  # noqa: ARG002
        self.loaded = True
        if not self.available:
            return None
        return ExistingSecrets(
            env_values=dict(self.env_values),
            vault_unseal_keys=self.vault_unseal_keys,
        )


# ---------------------------------------------------------------------------
# The reinstall request + result.
# ---------------------------------------------------------------------------
@dataclass
class ReinstallRequest:
    """The parameters of one reinstall (what the CLI flags resolve to).

    ``preserve`` is the operator's intent: True keeps the data (the safe
    default), False asks for a FRESH wipe-and-reinstall. ``deployment_name`` is
    the compose project to detect/tear down; it must be typed back to confirm a
    FRESH wipe (the same gate as the uninstall). ``data_root`` is the data tree
    that detection probes and a FRESH reinstall would wipe.

    When NO prior install is detected, ``preserve`` is moot — the run is a first
    install regardless.
    """

    preserve: bool = True
    deployment_name: str = PROJECT_NAME
    data_root: str = "/data/agent-platform"


@dataclass
class ReinstallResult:
    """Outcome of a completed reinstall (returned on success).

    ``mode`` is what actually happened (preserve / fresh / first install).
    ``data_preserved`` is the headline guarantee — True unless a FRESH wipe ran.
    ``reused_existing_secrets`` is True iff the existing secrets + Vault material
    were reused (only in PRESERVE), which is what prevents orphaning the kept
    encrypted data. ``existing_secrets`` carries the reused material (PRESERVE
    only; ``None`` otherwise) so the install pipeline can feed it back into the
    regenerated config + the Vault bootstrap. ``log`` is the secret-free log.
    """

    mode: ReinstallMode
    data_preserved: bool
    reused_existing_secrets: bool
    existing_secrets: ExistingSecrets | None = None
    log: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# The reinstall orchestration.
# ---------------------------------------------------------------------------
@dataclass
class Reinstaller:
    """Decides preserve / fresh / first-install and prepares the reinstall.

    Construct with the injectable seams (defaults are the in-memory stubs so the
    orchestration is testable with no host): the :class:`InstallDetector`, the
    :class:`ExistingSecretLoader` (PRESERVE reuse), the :class:`StackTeardown`
    + :class:`DataPurger` (FRESH wipe), the :class:`Confirmer` that gates the
    FRESH wipe, and the output stream.

    :meth:`run` resolves the mode and performs the PRE-install work — it does NOT
    run the install pipeline itself (that is :class:`~installer_backend.install.
    InstallOrchestrator`, driven afterward by the wizard / CLI with the
    :class:`ReinstallResult` plumbed in). Concretely:

      * **no prior install** — returns :attr:`ReinstallMode.FIRST_INSTALL`; the
        caller installs fresh.
      * **preserve** — loads + returns the existing secrets/Vault material to
        reuse (no wipe, no confirmation). The stack is stopped (data preserved)
        so the regenerated config can be applied. Raises if the existing material
        is unavailable (regenerating would orphan the data — refused, not risked).
      * **fresh** — enforces the DOUBLE confirmation (type the name + explicit
        yes); only then tears down the stack + wipes the data tree and returns
        :attr:`ReinstallMode.FRESH`. A failed confirmation ABORTS with nothing
        removed.
    """

    detector: InstallDetector
    secret_loader: ExistingSecretLoader
    teardown: StackTeardown
    purger: DataPurger
    confirmer: Confirmer
    out: TextIO
    #: The ordered phase names actually executed (for assertions).
    phases: list[str] = field(default_factory=list)

    def _log(self, message: str) -> None:
        """Emit one operator-facing log line. NEVER carries a secret."""

        print(message, file=self.out)

    def _confirm_fresh_wipe(self, req: ReinstallRequest) -> None:
        """Enforce the double confirmation before a FRESH wipe; abort if it fails.

        Identical contract to the uninstall: the operator must type the exact
        deployment name AND give an explicit yes. Either missing/wrong ABORTS
        (raises :class:`ReinstallAbortedError`) with NOTHING removed.
        """

        self.phases.append("confirm_fresh")
        typed = self.confirmer.confirm_name(
            f"Reinstalación LIMPIA: escribe el nombre del despliegue para confirmar "
            f"el BORRADO de todos los datos ({req.deployment_name}): "
        ).strip()
        if typed != req.deployment_name:
            raise ReinstallAbortedError(
                "Reinstalación limpia abortada: el nombre del despliegue no coincide "
                "(no se ha eliminado nada; los datos existentes están intactos)."
            )
        if not self.confirmer.confirm_yes(
            f"Esto BORRARÁ DE FORMA IRREVERSIBLE todos los datos bajo {req.data_root} "
            f"y reinstalará el stack '{req.deployment_name}' desde cero. "
            "Esta acción NO se puede deshacer. ¿Continuar? [y/N]: "
        ):
            raise ReinstallAbortedError(
                "Reinstalación limpia abortada: no se confirmó explícitamente "
                "(no se ha eliminado nada; los datos existentes están intactos)."
            )

    def _preserve(self, req: ReinstallRequest) -> ReinstallResult:
        """Prepare a PRESERVE reinstall: reuse existing secrets, keep the data.

        Loads the existing ``.env`` secrets + Vault unseal keys (reused so the
        regenerated config does not orphan the kept encrypted data). Stops the
        stack WITHOUT removing volumes and WITHOUT wiping the data root, so the
        regenerated compose can be applied over the intact data. Raises
        :class:`ReinstallAbortedError` if the existing material cannot be loaded
        (a preserve that minted new secrets would orphan the data — refused).
        """

        self.phases.append("preserve")
        self._log("[reinstall] Modo PRESERVAR: se conservan datos y se reutilizan los secretos.")

        existing = self.secret_loader.load(data_root=req.data_root)
        if existing is None:
            raise ReinstallAbortedError(
                "No se pudieron cargar los secretos / unseal keys existentes; "
                "preservar regenerando secretos dejaría HUÉRFANOS los datos cifrados. "
                "Aporta los secretos existentes o usa una reinstalación limpia (--fresh)."
            )
        self._log(
            "[reinstall] Secretos y unseal keys existentes cargados; se reutilizarán "
            "(no se regeneran para no huérfanar los datos)."
        )

        # Stop the running stack so the regenerated compose can be applied — but
        # NEVER remove volumes and NEVER wipe the data root (that is the whole
        # point of preserve). The purger is not called in this path.
        self.phases.append("teardown")
        self._log(f"[reinstall] Deteniendo el stack '{req.deployment_name}' (datos intactos)…")
        for line in self.teardown.down(req.deployment_name, remove_volumes=False):
            self._log(f"[reinstall]   {line}")

        self._log("[reinstall] Listo para regenerar configuración sobre los datos conservados.")
        return ReinstallResult(
            mode=ReinstallMode.PRESERVE,
            data_preserved=True,
            reused_existing_secrets=True,
            existing_secrets=existing,
        )

    def _fresh(self, req: ReinstallRequest) -> ReinstallResult:
        """Prepare a FRESH reinstall: double-confirm, then tear down + wipe.

        Enforces the double confirmation FIRST (nothing destructive until it
        passes), then tears down the stack and wipes the data root so the caller
        can install from scratch with freshly-generated secrets + a fresh Vault.
        """

        self._confirm_fresh_wipe(req)

        self.phases.append("teardown")
        self._log(f"[reinstall] Deteniendo y eliminando el stack '{req.deployment_name}'…")
        for line in self.teardown.down(req.deployment_name, remove_volumes=True):
            self._log(f"[reinstall]   {line}")

        self.phases.append("wipe_data")
        self._log(f"[reinstall] Borrando todos los datos bajo {req.data_root}…")
        for line in self.purger.purge(req.data_root):
            self._log(f"[reinstall]   {line}")

        self._log("[reinstall] Datos eliminados; se reinstalará desde cero con secretos nuevos.")
        return ReinstallResult(
            mode=ReinstallMode.FRESH,
            data_preserved=False,
            reused_existing_secrets=False,
            existing_secrets=None,
        )

    def _first_install(self) -> ReinstallResult:
        """No prior deployment: behave like a plain first install (fresh secrets)."""

        self.phases.append("first_install")
        self._log("[reinstall] No se detectó instalación previa: instalación desde cero.")
        return ReinstallResult(
            mode=ReinstallMode.FIRST_INSTALL,
            data_preserved=True,  # there was no data to preserve OR destroy
            reused_existing_secrets=False,
            existing_secrets=None,
        )

    def run(self, req: ReinstallRequest) -> ReinstallResult:
        """Resolve the mode + do the pre-install work for *req*.

        Detects an existing deployment, then:

          * none found        → :attr:`ReinstallMode.FIRST_INSTALL` (install fresh);
          * found + preserve  → reuse existing secrets/Vault, keep data (no wipe);
          * found + fresh     → DOUBLE-confirm, then tear down + wipe the data.

        Raises :class:`ReinstallAbortedError` (with nothing removed) if a FRESH
        wipe's confirmation fails, or if a PRESERVE cannot load the existing
        secrets (regenerating would orphan the data).
        """

        self.phases.append("detect")
        self._log("[reinstall] Detectando instalación previa…")
        existing = self.detector.detect(data_root=req.data_root, project_name=req.deployment_name)

        if not existing.present:
            return self._first_install()

        found = []
        if existing.data_dir_present:
            found.append(f"datos en {req.data_root}")
        if existing.stack_running:
            found.append(f"stack '{req.deployment_name}' en ejecución")
        self._log(f"[reinstall] Instalación previa detectada: {', '.join(found)}.")

        if req.preserve:
            return self._preserve(req)
        return self._fresh(req)


def build_default_reinstaller(out: TextIO, confirmer: Confirmer) -> Reinstaller:
    """Build a :class:`Reinstaller` wired to the in-memory stub seams.

    The host-touching seams default to the recording stubs (import-safe, no
    Docker / no disk access): the detector reports "no prior install", the
    secret loader returns scripted placeholders, the teardown/purger record
    instead of acting. The caller supplies the :class:`Confirmer` (the CLI passes
    a flag-derived one for the FRESH gate; tests pass a scripted one). The real
    install replaces the stub seams with the host bindings (``docker compose
    ps`` detection, ``.env`` read, ``docker compose down`` + ``shutil.rmtree``),
    exercised only by the plan's Tests Humanos.
    """

    return Reinstaller(
        detector=StubInstallDetector(),
        secret_loader=StubExistingSecretLoader(),
        teardown=StubStackTeardown(),
        purger=StubDataPurger(),
        confirmer=confirmer,
        out=out,
    )
