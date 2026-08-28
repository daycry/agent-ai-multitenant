"""Reinstall over an existing deployment (Plan 15 task_15_13).

A reinstall re-runs the installer over a machine that may ALREADY hold a
deployment (a ``/data/agent-platform`` data tree and/or a running compose
stack). Unlike a first install, it must FIRST decide what to do with the data
that is already there. This module owns that decision AND the pipeline that
runs after it. Every host-touching action goes through an injectable seam
(``RealInstallDetector`` / ``RealExistingSecretLoader`` here, the uninstall's
``RealStackTeardown`` / ``RealDataPurger`` for the destructive half), so the
orchestration is unit-tested with no Docker host and no disk.

What happens after :meth:`Reinstaller.run`
------------------------------------------
:meth:`Reinstaller.run` only does the PRE-install work. The install itself is
chained by the caller (``installer_backend.cli.run_reinstall``), and WHICH
pipeline it chains depends on the resolved mode — that is the point of the
mode:

* ``FRESH`` / ``FIRST_INSTALL`` → the ordinary first-install pipeline, all six
  steps plus the one-time credential reveal. There is no data underneath (a
  FRESH wiped it, a FIRST_INSTALL never had any), so fresh secrets and a fresh
  Vault are exactly right.
* ``PRESERVE`` → :data:`PRESERVE_STEP_ORDER`, four of the six steps, run with
  the REUSED secrets. See :func:`run_preserve_pipeline` for why the other two
  are left out; skipping them is not a shortcut, it is the correct behaviour
  over a deployment that already exists.

The two modes
-------------
``PRESERVE`` (the safe default)
    Keep the existing data volumes + database + object store. The stack is
    stopped and re-created with freshly-generated **config/compose** (so an
    upgraded installer rewrites the deployment topology), but the **data is
    never wiped** and — critically — the **existing secrets are REUSED**, never
    regenerated. Regenerating the DB/MinIO passwords or the Fernet keys would
    *orphan the existing data*: the Postgres role passwords and the MinIO access
    keys were set once at first start and the encrypted columns only open with
    the original Fernet material, so a preserve that minted new secrets would
    leave the kept data unreadable. PRESERVE therefore reads the deployment's
    existing ``.env`` and feeds those exact values back into the regenerated
    config (:func:`secrets_from_env`). This is the constraint the module
    enforces: if the existing material cannot be read, PRESERVE REFUSES rather
    than regenerating.

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

Vault, and why the reinstall never unseals it
---------------------------------------------
A PRESERVE finds Vault ALREADY initialised, and after the teardown it is
**sealed** (Vault seals whenever its container stops). The reinstall does not
try to unseal it, and does not accept the unseal keys on a flag or in a file:
`ADR 0145 <../../../docs/05-architecture-decisions/0145-vault-operable-tokens-y-unseal.md>`_
decided **manual unsealing**, with the Shamir shares held by people. An
installer that read those shares off the same box to automate the unseal would
dismantle that decision while appearing to be a convenience. So the preserve
pipeline SKIPS the Vault bootstrap and ends by telling the operator to unseal by
hand — the same instruction the upgrade runbook already gives after any restart.

Security
--------
The existing secrets reused in PRESERVE mode are loaded behind a seam and held
only in memory; nothing here logs a secret — :class:`ExistingSecrets` has a
redacted ``repr`` and the reuse log names VARIABLES, never values. FRESH mode
mints CSPRNG secrets via :mod:`installer_backend.config_generators` (never the
dev-default markers). The data wipe is the only destructive action and it is
double-confirmed, exactly like the uninstall.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol, TextIO, runtime_checkable

from installer_backend.command_runner import CommandRunner
from installer_backend.compose_generator import PROJECT_NAME
from installer_backend.config import InstallerConfig
from installer_backend.config_generators import GeneratedSecrets, generate_secrets
from installer_backend.install import INSTALL_STEP_ORDER, InstallStep, StepExecutor
from installer_backend.real_teardown import FileSystem, RealFileSystem
from installer_backend.uninstall import (
    Confirmer,
    DataPurger,
    StackTeardown,
    StubDataPurger,
    StubStackTeardown,
)

#: The generated env file, directly under the data root. A PRESERVE reads its
#: existing copy to recover the secrets it must NOT regenerate.
ENV_BASENAME = ".env"


class ReinstallAbortedError(Exception):
    """Raised when a FRESH reinstall's destructive confirmation was not given.

    Carries an operator-facing message (never a secret). When raised NOTHING
    destructive has run: the existing stack + data are intact. The CLI maps it
    to the ``ABORTED`` exit code.
    """


class ReinstallMode(StrEnum):
    """How a reinstall treats the data already on the machine.

    * ``PRESERVE``      — keep data + reuse the existing secrets (the default).
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

    Loaded from the deployment's on-disk ``.env``. Reusing these is the whole
    point of PRESERVE: the kept Postgres / MinIO data and the Fernet-encrypted
    columns are bound to this material, so regenerating it would orphan the
    data. ``__repr__`` is redacted so a stray log line or traceback frame cannot
    leak the values.

    ``env_values`` is the parsed ``KEY=value`` map of the existing ``.env``.

    There is deliberately NO ``vault_unseal_keys`` field. It existed while the
    plan assumed the reinstall would re-bootstrap Vault, and it was never
    populated by anything real. It is gone because the preserve pipeline does
    not touch Vault at all: ADR 0145 keeps unsealing MANUAL, so the shares live
    with people and not in a file this process could read (see the module
    docstring). A field that production always leaves empty is not a placeholder
    — it is a promise the code does not keep.
    """

    env_values: dict[str, str]

    def __repr__(self) -> str:  # pragma: no cover - security-load-bearing, trivial
        return "ExistingSecrets(<redacted: reused on preserve, never logged>)"

    __str__ = __repr__


@runtime_checkable
class ExistingSecretLoader(Protocol):
    """Loads the existing secrets a PRESERVE reinstall reuses.

    The real binding (:class:`RealExistingSecretLoader`) reads the deployment's
    ``.env`` from under the data root. The fake returns scripted values so the
    reuse contract is asserted with no disk access. Returns ``None`` when the
    existing material cannot be found, which makes a PRESERVE impossible:
    :meth:`Reinstaller._preserve` raises rather than silently regenerating and
    orphaning the data it promised to keep.
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
    #: True once :meth:`load` has been called (the existing material was read).
    loaded: bool = False

    def load(self, *, data_root: str) -> ExistingSecrets | None:  # noqa: ARG002
        self.loaded = True
        if not self.available:
            return None
        return ExistingSecrets(env_values=dict(self.env_values))


