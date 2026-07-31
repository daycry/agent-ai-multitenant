"""Post-backup corruption check (Plan 12 task_12_03).

Plan 12 Riesgos: *"Backup corrupto sin detectar … Verificación post-backup
(pg_restore --list, tar -tf)."* Once :mod:`workers.backup` has assembled a
bundle (DB dump + volume tars + ``manifest.json``), this module re-reads that
manifest and **proves the artifacts are intact** before anyone trusts the
backup at restore time. A bundle that fails verification is marked INVALID; in
Phase D that INVALID verdict is the basis for the *"last backup failed"* alert.

Three independent checks, every one behind the injectable command runner
-----------------------------------------------------------------------
1. **pg_restore --list** against the directory-format dump. ``pg_restore -l``
   parses the dump's table-of-contents WITHOUT touching a database; a non-zero
   exit (or an empty / unparseable TOC) means the dump is corrupt or truncated.
   We additionally require the TOC to be non-empty (at least one archive entry),
   because a structurally-valid-but-empty dump is itself suspicious.
2. **tar -tf** against each volume archive. ``tar -t`` lists an archive's
   members without extracting; a non-zero exit means the gzip/tar stream is
   damaged.
3. **checksum match** against the manifest. Every artifact's on-disk SHA-256 is
   recomputed and compared with the value :mod:`workers.backup` recorded. A
   mismatch means the bytes changed after the manifest was written (bit-rot, a
   truncated copy, tampering).

ANY failed check makes the whole report ``valid == False``. The report is a
typed object (:class:`VerificationReport`) carrying the overall verdict plus a
:class:`CheckResult` per check, so the caller (and the future alert) can see
exactly *what* failed, not just *that* something did.

Encryption (task_12_02)
-----------------------
When the manifest says ``encrypted: true`` the bundle is a single
AES-256-GCM blob (``bundle.tar.enc``) — there is no plaintext pg_dump dir or
volume tar to ``pg_restore --list`` / ``tar -tf`` without first decrypting, and
decrypting the whole bundle to disk during a routine corruption check would
defeat the point of at-rest encryption. So for an encrypted bundle we verify
the **checksum of the blob** (catches bit-rot / truncation) and, when a
:class:`BackupEncryptor` is injected, additionally confirm the blob *decrypts*
(GCM authentication — a tampered blob fails the tag). That decrypt check streams
through memory and writes nothing to disk.

The subprocess seam
-------------------
Real ``pg_restore`` / ``tar`` cannot run in the unit-test environment, so they
go through the same :class:`workers.backup.CommandRunner` the backup engine
uses. Tests inject a fake that records argv and returns a chosen exit code; the
tests assert command construction (``pg_restore --list`` against the dump dir,
``tar -tf`` against each archive), the verdict logic (good → valid; a
``pg_restore`` error → invalid; a ``tar`` error → invalid; a checksum mismatch
→ invalid), and that the result is the typed per-check report.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from workers.backup import (
    MANIFEST_FILENAME,
    CommandRunner,
    SubprocessRunner,
    _checksum_file,
    _checksum_tree,
)
from workers.backup_encryption import BackupEncryptionError, BackupEncryptor

_log = structlog.get_logger("workers.backup_verification")

# Wall-clock cap for a list-only introspection command. Generous, but a hung
# pg_restore/tar must not wedge the check forever. Operator-tunable via the
# verifier's constructor — never a magic number baked into the call site.
DEFAULT_VERIFY_TIMEOUT_S = 600

# Per-check names — stable identifiers the alert layer (Phase D) keys on.
CHECK_PG_RESTORE_LIST = "pg_restore_list"
CHECK_TAR_LIST = "tar_list"
CHECK_CHECKSUM = "checksum"
CHECK_DECRYPT = "decrypt"


class BackupVerificationError(RuntimeError):
    """Raised only for an *operational* failure of the verifier itself — a
    missing bundle directory or an unreadable/garbled manifest. A backup whose
    artifacts are merely corrupt does NOT raise: it returns a report with
    ``valid == False`` (that is the expected, handled outcome)."""


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one verification check against one artifact."""

    check: str  # one of the CHECK_* constants
    artifact: str  # the artifact name/path this check ran against
    ok: bool
    detail: str = ""  # human-readable reason, esp. on failure

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass(frozen=True)
class VerificationReport:
    """The typed verdict of verifying one backup bundle.

    ``valid`` is the AND of every check. ``checks`` carries the per-check detail
    so the caller can see exactly which artifact / which check failed.
    """

    backup_id: str
    bundle_dir: str
    encrypted: bool
    valid: bool
    checks: tuple[CheckResult, ...] = field(default_factory=tuple)

    @property
    def failures(self) -> tuple[CheckResult, ...]:
        return tuple(c for c in self.checks if not c.ok)

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_id": self.backup_id,
            "bundle_dir": self.bundle_dir,
            "encrypted": self.encrypted,
            "valid": self.valid,
            "checks": [c.to_dict() for c in self.checks],
        }


