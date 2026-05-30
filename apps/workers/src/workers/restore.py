"""Full restore from a backup bundle (Plan 12 task_12_10).

The destructive other half of :mod:`workers.backup`. Where the backup engine
assembles a *timestamped bundle* (a pg_dump LOGICAL directory dump + per-volume
``tar.gz`` + a checksummed ``manifest.json``, optionally collapsed into one
AES-256-GCM ``bundle.tar.enc``), this engine puts a stack back together FROM
that bundle:

  1. **LOCATE + (if encrypted) DECRYPT** the bundle. An encrypted bundle is one
     blob keyed by a Vault secret; the same :class:`workers.backup_encryption.BackupEncryptor`
     primitive decrypts it back into the plaintext bundle layout. A plaintext
     bundle is used as-is.
  2. **VERIFY** the (decrypted) bundle against its manifest — checksums + the
     structural ``pg_restore --list`` / ``tar -tf`` probes — REUSING
     :class:`workers.backup_verification.BackupVerifier`. This is the
     *verify-before-restore* gate: a bundle that fails verification ABORTS the
     restore BEFORE any destructive command runs (fail closed). Restoring a
     corrupt bundle over a live stack is the worst possible outcome.
  3. **STOP the app stack** — ``docker compose stop`` of the *app* services
     (api-server, workers, web-app, …) while LEAVING PostgreSQL reachable so the
     dump can be restored into it. The DB + volume-backing services are NOT
     stopped here; they are stopped/started around the volume restore separately.
  4. **pg_restore** the LOGICAL directory dump into PostgreSQL with ``--clean
     --if-exists`` so existing objects are dropped + recreated (a full restore
     replaces the database contents).
  5. **RESTORE the volume tars** (MinIO / Vault / Redis) into their host mount
     paths: stop the volume-backing services, wipe + re-extract each volume's
     ``_data`` tree from its archive, so the restored volume is exactly the
     captured one (no stale files left behind).
  6. **RESTART the stack** cleanly — ``docker compose up -d`` brings every
     service back.

Design — reuse + the subprocess seam
------------------------------------
Real ``pg_restore`` / ``docker compose`` / live volume writes cannot run inside
the unit-test environment, so EVERY external command goes through the same
injectable :class:`workers.backup.CommandRunner` the backup engine uses
(production: :class:`workers.backup.SubprocessRunner`, explicit argv, never
``shell=True``; tests inject a fake that records argv). Tests therefore assert
*command construction* (pg_restore gets ``--clean --if-exists`` + the dump dir +
the libpq URL; the stop/up argv targets the right compose project + services),
*ordering* (verify → stop → pg_restore → volumes → start), and *failure
handling* (a non-zero step surfaces a typed :class:`RestoreError`; a bad bundle
aborts before any destructive command) — never a real restore of a live stack.
A real end-to-end restore is a HUMAN test (Plan 12 Tests Humanos human_12_02).

Safety
------
Restore is DESTRUCTIVE: it drops + recreates the database and wipes + re-extracts
volume trees. Two guards:

  * **double confirmation** — :meth:`run_full_restore` requires an explicit
    ``confirm`` token equal to the bundle id; a mismatch raises
    :class:`RestoreError` before anything runs. The UI (task_12_12) supplies it
    from the operator's second confirmation; the Celery task (a background job)
    forwards it.
  * **verify-before-restore, fail closed** — if the manifest verification fails,
    the restore aborts with the verification report attached and NOTHING
    destructive runs.

Secrets (the Vault decrypt key) are resolved through the same secret seam the
backup encryption layer uses (:class:`workers.secrets.SecretsProvider`); the key
is never plaintext, never logged.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

from workers.backup import (
    MANIFEST_FILENAME,
    CommandRunner,
    SubprocessRunner,
)
from workers.backup_encryption import (
    ENCRYPTED_SUFFIX,
    BackupEncryptionError,
    BackupEncryptor,
)
from workers.backup_verification import (
    BackupVerificationError,
    BackupVerifier,
    VerificationReport,
)
from workers.config import Settings, get_settings

_log = structlog.get_logger("workers.restore")

# The DB dump subtree inside a bundle (mirrors workers.backup._DB_DUMP_DIRNAME).
_DB_DUMP_DIRNAME = "postgres"

# On-disk name of the collapsed-then-encrypted bundle blob, and the plaintext
# tar it decrypts back into (mirror workers.backup's two constants).
_BUNDLE_ARCHIVE_NAME = "bundle.tar"
_ENCRYPTED_BUNDLE_NAME = _BUNDLE_ARCHIVE_NAME + ENCRYPTED_SUFFIX  # bundle.tar.enc


class RestoreError(RuntimeError):
    """Raised when a restore step fails or a precondition is not met.

    Covers a missing/locked bundle, a confirmation-token mismatch, a failed
    verification (fail closed), and any non-zero external command. The cause is
    chained where there is one; the message never echoes secret material.
    """


@dataclass(frozen=True)
class RestoreConfig:
    """Operator-tunable knobs for one full restore — no magic numbers.

    Built from :class:`workers.config.Settings` via :meth:`from_settings`. Tests
    construct it directly with ``tmp_path`` roots and a fake compose project.
    """

    backup_root: Path
    database_url: str
    volumes: tuple[str, ...]
    volumes_mount_root: Path
    # docker compose project name + the compose file path the stack control
    # commands target. Explicit so a restore never accidentally drives the wrong
    # project on a host running several compose stacks.
    compose_project: str
    compose_file: Path
    # The APP services stopped (and brought back up) around the restore. The DB
    # service is deliberately NOT here — it must stay reachable for pg_restore.
    app_services: tuple[str, ...]
    # The services that back the data volumes being restored — stopped while
    # their _data tree is wiped + re-extracted, then started again.
    volume_services: tuple[str, ...]
    # Optional at-rest encryption (mirrors BackupConfig). When the manifest says
    # the bundle is encrypted, an injected BackupEncryptor decrypts it; the Vault
    # key NAME (never the value) lives here so a default encryptor can be built.
    encryption_enabled: bool = False
    encryption_vault_key: str = "backup_encryption_key"
    # Wall-clock caps for the heavy commands. Generous; a legitimate multi-GB
    # restore must not be killed, but a hung command is a problem.
    pg_restore_timeout_s: int = 3600
    tar_timeout_s: int = 3600
    compose_timeout_s: int = 600

    @classmethod
    def from_settings(cls, settings: Settings) -> RestoreConfig:
        return cls(
            backup_root=Path(settings.backup_root),
            database_url=settings.backup_database_url,
            volumes=tuple(settings.backup_volumes),
            volumes_mount_root=Path(settings.backup_volumes_mount_root),
            compose_project=str(settings.restore_compose_project),
            compose_file=Path(settings.restore_compose_file),
            app_services=tuple(settings.restore_app_services),
            volume_services=tuple(settings.restore_volume_services),
            encryption_enabled=bool(settings.backup_encryption_enabled),
            encryption_vault_key=str(settings.backup_encryption_vault_key),
        )


@dataclass(frozen=True)
class RestoreResult:
    """What :meth:`RestoreEngine.run_full_restore` returns on success."""

    backup_id: str
    bundle_dir: str
    encrypted: bool
    restored_volumes: tuple[str, ...]
    verification: VerificationReport

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "bundle_dir": self.bundle_dir,
            "encrypted": self.encrypted,
            "restored_volumes": list(self.restored_volumes),
            "verification": self.verification.to_dict(),
        }


class RestoreVerificationError(RestoreError):
    """A :class:`RestoreError` carrying the failing verification report.

    Raised by the verify-before-restore gate so the caller (UI / task) can show
    exactly which artifact/check failed and confirm NOTHING destructive ran.
    """

    def __init__(self, report: VerificationReport) -> None:
        self.report = report
        failures = ", ".join(f"{c.check}:{c.artifact}" for c in report.failures)
        super().__init__(
            f"bundle {report.backup_id} failed verification before restore ({failures})"
        )


class RestoreEngine:
    """Orchestrates one full restore behind an injectable command runner.

    Construct with the same :class:`workers.backup.CommandRunner` seam the backup
    engine uses (production: :class:`SubprocessRunner`; tests: a fake). When the
    bundle is encrypted, an injected :class:`BackupEncryptor` decrypts it; in
    production it is built from settings in :func:`run_full_restore`.
    """

    def __init__(
        self,
        config: RestoreConfig,
        *,
        runner: CommandRunner | None = None,
        encryptor: BackupEncryptor | None = None,
        verifier: BackupVerifier | None = None,
    ) -> None:
        self._config = config
        self._runner: CommandRunner = runner or SubprocessRunner()
        self._encryptor = encryptor
        # The verifier shares the SAME runner so its pg_restore --list / tar -tf
        # probes are mocked alongside the restore commands in tests.
        self._verifier = verifier or BackupVerifier(runner=self._runner, encryptor=encryptor)

    @property
    def config(self) -> RestoreConfig:
        return self._config

    # -- public API ---------------------------------------------------------

    def run_full_restore(self, bundle: str | Path, *, confirm: str) -> RestoreResult:
        """Restore the whole stack from ``bundle`` (a bundle id or a path).

        Sequence: locate → (decrypt) → VERIFY (fail closed) → stop app stack →
        pg_restore → restore volumes → restart stack. ``confirm`` MUST equal the
        bundle id (the double-confirmation guard); a mismatch raises
        :class:`RestoreError` before anything runs.

        Raises :class:`RestoreError` (a :class:`RestoreVerificationError` when
        the bundle is corrupt) if any precondition or step fails. On a
        verification failure NOTHING destructive runs.
        """
        bundle_dir = self._locate_bundle(bundle)
        backup_id = bundle_dir.name

        # -- DOUBLE CONFIRMATION (before any work, never destructive on mismatch)
        if confirm != backup_id:
            raise RestoreError(
                f"restore confirmation token does not match the bundle id "
                f"{backup_id!r}; refusing to run a destructive restore"
            )

        _log.info("restore.start", backup_id=backup_id, bundle_dir=str(bundle_dir))

        # -- LOCATE + DECRYPT: read the manifest, decrypt an encrypted bundle in
        # place into the plaintext layout the rest of the flow + verifier expect.
        manifest = self._load_manifest(bundle_dir)
        encrypted = bool(manifest.get("encrypted", False))
        if encrypted:
            self._decrypt_bundle(bundle_dir)

        # -- VERIFY before touching anything (fail closed). The verifier re-reads
        # the (now plaintext) manifest + artifacts and runs checksum + structural
        # probes. A corrupt bundle aborts the restore here.
        report = self._verify(bundle_dir)
        if not report.valid:
            _log.warning(
                "restore.aborted.verification_failed",
                backup_id=backup_id,
                failures=[c.check + ":" + c.artifact for c in report.failures],
            )
            raise RestoreVerificationError(report)

        # -- DESTRUCTIVE from here on. Stop the app stack (DB stays reachable).
        self._stop_app_stack()
        try:
            self._pg_restore(bundle_dir)
            restored = self._restore_volumes(bundle_dir, manifest)
        finally:
            # Always bring the stack back up — even if a step failed, leaving the
            # stack down is worse than a partially-restored, running stack the
            # operator can re-run against.
            self._start_stack()

        _log.info(
            "restore.done",
            backup_id=backup_id,
            encrypted=encrypted,
            volumes=len(restored),
        )
        return RestoreResult(
            backup_id=backup_id,
            bundle_dir=str(bundle_dir),
            encrypted=encrypted,
            restored_volumes=restored,
            verification=report,
        )

    # -- locate + decrypt ----------------------------------------------------

    def _locate_bundle(self, bundle: str | Path) -> Path:
        """Resolve ``bundle`` (a bundle id or a path) to an existing directory.

        A bare id (no path separator) is looked up under ``backup_root``; an
        absolute/relative path is used directly. A missing bundle raises
        :class:`RestoreError` — restore never fabricates a target.
        """
        candidate = Path(bundle)
        if candidate.is_absolute() or len(candidate.parts) > 1:
            bundle_dir = candidate
        else:
            bundle_dir = self._config.backup_root / candidate
        if not bundle_dir.is_dir():
            raise RestoreError(f"backup bundle not found: {bundle_dir}")
        return bundle_dir

    def _load_manifest(self, bundle_dir: Path) -> dict[str, Any]:
        manifest_path = bundle_dir / MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise RestoreError(f"no {MANIFEST_FILENAME} in bundle {bundle_dir}")
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RestoreError(f"could not read manifest at {manifest_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise RestoreError(f"manifest at {manifest_path} is not a JSON object")
        return data

    def _decrypt_bundle(self, bundle_dir: Path) -> None:
        """Decrypt ``bundle.tar.enc`` (Vault key) + un-tar it into the bundle.

        Reverses :meth:`workers.backup.BackupEngine._encrypt_bundle`: the blob is
        decrypted to ``bundle.tar`` via the injected :class:`BackupEncryptor`
        (GCM-authenticated — a tampered blob raises and aborts the restore), then
        extracted in place so the plaintext DB dump + volume tars are present for
        the verifier + the restore steps. Decryption to disk is unavoidable here
        (unlike the routine corruption check) because pg_restore + tar need the
        real files. The intermediate ``bundle.tar`` is removed afterwards.
        """
        encryptor = self._encryptor
        if encryptor is None:
            raise RestoreError(
                "bundle is encrypted but no BackupEncryptor was provided "
                "(the Vault key could not be wired)"
            )
        blob_path = bundle_dir / _ENCRYPTED_BUNDLE_NAME
        if not blob_path.is_file():
            raise RestoreError(
                f"manifest says the bundle is encrypted but {_ENCRYPTED_BUNDLE_NAME} is missing"
            )
        archive_path = bundle_dir / _BUNDLE_ARCHIVE_NAME
        try:
            encryptor.decrypt_file(blob_path, archive_path)
        except BackupEncryptionError as exc:
            # Tampered/truncated blob or the wrong key — fail closed, never echo
            # key material (BackupEncryptionError is already non-leaky).
            raise RestoreError(f"failed to decrypt backup bundle: {exc}") from exc
        # Un-tar the plaintext bundle in place (members land at the bundle root).
        args = [
            "tar",
            "--extract",
            f"--directory={bundle_dir}",
            f"--file={archive_path}",
        ]
        result = self._runner.run(args, timeout=self._config.tar_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"extracting the decrypted bundle failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        # Drop the intermediate plaintext tar — only the extracted layout is used.
        archive_path.unlink(missing_ok=True)
        _log.info("restore.decrypted", vault_key_name=self._config.encryption_vault_key)

    def _verify(self, bundle_dir: Path) -> VerificationReport:
        """Run the manifest verification (REUSED from task_12_03).

        A verifier *operational* failure (unreadable manifest, etc.) is itself a
        reason to abort: an unverifiable bundle is not trustworthy, so we map it
        to a :class:`RestoreError` rather than proceeding.
        """
        try:
            return self._verifier.verify_bundle(bundle_dir)
        except BackupVerificationError as exc:
            raise RestoreError(f"could not verify bundle before restore: {exc}") from exc

    # -- stack control -------------------------------------------------------

    def _compose_base(self) -> list[str]:
        """The ``docker compose -p <project> -f <file>`` prefix every op shares."""
        return [
            "docker",
            "compose",
            "--project-name",
            self._config.compose_project,
            "--file",
            str(self._config.compose_file),
        ]

    def _stop_app_stack(self) -> None:
        """``docker compose stop`` the app services (DB stays reachable).

        Only the app services are stopped here — Postgres must remain up so
        pg_restore can connect. Stopping the writers first guarantees no app
        process mutates the DB mid-restore.
        """
        if not self._config.app_services:
            return
        args = [*self._compose_base(), "stop", *self._config.app_services]
        result = self._runner.run(args, timeout=self._config.compose_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"stopping the app stack failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        _log.info("restore.app_stack_stopped", services=list(self._config.app_services))

    def _start_stack(self) -> None:
        """``docker compose up -d`` — bring the whole stack back cleanly."""
        args = [*self._compose_base(), "up", "--detach"]
        result = self._runner.run(args, timeout=self._config.compose_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"restarting the stack failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        _log.info("restore.stack_started")

    # -- pg_restore ----------------------------------------------------------

    def _pg_restore(self, bundle_dir: Path) -> None:
        """``pg_restore`` the LOGICAL directory dump into PostgreSQL.

        ``--clean --if-exists`` drops each object before recreating it (so a full
        restore replaces existing contents without erroring on absent objects);
        ``--no-owner --no-privileges`` mirror the dump flags so ownership/ACLs are
        not required to match. ``--dbname`` carries the full libpq URL so the
        password never lands in a separate logged arg.
        """
        dump_dir = bundle_dir / _DB_DUMP_DIRNAME
        if not dump_dir.is_dir():
            raise RestoreError(f"bundle has no pg_dump directory at {dump_dir}")
        args = [
            "pg_restore",
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            f"--dbname={self._config.database_url}",
            str(dump_dir),
        ]
        result = self._runner.run(args, timeout=self._config.pg_restore_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"pg_restore failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        _log.info("restore.pg_restored", dump_dir=str(dump_dir))

    # -- volume restore ------------------------------------------------------

    def _restore_volumes(self, bundle_dir: Path, manifest: dict[str, Any]) -> tuple[str, ...]:
        """Restore each captured volume tar into its host ``_data`` tree.

        Stops the volume-backing services first (so nothing holds the files
        open), then for each ``volume_tar`` artifact in the manifest: wipe the
        volume's ``_data`` tree and re-extract the archive into it, so the
        restored volume is EXACTLY the captured one (no stale files survive).
        Only volumes the manifest actually captured are touched.
        """
        volume_artifacts = [
            a for a in manifest.get("artifacts", []) if a.get("kind") == "volume_tar"
        ]
        if not volume_artifacts:
            return ()

        self._stop_volume_services()
        restored: list[str] = []
        for art in volume_artifacts:
            volume = str(art.get("source") or "")
            archive_name = str(art.get("path") or art.get("name") or "")
            if not volume or not archive_name:
                raise RestoreError(f"volume artifact in manifest is missing source/path: {art!r}")
            self._restore_one_volume(bundle_dir, volume=volume, archive_name=archive_name)
            restored.append(volume)
        return tuple(restored)

    def _stop_volume_services(self) -> None:
        """``docker compose stop`` the services backing the restored volumes."""
        if not self._config.volume_services:
            return
        args = [*self._compose_base(), "stop", *self._config.volume_services]
        result = self._runner.run(args, timeout=self._config.compose_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"stopping volume services failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        _log.info("restore.volume_services_stopped", services=list(self._config.volume_services))

    def _restore_one_volume(self, bundle_dir: Path, *, volume: str, archive_name: str) -> None:
        """Wipe + re-extract one volume's ``_data`` tree from its archive.

        Mirrors :meth:`workers.backup.BackupEngine._tar_volume` in reverse: the
        archive holds the volume's contents at its root, so we extract with
        ``--directory=<mount_root>/<volume>/_data`` after clearing that dir.
        """
        archive_path = bundle_dir / archive_name
        if not archive_path.is_file():
            raise RestoreError(f"volume archive missing in bundle: {archive_path}")
        target_dir = self._config.volumes_mount_root / volume / "_data"
        # Clear the existing volume contents so no stale file outlives the restore,
        # then recreate the empty mount point. Best-effort wipe; recreate is hard.
        if target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)
        target_dir.mkdir(parents=True, exist_ok=True)
        args = [
            "tar",
            "--extract",
            "--gzip",
            f"--directory={target_dir}",
            f"--file={archive_path}",
        ]
        result = self._runner.run(args, timeout=self._config.tar_timeout_s)
        if result.returncode != 0:
            raise RestoreError(
                f"restoring volume {volume!r} failed (rc={result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        _log.info("restore.volume_restored", volume=volume, target=str(target_dir))


def run_full_restore(
    bundle: str | Path,
    *,
    confirm: str,
    settings: Settings | None = None,
    runner: CommandRunner | None = None,
    encryptor: BackupEncryptor | None = None,
) -> RestoreResult:
    """Convenience entrypoint: build the engine from settings and run a restore.

    This is what the restore Celery task (a background job, task_12_12) and a
    future ``scripts/restore.sh`` call. ``runner`` / ``encryptor`` are injectable
    for tests; production leaves them ``None`` (real subprocess; and — when the
    bundle is encrypted — a default Vault/env-backed :class:`BackupEncryptor`
    built here so the engine can decrypt the blob).
    """
    cfg = RestoreConfig.from_settings(settings or get_settings())
    if encryptor is None and cfg.encryption_enabled:
        from workers.backup_encryption import EnvSecretsProvider

        encryptor = BackupEncryptor(
            provider=EnvSecretsProvider(),
            vault_key_name=cfg.encryption_vault_key,
        )
    return RestoreEngine(cfg, runner=runner, encryptor=encryptor).run_full_restore(
        bundle, confirm=confirm
    )


__all__ = [
    "RestoreConfig",
    "RestoreEngine",
    "RestoreError",
    "RestoreResult",
    "RestoreVerificationError",
    "run_full_restore",
]