# ---------------------------------------------------------------------------
# Reusing the existing secrets — the half of PRESERVE that must not be wrong.
# ---------------------------------------------------------------------------
class MissingExistingSecretError(Exception):
    """The existing ``.env`` lacks a secret whose loss would ORPHAN the data.

    Raised by :func:`secrets_from_env`. Carries the missing VARIABLE names
    (never values) so the operator can see what to recover. A PRESERVE that hit
    this must abort: minting a replacement is precisely the outcome PRESERVE
    exists to prevent.
    """


#: Env vars whose value is bound to data ALREADY on disk, mapped to the
#: :class:`GeneratedSecrets` field that carries them. Losing one of these does
#: not "rotate" anything — it orphans data:
#:
#:   * the three Postgres role passwords were set by ``initdb`` / the role
#:     creation on first start and are not re-applied by a later ``up``;
#:   * the MinIO root user AND password were baked into the object store the
#:     same way;
#:   * the three Fernet keys are the ONLY way to read the encrypted columns
#:     (SSO client secrets, notification channel credentials, incoming webhook
#:     secrets — the documented exception to Vault in CLAUDE.md). A new key
#:     leaves that ciphertext unreadable forever.
#:
#: So a PRESERVE missing any of them REFUSES.
_DATA_BOUND_SECRETS: dict[str, str] = {
    "POSTGRES_PASSWORD": "postgres_password",
    "MIGRATIONS_USER_PASSWORD": "migrations_user_password",
    "APP_USER_PASSWORD": "app_user_password",
    "SERVICE_USER_PASSWORD": "service_user_password",
    "MINIO_ROOT_USER": "minio_root_user",
    "MINIO_ROOT_PASSWORD": "minio_root_password",
    "API_SERVER_SSO_ENCRYPTION_KEY": "sso_encryption_key",
    "API_SERVER_NOTIFICATION_ENCRYPTION_KEY": "notification_encryption_key",
    "API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY": "incoming_webhook_encryption_key",
}