class BackupVerifier:
    """Verifies a completed backup bundle behind an injectable command runner.

    Construct with the same :class:`workers.backup.CommandRunner` the engine
    uses (production: :class:`SubprocessRunner`; tests: a fake). Pass an
    :class:`workers.backup_encryption.BackupEncryptor` to additionally prove an
    encrypted bundle decrypts (GCM authentication).
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        encryptor: BackupEncryptor | None = None,
        timeout_s: int = DEFAULT_VERIFY_TIMEOUT_S,
    ) -> None:
        self._runner: CommandRunner = runner or SubprocessRunner()
        self._encryptor = encryptor
        self._timeout_s = timeout_s

    # -- public API ---------------------------------------------------------

    def verify_bundle(self, bundle_dir: Path) -> VerificationReport:
        """Verify the bundle rooted at ``bundle_dir`` and return the report.

        Reads ``manifest.json`` and runs every applicable check. Raises
        :class:`BackupVerificationError` only if the bundle / manifest cannot be
        read at all; a corrupt-but-present backup returns ``valid == False``.
        """
        manifest = self._load_manifest(bundle_dir)
        backup_id = str(manifest.get("backup_id", bundle_dir.name))
        encrypted = bool(manifest.get("encrypted", False))
        artifacts = manifest.get("artifacts", [])
        if not isinstance(artifacts, list):
            raise BackupVerificationError(
                f"manifest at {bundle_dir} has a malformed 'artifacts' field"
            )

        checks: list[CheckResult] = []
        for art in artifacts:
            checks.extend(self._verify_artifact(bundle_dir, art))

        valid = all(c.ok for c in checks) and len(checks) > 0
        report = VerificationReport(
            backup_id=backup_id,
            bundle_dir=str(bundle_dir),
            encrypted=encrypted,
            valid=valid,
            checks=tuple(checks),
        )
        if valid:
            _log.info("backup.verify.ok", backup_id=backup_id, checks=len(checks))
        else:
            _log.warning(
                "backup.verify.invalid",
                backup_id=backup_id,
                failures=[c.check + ":" + c.artifact for c in report.failures],
            )
        return report

    # -- internals ----------------------------------------------------------

    def _load_manifest(self, bundle_dir: Path) -> dict[str, Any]:
        manifest_path = bundle_dir / MANIFEST_FILENAME
        if not manifest_path.is_file():
            raise BackupVerificationError(f"no {MANIFEST_FILENAME} at {bundle_dir}")
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BackupVerificationError(
                f"could not read manifest at {manifest_path}: {exc}"
            ) from exc
        if not isinstance(data, dict):
            raise BackupVerificationError(f"manifest at {manifest_path} is not a JSON object")
        return data

    def _verify_artifact(self, bundle_dir: Path, art: dict[str, Any]) -> list[CheckResult]:
        """Run every check that applies to one artifact record."""
        name = str(art.get("name", art.get("path", "<unknown>")))
        rel_path = str(art.get("path", name))
        kind = str(art.get("kind", ""))
        target = bundle_dir / rel_path

        results: list[CheckResult] = []

        # 1. Existence — every later check assumes the file is there.
        if not target.exists():
            results.append(
                CheckResult(
                    check=CHECK_CHECKSUM,
                    artifact=name,
                    ok=False,
                    detail=f"artifact missing on disk: {rel_path}",
                )
            )
            return results

        # 2. Checksum match against the manifest (applies to every artifact).
        results.append(self._check_checksum(target, art, name=name))

        # 3. Structural checks per kind.
        if kind == "pg_dump":
            results.append(self._check_pg_restore_list(target, name=name))
        elif kind in ("volume_tar", "bind_tar", "projects_tar", "redis_tar"):
            # prod-04 (auditoría 2026-07-06): el bind_tar (bare repos + worktrees)
            # es también un .tar.gz y merece el mismo `tar -tf` estructural que un
            # volume_tar — antes solo se le comprobaba el checksum, así que una
            # corrupción coherente con el manifest pasaba como válida.
            # prod-04 task_prod_04_05: y el projects_tar (los bare repos como
            # artefacto propio) por la misma razón — es el código de los clientes.
            results.append(self._check_tar_list(target, name=name))
        elif kind == "encrypted_bundle" and self._encryptor is not None:
            # The plaintext is wrapped; only the blob's checksum is verifiable
            # offline. When an encryptor is wired, additionally prove it decrypts
            # (GCM tag) — the same authenticated-cipher guard restore relies on.
            results.append(self._check_decrypts(target, name=name))
        # An unknown kind gets only the checksum check (still meaningful).

        return results

    def _check_checksum(self, target: Path, art: dict[str, Any], *, name: str) -> CheckResult:
        expected = str(art.get("sha256", ""))
        if not expected:
            return CheckResult(
                check=CHECK_CHECKSUM,
                artifact=name,
                ok=False,
                detail="manifest has no sha256 for this artifact",
            )
        actual = _checksum_tree(target) if target.is_dir() else _checksum_file(target)
        if actual != expected:
            return CheckResult(
                check=CHECK_CHECKSUM,
                artifact=name,
                ok=False,
                detail=f"sha256 mismatch (manifest {expected[:12]}…, disk {actual[:12]}…)",
            )
        return CheckResult(check=CHECK_CHECKSUM, artifact=name, ok=True)

    def _check_pg_restore_list(self, dump_dir: Path, *, name: str) -> CheckResult:
        """``pg_restore --list`` the directory dump: parses + has entries.

        ``-l`` reads only the archive's TOC, never connecting to a DB, so it is
        a safe offline integrity probe. A non-zero exit means the dump is
        corrupt/truncated; an empty TOC (no archive entries) is also treated as
        a failure — a structurally-valid but empty dump is not a usable backup.
        """
        args = ["pg_restore", "--list", str(dump_dir)]
        result = self._runner.run(args, timeout=self._timeout_s)
        if result.returncode != 0:
            return CheckResult(
                check=CHECK_PG_RESTORE_LIST,
                artifact=name,
                ok=False,
                detail=(
                    f"pg_restore --list failed (rc={result.returncode}): "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                ),
            )
        if not _toc_has_entries(result.stdout):
            return CheckResult(
                check=CHECK_PG_RESTORE_LIST,
                artifact=name,
                ok=False,
                detail="pg_restore --list returned an empty table of contents",
            )
        return CheckResult(check=CHECK_PG_RESTORE_LIST, artifact=name, ok=True)

    def _check_tar_list(self, archive: Path, *, name: str) -> CheckResult:
        """``tar -tf`` the volume archive: lists members without extracting."""
        args = ["tar", "--list", "--gzip", f"--file={archive}"]
        result = self._runner.run(args, timeout=self._timeout_s)
        if result.returncode != 0:
            return CheckResult(
                check=CHECK_TAR_LIST,
                artifact=name,
                ok=False,
                detail=(
                    f"tar --list failed (rc={result.returncode}): "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                ),
            )
        return CheckResult(check=CHECK_TAR_LIST, artifact=name, ok=True)

    def _check_decrypts(self, blob: Path, *, name: str) -> CheckResult:
        """Confirm the encrypted blob decrypts (GCM authentication).

        Streams through memory — writes no plaintext to disk. A tampered/
        truncated blob or the wrong key raises
        :class:`BackupEncryptionError`, which we fold into a failed check.
        """
        assert self._encryptor is not None  # only called when wired
        try:
            self._encryptor.decrypt_bytes(blob.read_bytes())
        except BackupEncryptionError as exc:
            return CheckResult(
                check=CHECK_DECRYPT,
                artifact=name,
                ok=False,
                detail=f"encrypted bundle did not decrypt: {exc}",
            )
        except OSError as exc:
            return CheckResult(
                check=CHECK_DECRYPT,
                artifact=name,
                ok=False,
                detail=f"could not read encrypted bundle: {exc}",
            )
        return CheckResult(check=CHECK_DECRYPT, artifact=name, ok=True)


def _toc_has_entries(stdout: str) -> bool:
    """True if ``pg_restore --list`` output names at least one archive entry.

    ``pg_restore -l`` prints a header of ``;``-comment lines followed by one
    line per TOC entry (``<dumpId>; <oid> <oid> <desc> …``). A dump with real
    objects has at least one non-comment, non-blank line.
    """
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(";"):
            return True
    return False


def verify_bundle(
    bundle_dir: Path,
    *,
    runner: CommandRunner | None = None,
    encryptor: BackupEncryptor | None = None,
    timeout_s: int = DEFAULT_VERIFY_TIMEOUT_S,
) -> VerificationReport:
    """Convenience entrypoint: build a :class:`BackupVerifier` and verify.

    ``runner`` / ``encryptor`` are injectable for tests; production leaves them
    ``None`` (real subprocess; an encryptor only needed to additionally prove an
    encrypted bundle decrypts).
    """
    return BackupVerifier(runner=runner, encryptor=encryptor, timeout_s=timeout_s).verify_bundle(
        bundle_dir
    )


__all__ = [
    "CHECK_CHECKSUM",
    "CHECK_DECRYPT",
    "CHECK_PG_RESTORE_LIST",
    "CHECK_TAR_LIST",
    "DEFAULT_VERIFY_TIMEOUT_S",
    "BackupVerificationError",
    "BackupVerifier",
    "CheckResult",
    "VerificationReport",
    "verify_bundle",
]
