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
    4. bootstrap_vault — init + unseal + KV v2 + policies (task 15_09).
    5. seed_tenant     — create the initial tenant + admin user.
    6. finalize        — arm the one-time reveal (task 15_06) and print the
                         credentials + Vault unseal keys ONCE.

Steps 1-5 are exactly :data:`installer_backend.install.INSTALL_STEP_ORDER`, run
by the same :class:`~installer_backend.install.InstallOrchestrator` the
``/api/install/stream`` route uses. The CLI adds the prereq gate in front (the
wizard gates it in step 1) and the finalize reveal at the end (the wizard's
step 9). :func:`headless_pipeline` is the single source of truth for the named
phases so a test can assert the CLI runs the wizard's pipeline.

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
    5  ABORTED       — the operator declined a destructive confirmation.

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
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol, TextIO, runtime_checkable

import yaml
from pydantic import ValidationError

from installer_backend.config import InstallerConfig, validate_config
from installer_backend.finalize import FinalizeService, InstallCredentials, RevealPayload
from installer_backend.install import (
    INSTALL_STEP_ORDER,
    FakeStepExecutor,
    InstallOrchestrator,
    StepExecutor,
)
from installer_backend.seams import (
    InstallerLifecycle,
    PrereqChecker,
    StubInstallerLifecycle,
    StubPrereqChecker,
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
# Seam factory — built fresh per run so the in-memory stubs don't leak state.
# Phase B / tests override these with real / fake bindings.
# ---------------------------------------------------------------------------
def build_default_installer(out: TextIO) -> HeadlessInstaller:
    """Build a :class:`HeadlessInstaller` wired to the in-memory stub seams.

    The defaults make ``python -m installer_backend.cli`` import-safe and
    runnable on a host with no Docker (it goes through the fakes). The real
    install replaces these with host bindings; tests inject scripted fakes.
    """

    lifecycle: InstallerLifecycle = StubInstallerLifecycle()
    return HeadlessInstaller(
        prereq_checker=StubPrereqChecker(),
        executor=FakeStepExecutor(),
        credential_builder=StubCredentialBuilder(),
        finalize=FinalizeService(lifecycle=lifecycle),
        out=out,
    )


# ---------------------------------------------------------------------------
# Argument parsing + the `install` subcommand.
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (``install`` subcommand)."""

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
) -> ExitCode:
    """Load *config_path* and run the unattended install.

    Returns the :class:`ExitCode`. ``installer`` is injectable (tests pass one
    wired to fakes / failure scenarios); when omitted a default stub-wired
    installer is built against *out*. The config gate runs FIRST, so a malformed
    config returns :data:`ExitCode.CONFIG` with no provisioning attempted.
    """

    stream = out if out is not None else sys.stdout
    inst = installer if installer is not None else build_default_installer(stream)

    text = _read_config_file(config_path)
    config = load_install_config(text)
    inst.run(config)
    return ExitCode.OK


def main(argv: Sequence[str] | None = None, *, out: TextIO | None = None) -> int:
    """CLI entry point. Returns a process exit code (see :class:`ExitCode`).

    ``scripts/install.sh`` execs ``python -m installer_backend.cli install
    --config install.yaml`` and propagates this return value as ``$?``. All
    failures are caught and mapped to their documented exit code with a clear
    stderr line; nothing here logs a secret.
    """

    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse exits 2 on bad args; normalise to our USAGE code.
        return int(ExitCode.USAGE) if exc.code not in (0, None) else int(ExitCode.OK)

    try:
        if args.command == "install":
            return int(run_install(args.config, out=out))
    except CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return int(exc.code)

    # Unreachable (subparser is required), kept for exhaustiveness.
    print("error: comando desconocido.", file=sys.stderr)  # pragma: no cover
    return int(ExitCode.USAGE)  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