#: Env vars that are reused when present but CAN be minted again: they sign or
#: authenticate, they do not decrypt anything at rest. Rotating one is
#: disruptive (every session drops when the JWT secret changes) but never
#: destructive, so a ``.env`` written before one of them existed — the real
#: case: ``API_SERVER_INTERNAL_TOKEN_SECRET`` arrived with ADR 0136 — must not
#: block a reinstall. It IS reported: see :func:`secrets_from_env`.
_ROTATABLE_SECRETS: dict[str, str] = {
    "API_SERVER_JWT_SECRET": "jwt_secret",
    "API_SERVER_INTERNAL_TOKEN_SECRET": "internal_token_secret",
    "API_SERVER_ALERTS_INGEST_TOKEN": "alerts_ingest_token",
    "API_SERVER_REVIEW_URL_SIGNING_SECRET": "review_url_signing_secret",
    # Redis lo lee de `--requirepass` al arrancar, no lo lleva grabado en el RDB
    # ni en el AOF: cambiarlo no deja nada ilegible, sólo exige que el stack
    # entero arranque con el mismo .env — que es justo lo que hace un reinstall.
    "REDIS_PASSWORD": "redis_password",
}

#: Rotatable too, but only written to the ``.env`` when the monitoring overlay
#: is on. Kept apart so a base install's ``.env`` — which legitimately has no
#: Grafana — does not get reported as "I regenerated your Grafana password"
#: when there is no Grafana. A false alarm costs the same trust as a silence.
_MONITORING_SECRETS: dict[str, str] = {
    "GRAFANA_ADMIN_PASSWORD": "grafana_admin_password",
}

#: Fields of :class:`GeneratedSecrets` that are NEVER written to the ``.env``
#: and therefore cannot be recovered from one. Listed explicitly so the
#: completeness guard in the tests can tell "deliberately not recoverable" from
#: "somebody added a secret and forgot to classify it" — the second being a
#: silent way to orphan data on the next preserve.
SECRETS_NOT_IN_THE_ENV: frozenset[str] = frozenset({"vault_root_token_placeholder"})


def parse_env_text(text: str) -> dict[str, str]:
    """Parse generated ``.env`` text back into its ``KEY=value`` map.

    The exact inverse of :func:`~installer_backend.config_generators.render_env_file`
    plus its ``_quote_env_value``: comments and blank lines are dropped, only the
    FIRST ``=`` splits (so a value containing ``=`` survives), and a
    double-quoted value is unquoted with its escapes undone. Deliberately not a
    general dotenv parser — it reads back a file this installer wrote.
    """

    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            value = value[1:-1].replace('\\"', '"').replace("\\\\", "\\")
        values[key] = value
    return values


def secrets_from_env(
    env_values: dict[str, str], *, monitoring: bool = False
) -> tuple[GeneratedSecrets, tuple[str, ...]]:
    """Rebuild the install secrets from an existing ``.env``; report what was minted.

    Returns ``(secrets, regenerated)`` where *regenerated* names the ROTATABLE
    variables that were not in the old file and had to be minted fresh — names
    only, never values, and surfaced to the operator because a rotated JWT
    secret drops every session.

    Raises :class:`MissingExistingSecretError` if any :data:`_DATA_BOUND_SECRETS`
    entry is absent: that is the case where continuing would orphan data, and
    the module's contract is to refuse rather than risk it.

    ``vault_root_token_placeholder`` is always minted: the env generator never
    writes it to the ``.env`` (its own docstring calls it a throwaway the Vault
    bootstrap overwrites), so there is nothing to recover and nothing bound to
    it.
    """

    missing = sorted(key for key in _DATA_BOUND_SECRETS if not env_values.get(key))
    if missing:
        raise MissingExistingSecretError(
            "El .env existente no tiene "
            f"{', '.join(missing)}. Sin esos valores no se puede PRESERVAR: los "
            "datos de PostgreSQL/MinIO y las columnas cifradas con Fernet están "
            "atados a ellos y regenerarlos los dejaría ilegibles para siempre. "
            "Recupera el .env de la instalación (o su copia de seguridad), o haz "
            "una reinstalación limpia con --fresh asumiendo la pérdida de datos."
        )

    # Start from a fresh draw so any field not covered by the maps below still
    # gets a high-entropy value instead of an empty string.
    reused = generate_secrets()
    overrides = {field_name: env_values[key] for key, field_name in _DATA_BOUND_SECRETS.items()}
    regenerated: list[str] = []
    rotatable = dict(_ROTATABLE_SECRETS)
    if monitoring:
        rotatable.update(_MONITORING_SECRETS)
    for key, field_name in rotatable.items():
        value = env_values.get(key)
        if value:
            overrides[field_name] = value
        else:
            regenerated.append(key)
    return replace(reused, **overrides), tuple(sorted(regenerated))


# ---------------------------------------------------------------------------
# Real host bindings — the seams `build_default_reinstaller` wires by default.
# ---------------------------------------------------------------------------
@runtime_checkable
class EnvFileReader(Protocol):
    """Reads back a file the installer previously wrote (the existing ``.env``).

    A reader seam, not a writer: it lives here rather than in ``real_bindings``
    because reading an installation back is something only the reinstall does.
    """

    def exists(self, path: str) -> bool: ...

    def read_text(self, path: str) -> str: ...


class RealEnvFileReader:
    """Real file reader (host-only; exercised by the e2e / human tests)."""

    def exists(self, path: str) -> bool:  # pragma: no cover - host-only
        from pathlib import Path

        return Path(path).is_file()

    def read_text(self, path: str) -> str:  # pragma: no cover - host-only
        from pathlib import Path

        return Path(path).read_text(encoding="utf-8")


@dataclass
class FakeEnvFileReader:
    """Test reader: ``files`` maps path → contents. No disk access."""

    files: dict[str, str] = field(default_factory=dict)

    def exists(self, path: str) -> bool:
        return path in self.files

    def read_text(self, path: str) -> str:
        return self.files[path]


@dataclass
class RealInstallDetector:
    """Detects a prior deployment: the data root on disk + the compose project.

    Two independent probes, because either one alone misses a real case: a
    half-installed machine has the data tree but nothing running, and a stack
    started from a data root that was since moved has containers but no tree.

    ``docker compose -p <project> ps -q`` addresses the project by its labels,
    so it needs no ``-f`` and works even when the generated compose file is
    gone — the same property :class:`~installer_backend.real_teardown.RealStackTeardown`
    relies on for its fallback.
    """

    runner: CommandRunner
    fs: FileSystem = field(default_factory=RealFileSystem)

    def detect(self, *, data_root: str, project_name: str) -> ExistingInstall:
        data_dir_present = self.fs.exists(data_root)
        result = self.runner.run(["docker", "compose", "-p", project_name, "ps", "-q"])
        if result.returncode != 0:
            # NEVER conclude "no prior install" from a probe that failed. The
            # decision hanging off this answer is preserve / wipe / install
            # fresh, and a wrong "nothing here" means installing from scratch —
            # with NEW secrets — on top of an existing PGDATA, which is the
            # exact catastrophe this module exists to avoid. So we stop and say
            # what did not work.
            detail = "; ".join(result.output_lines[-3:]) or f"rc={result.returncode}"
            raise ReinstallAbortedError(
                "No se pudo comprobar si el stack está en ejecución: "
                f"`docker compose -p {project_name} ps` falló ({detail}). Una "
                "reinstalación no puede suponer el estado de la máquina: "
                "arregla el acceso a Docker y vuelve a ejecutarla. No se ha "
                "tocado nada."
            )
        stack_running = any(line.strip() for line in result.output_lines)
        return ExistingInstall(data_dir_present=data_dir_present, stack_running=stack_running)


@dataclass
class RealExistingSecretLoader:
    """Loads the existing secrets from the deployment's ``.env`` under the data root.

    Returns ``None`` when there is no ``.env`` to read — which a PRESERVE turns
    into a refusal, never into a fresh mint.
    """

    reader: EnvFileReader = field(default_factory=RealEnvFileReader)

    def load(self, *, data_root: str) -> ExistingSecrets | None:
        path = f"{data_root}/{ENV_BASENAME}"
        if not self.reader.exists(path):
            return None
        return ExistingSecrets(env_values=parse_env_text(self.reader.read_text(path)))


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
    ``reused_existing_secrets`` is True iff the existing secrets were reused
    (only in PRESERVE), which is what prevents orphaning the kept data.
    ``existing_secrets`` carries the reused material (PRESERVE only; ``None``
    otherwise) so the caller can feed it back into the regenerated config —
    :func:`secrets_from_env` turns it into the install's ``GeneratedSecrets``.
    ``log`` is the secret-free log.
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

    :meth:`run` resolves the mode and performs the PRE-install work; the install
    itself is chained afterward by ``installer_backend.cli.run_reinstall`` from
    the returned :class:`ReinstallResult` (see the module docstring — for a long
    time nothing chained anything, and the subcommand exited 0 having only
    stopped the stack). Concretely:

      * **no prior install** — returns :attr:`ReinstallMode.FIRST_INSTALL`; the
        caller installs fresh.
      * **preserve** — loads + returns the existing secrets to reuse (no wipe,
        no confirmation). The stack is stopped (data preserved) so the
        regenerated config can be applied. Raises if the existing material is
        unavailable (regenerating would orphan the data — refused, not risked).
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

        Loads the existing ``.env`` secrets (reused so the regenerated config
        does not orphan the kept data). Stops the stack WITHOUT removing volumes
        and WITHOUT wiping the data root, so the regenerated compose can be
        applied over the intact data. Raises :class:`ReinstallAbortedError` if
        the existing material cannot be loaded (a preserve that minted new
        secrets would orphan the data — refused).

        Vault is deliberately not part of this: see the module docstring.
        """

        self.phases.append("preserve")
        self._log("[reinstall] Modo PRESERVAR: se conservan datos y se reutilizan los secretos.")

        existing = self.secret_loader.load(data_root=req.data_root)
        if existing is None:
            raise ReinstallAbortedError(
                "No se pudo leer el .env de la instalación existente; "
                "preservar regenerando secretos dejaría HUÉRFANOS los datos cifrados. "
                "Aporta los secretos existentes o usa una reinstalación limpia (--fresh)."
            )
        self._log(
            "[reinstall] Secretos existentes cargados del .env; se reutilizarán "
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
        report = self.purger.purge(req.data_root)
        for line in report.lines:
            self._log(f"[reinstall]   {line}")
        if not report.complete:
            # Continuar sería peor que parar: la reinstalación limpia mintea
            # secretos NUEVOS, y sobre un PGDATA viejo que sobrevivió el
            # `initdb` no vuelve a correr — Postgres rechazaría la contraseña
            # nueva y el stack quedaría a medio levantar con los datos del
            # despliegue anterior debajo. Aquí sólo se ha parado el stack.
            detail = "; ".join(f"{left.path} ({left.reason})" for left in report.leftovers)
            raise ReinstallAbortedError(
                "Reinstalación limpia abortada: la purga NO pudo eliminarlo todo, "
                f"queda: {detail}. El stack está parado y los datos que sobrevivieron "
                "siguen en disco; reinstalar encima con secretos nuevos dejaría el "
                "despliegue inconsistente. Libera esas rutas y vuelve a ejecutarlo."
            )

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
          * found + preserve  → reuse the existing secrets, keep data (no wipe);
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


#: The steps a PRESERVE reinstall runs — four of the six. See
#: :func:`run_preserve_pipeline` for why the other two are absent.
PRESERVE_STEP_ORDER: tuple[InstallStep, ...] = (
    InstallStep.GENERATE_CONFIG,
    InstallStep.PULL_IMAGES,
    InstallStep.START_STACK,
    InstallStep.RUN_MIGRATIONS,
)


def run_preserve_pipeline(
    executor: StepExecutor,
    config: dict[str, object],
    out: TextIO,
) -> None:
    """Regenerate config + compose over the PRESERVED data and bring the stack back.

    Runs :data:`PRESERVE_STEP_ORDER` in order against *executor*, streaming its
    log lines. Halts on the first :class:`~installer_backend.install.
    StepExecutionError` (which it re-raises) — a half-applied regeneration must
    not be reported as an upgrade.

    **Why this is not the install pipeline.** Two of the six steps are wrong
    over a deployment that already exists, and running them would be worse than
    skipping them:

    * ``BOOTSTRAP_VAULT`` — Vault is already initialised. Re-initialising is
      irreversible and would discard the material that decrypts the existing
      secret tree; and it cannot be reconciled either, because after the
      teardown Vault is SEALED and unsealing it is manual by ADR 0145. So the
      step is left out and the operator is told to unseal by hand, which is the
      same thing the upgrade runbook says after any restart.
    * ``SEED_TENANT`` — the tenant, the admin user and the built-in catalogue
      are already there. This step mints a NEW admin password with a CSPRNG on
      every run; over an existing deployment that would either fail or hand the
      operator a password that does not open the account.

    The four that DO run are exactly the ones that make a reinstall a
    reinstall: rewrite the generated config with the REUSED secrets, pull the
    (possibly newer) images, bring the stack up and apply migrations.
    """

    print("[reinstall] Regenerando configuración y levantando el stack…", file=out)
    for step in PRESERVE_STEP_ORDER:
        print(f"[reinstall] {step.value}…", file=out)
        for line in executor.execute(step, config):
            print(f"[reinstall]   {line}", file=out)

    skipped = ", ".join(
        step.value for step in INSTALL_STEP_ORDER if step not in PRESERVE_STEP_ORDER
    )
    print("", file=out)
    print("[reinstall] Reinstalación con preservación completada.", file=out)
    print(f"[reinstall] Pasos NO ejecutados (por diseño, ver el módulo): {skipped}.", file=out)
    print(
        "[reinstall] IMPORTANTE: Vault ha quedado SELLADO al reiniciar su "
        "contenedor y este proceso NO lo desella (ADR 0145: desellado manual). "
        "Deséllalo con tus unseal keys — runbook 06-runbooks/04-disaster-recovery.md "
        "— o los servicios no leerán los secretos de plataforma.",
        file=out,
    )
    print(
        "[reinstall] Los secretos existentes se han REUTILIZADO: las credenciales "
        "de acceso siguen siendo las de la instalación anterior (no hay revelado "
        "nuevo que mostrar).",
        file=out,
    )


def build_preserve_executor(
    cfg: InstallerConfig,
    secrets: GeneratedSecrets,
    *,
    monitoring: bool = False,
) -> StepExecutor:
    """Build the REAL executor for a PRESERVE, carrying the REUSED secrets.

    Same executor the first install uses; the only thing that differs — and the
    only thing that matters — is that ``secrets`` are the values read back from
    the existing ``.env`` instead of a fresh draw, so the regenerated ``.env``
    keeps the deployment's data readable.
    """

    from installer_backend.command_runner import SubprocessRunner
    from installer_backend.real_bindings import (
        RealDataTreeProvisioner,
        RealEnvFileWriter,
        build_hvac_vault_client,
    )
    from installer_backend.real_step_executor import RealStepExecutor

    return RealStepExecutor(
        compose_dir=cfg.storage.data_root,
        runner=SubprocessRunner(),
        env_writer=RealEnvFileWriter(),
        tree=RealDataTreeProvisioner(),
        # Nunca se llama: `PRESERVE_STEP_ORDER` no incluye BOOTSTRAP_VAULT. Se
        # pasa porque el ejecutor es una pieza compartida y construirlo entero
        # es libre de efectos (el factory sólo importa `hvac` cuando se invoca).
        vault_client_factory=build_hvac_vault_client,
        cfg=cfg,
        secrets=secrets,
        monitoring=monitoring,
    )


def build_default_reinstaller(
    out: TextIO,
    confirmer: Confirmer,
    *,
    data_root: str,
    dry_run: bool = False,
) -> Reinstaller:
    """Build a :class:`Reinstaller` with the REAL host bindings by default.

    This used to wire the four in-memory stubs unconditionally, and that is the
    whole of the 2026-08-27 blocking finding: with a stub detector that always
    answered "no prior install", a stub secret loader and recording
    teardown/purger, ``reinstall`` printed two lines and exited 0 having done
    nothing at all — over a running stack, and with two runbooks pointing at it
    as the way to upgrade. The uninstall next door had wired its real bindings
    since prod-01 task_19, which is what shows this was an oversight and not a
    design.

    ``dry_run=True`` still wires the stubs, but only as an EXPLICIT simulation:
    the CLI's :func:`~installer_backend.cli._assert_real_reinstall_seams` refuses
    to run them without the flag, exactly as install and uninstall do.
    """

    if dry_run:
        return Reinstaller(
            detector=StubInstallDetector(),
            secret_loader=StubExistingSecretLoader(),
            teardown=StubStackTeardown(),
            purger=StubDataPurger(),
            confirmer=confirmer,
            out=out,
        )

    from installer_backend.command_runner import SubprocessRunner
    from installer_backend.real_teardown import RealDataPurger, RealStackTeardown

    runner = SubprocessRunner()
    return Reinstaller(
        detector=RealInstallDetector(runner=runner),
        secret_loader=RealExistingSecretLoader(),
        teardown=RealStackTeardown(data_root, runner),
        purger=RealDataPurger(),
        confirmer=confirmer,
        out=out,
    )
