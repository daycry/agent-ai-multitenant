"""Integration tests for post-backup corruption check (Plan 12 task_12_03).

Real ``pg_dump`` / ``tar`` / ``pg_restore`` cannot run in the test environment,
so the external-command seam (:class:`workers.backup.CommandRunner`) is MOCKED.
One fake runner both *builds* a bundle (fabricating the pg_dump dir + the volume
tars the engine asks for) AND *answers the verification probes*
(``pg_restore --list`` → a fake TOC, ``tar --list`` → member names), with knobs
to make any one probe fail.

The tests assert:

  * COMMAND CONSTRUCTION — verification runs ``pg_restore --list`` against the
    directory dump and ``tar --list`` against each volume archive.
  * GOOD BACKUP → VALID — every check passes; the report is ``valid == True``
    with one checksum check per artifact plus the structural probes.
  * pg_restore --list ERROR → INVALID — a non-zero pg_restore exit flips the
    verdict; the failing check is reported.
  * tar -tf ERROR → INVALID — a non-zero tar exit flips the verdict.
  * CHECKSUM MISMATCH → INVALID — mutating an artifact after the manifest is
    written is caught by the recomputed SHA-256.
  * EMPTY TOC → INVALID — a pg_restore --list that parses but lists no objects
    is not a usable dump.
  * TYPED REPORT — the result is a :class:`VerificationReport` with per-check
    :class:`CheckResult` detail, not a bare bool.

No real backup of the live stack runs here.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pytest
from workers.backup import (
    BackupConfig,
    BackupEngine,
    CommandResult,
)
from workers.backup_encryption import BackupEncryptor
from workers.backup_verification import (
    CHECK_CHECKSUM,
    CHECK_DECRYPT,
    CHECK_PG_RESTORE_LIST,
    CHECK_TAR_LIST,
    BackupVerificationError,
    BackupVerifier,
    CheckResult,
    VerificationReport,
    verify_bundle,
)
from workers.secrets import StaticSecretsProvider

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 30, 3, 0, 0, tzinfo=UTC)

_VAULT_KEY_NAME = "backup_encryption_key"
_VAULT_KEY_VALUE = "s3cr3t-vault-backup-key-0123456789abcdef"

# A canned, non-empty pg_restore --list TOC: a header of ``;`` comment lines
# followed by one real archive entry line.
_GOOD_TOC = (
    ";\n"
    "; Archive created at 2026-05-30 03:00:00 UTC\n"
    ";     dbname: agentic_platform\n"
    ";\n"
    "215; 1259 16květ TABLE public tenants migrations_user\n"
)
# A TOC that parses but lists no archive objects (only comment/blank lines).
_EMPTY_TOC = ";\n; Archive created\n;\n"


@dataclass
class FakeRunner:
    """Builds the bundle AND answers verification probes; configurable failures.

    ``fail_pg_restore`` / ``fail_tar_list`` make the matching verification probe
    return a non-zero result. ``empty_toc`` makes ``pg_restore --list`` succeed
    but emit a TOC with no archive entries.
    """

    fail_pg_restore: bool = False
    fail_tar_list: bool = False
    empty_toc: bool = False
    calls: list[list[str]] = field(default_factory=list)

    def run(
        self,
        args: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        argv = list(args)
        self.calls.append(argv)

        # --- backup-build commands (fabricate artifacts) ---
        if argv[0] == "pg_dump":
            out_dir = Path(_arg_value(argv, "--file="))
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "toc.dat").write_bytes(b"fake-toc")
            (out_dir / "3434.dat.gz").write_bytes(b"fake-table-data")
            return CommandResult(returncode=0)
        if argv[0] == "tar" and "--list" not in argv:
            archive = Path(_arg_value(argv, "--file="))
            archive.write_bytes(b"fake-tar-gz-bytes")
            return CommandResult(returncode=0)

        # --- verification probes ---
        if argv[0] == "pg_restore" and "--list" in argv:
            if self.fail_pg_restore:
                return CommandResult(returncode=1, stderr="pg_restore: error: corrupt archive")
            return CommandResult(returncode=0, stdout=_EMPTY_TOC if self.empty_toc else _GOOD_TOC)
        if argv[0] == "tar" and "--list" in argv:
            if self.fail_tar_list:
                return CommandResult(returncode=2, stderr="tar: Unexpected EOF in archive")
            return CommandResult(returncode=0, stdout="./\n./objects/\n")

        raise AssertionError(f"unexpected command: {argv!r}")


def _arg_value(argv: list[str], prefix: str) -> str:
    for token in argv:
        if token.startswith(prefix):
            return token[len(prefix) :]
    raise AssertionError(f"no arg with prefix {prefix!r} in {argv!r}")


def _config(tmp_path: Path, *, encryption_enabled: bool = False) -> BackupConfig:
    return BackupConfig(
        backup_root=tmp_path / "backups",
        database_url="postgresql://migrations_user:db-pw@db:5432/agentic_platform",
        volumes=("minio_data", "redis_data"),
        volumes_mount_root=tmp_path / "volumes",
        retention_days=7,
        encryption_enabled=encryption_enabled,
        encryption_vault_key=_VAULT_KEY_NAME,
    )


def _encryptor() -> BackupEncryptor:
    return BackupEncryptor(
        provider=StaticSecretsProvider(values={_VAULT_KEY_NAME: _VAULT_KEY_VALUE}),
        vault_key_name=_VAULT_KEY_NAME,
    )


def _build_bundle(tmp_path: Path, runner: FakeRunner, **cfg_kw: object) -> Path:
    """Run the engine (with the same runner) to assemble a real bundle on disk."""
    cfg = _config(tmp_path, **cfg_kw)  # type: ignore[arg-type]
    engine = BackupEngine(cfg, runner=runner, now=_NOW)
    return engine.run_full_backup().bundle_dir


# --------------------------------------------------------------------------- #
# Good backup → VALID.
# --------------------------------------------------------------------------- #


def test_good_backup_verifies_ok(tmp_path: Path) -> None:
    runner = FakeRunner()
    bundle = _build_bundle(tmp_path, runner)

    report = BackupVerifier(runner=runner).verify_bundle(bundle)

    assert isinstance(report, VerificationReport)
    assert report.valid is True
    assert report.failures == ()
    # A checksum check per artifact (1 dump + 2 volumes) + the structural probes.
    checks_by_kind = {(c.check, c.artifact) for c in report.checks}
    assert (CHECK_PG_RESTORE_LIST, "postgres") in checks_by_kind
    assert (CHECK_TAR_LIST, "minio_data.tar.gz") in checks_by_kind
    assert (CHECK_TAR_LIST, "redis_data.tar.gz") in checks_by_kind
    assert all(c.ok for c in report.checks)


def test_verification_invokes_pg_restore_list_and_tar_list(tmp_path: Path) -> None:
    runner = FakeRunner()
    bundle = _build_bundle(tmp_path, runner)
    runner.calls.clear()  # focus on the verification commands only

    BackupVerifier(runner=runner).verify_bundle(bundle)

    pg_calls = [c for c in runner.calls if c[0] == "pg_restore"]
    assert len(pg_calls) == 1
    assert "--list" in pg_calls[0]
    # The dump directory is the verification target.
    assert str(bundle / "postgres") in pg_calls[0]

    tar_list_calls = [c for c in runner.calls if c[0] == "tar" and "--list" in c]
    assert len(tar_list_calls) == 2
    for call in tar_list_calls:
        archive = _arg_value(call, "--file=")
        assert archive.endswith(".tar.gz")


# --------------------------------------------------------------------------- #
# Each failing probe → INVALID.
# --------------------------------------------------------------------------- #


def test_pg_restore_list_error_marks_backup_invalid(tmp_path: Path) -> None:
    runner = FakeRunner()
    bundle = _build_bundle(tmp_path, runner)
    runner.fail_pg_restore = True

    report = BackupVerifier(runner=runner).verify_bundle(bundle)

    assert report.valid is False
    failing = [c for c in report.failures if c.check == CHECK_PG_RESTORE_LIST]
    assert len(failing) == 1
    assert "pg_restore --list failed" in failing[0].detail


def test_empty_toc_marks_backup_invalid(tmp_path: Path) -> None:
    runner = FakeRunner()
    bundle = _build_bundle(tmp_path, runner)
    runner.empty_toc = True

    report = BackupVerifier(runner=runner).verify_bundle(bundle)

    assert report.valid is False
    failing = [c for c in report.failures if c.check == CHECK_PG_RESTORE_LIST]
    assert len(failing) == 1
    assert "empty table of contents" in failing[0].detail


def test_tar_list_error_marks_backup_invalid(tmp_path: Path) -> None:
    runner = FakeRunner()
    bundle = _build_bundle(tmp_path, runner)
    runner.fail_tar_list = True

    report = BackupVerifier(runner=runner).verify_bundle(bundle)

    assert report.valid is False
    failing = [c for c in report.failures if c.check == CHECK_TAR_LIST]
    # Both volume tars fail to list.
    assert len(failing) == 2
    assert all("tar --list failed" in c.detail for c in failing)


def test_checksum_mismatch_marks_backup_invalid(tmp_path: Path) -> None:
    runner = FakeRunner()
    bundle = _build_bundle(tmp_path, runner)

    # Corrupt one volume archive AFTER the manifest captured its checksum.
    tampered = bundle / "minio_data.tar.gz"
    tampered.write_bytes(b"this is not the bytes the manifest recorded")

    report = BackupVerifier(runner=runner).verify_bundle(bundle)

    assert report.valid is False
    failing = [
        c
        for c in report.failures
        if c.check == CHECK_CHECKSUM and c.artifact == "minio_data.tar.gz"
    ]
    assert len(failing) == 1
    assert "sha256 mismatch" in failing[0].detail


def test_missing_artifact_marks_backup_invalid(tmp_path: Path) -> None:
    runner = FakeRunner()
    bundle = _build_bundle(tmp_path, runner)

    (bundle / "redis_data.tar.gz").unlink()

    report = BackupVerifier(runner=runner).verify_bundle(bundle)

    assert report.valid is False
    failing = [c for c in report.failures if c.artifact == "redis_data.tar.gz"]
    assert len(failing) == 1
    assert "missing on disk" in failing[0].detail


# --------------------------------------------------------------------------- #
# Operational failures of the verifier itself raise (not a "valid: false").
# --------------------------------------------------------------------------- #


def test_missing_manifest_raises(tmp_path: Path) -> None:
    empty = tmp_path / "no-manifest"
    empty.mkdir()
    with pytest.raises(BackupVerificationError, match="no manifest.json"):
        BackupVerifier(runner=FakeRunner()).verify_bundle(empty)


def test_garbled_manifest_raises(tmp_path: Path) -> None:
    bundle = tmp_path / "garbled"
    bundle.mkdir()
    (bundle / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(BackupVerificationError, match="could not read manifest"):
        BackupVerifier(runner=FakeRunner()).verify_bundle(bundle)


# --------------------------------------------------------------------------- #
# Encrypted bundle: checksum + decrypt probe (no pg_restore/tar on the blob).
# --------------------------------------------------------------------------- #


def test_encrypted_bundle_verifies_with_decrypt_check(tmp_path: Path) -> None:
    runner = _EncFakeRunner()
    cfg = _config(tmp_path, encryption_enabled=True)
    engine = BackupEngine(cfg, runner=runner, encryptor=_encryptor(), now=_NOW)
    bundle = engine.run_full_backup().bundle_dir

    report = verify_bundle(bundle, runner=runner, encryptor=_encryptor())

    assert report.valid is True
    # An encrypted bundle is one blob: checksum + decrypt, no pg_restore/tar.
    checks = {(c.check, c.artifact) for c in report.checks}
    assert (CHECK_CHECKSUM, "bundle.tar.enc") in checks
    assert (CHECK_DECRYPT, "bundle.tar.enc") in checks
    assert not any(c.check in {CHECK_PG_RESTORE_LIST, CHECK_TAR_LIST} for c in report.checks)


def test_tampered_encrypted_bundle_fails_decrypt_check(tmp_path: Path) -> None:
    runner = _EncFakeRunner()
    cfg = _config(tmp_path, encryption_enabled=True)
    engine = BackupEngine(cfg, runner=runner, encryptor=_encryptor(), now=_NOW)
    bundle = engine.run_full_backup().bundle_dir

    # Flip a byte deep in the encrypted blob (past header + nonce) so the GCM
    # tag fails but the checksum is recomputed from the same mutated bytes —
    # the decrypt check is what catches it. To isolate the decrypt failure we
    # recompute & rewrite the manifest checksum to match the mutated blob.
    blob = bundle / "bundle.tar.enc"
    data = bytearray(blob.read_bytes())
    data[-1] ^= 0x01
    blob.write_bytes(bytes(data))
    _rewrite_manifest_checksum(bundle, "bundle.tar.enc")

    report = verify_bundle(bundle, runner=runner, encryptor=_encryptor())

    assert report.valid is False
    failing = [c for c in report.failures if c.check == CHECK_DECRYPT]
    assert len(failing) == 1
    assert "did not decrypt" in failing[0].detail


# --------------------------------------------------------------------------- #
# The CheckResult/report shapes are the typed contract Phase D keys on.
# --------------------------------------------------------------------------- #


def test_report_is_typed_and_serialisable(tmp_path: Path) -> None:
    runner = FakeRunner()
    bundle = _build_bundle(tmp_path, runner)

    report = verify_bundle(bundle, runner=runner)

    assert isinstance(report, VerificationReport)
    assert all(isinstance(c, CheckResult) for c in report.checks)
    d = report.to_dict()
    assert d["valid"] is True
    assert d["encrypted"] is False
    assert isinstance(d["checks"], list) and d["checks"]
    assert {"check", "artifact", "ok", "detail"} <= set(d["checks"][0])


@dataclass
class _EncFakeRunner:
    """Runner for the encrypted-bundle path: fabricates the dump + volume tars
    AND the bundle-collapse tar (folds member bytes in) so the engine's
    encryptor wraps real, decryptable content."""

    calls: list[list[str]] = field(default_factory=list)

    def run(
        self,
        args: Sequence[str],
        *,
        env: dict[str, str] | None = None,
        timeout: int | None = None,
    ) -> CommandResult:
        argv = list(args)
        self.calls.append(argv)
        if argv[0] == "pg_dump":
            out_dir = Path(_arg_value(argv, "--file="))
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "toc.dat").write_bytes(b"fake-toc")
            return CommandResult(returncode=0)
        if argv[0] == "tar":
            archive = Path(_arg_value(argv, "--file="))
            directory = Path(_arg_value(argv, "--directory="))
            members = [t for t in argv[2:] if not t.startswith("--")]
            if members and members != ["."]:
                out = bytearray(b"FAKETAR\0")
                for name in members:
                    target = directory / name
                    if target.is_dir():
                        blob = b"".join(
                            p.read_bytes() for p in sorted(target.rglob("*")) if p.is_file()
                        )
                    else:
                        blob = target.read_bytes()
                    out += name.encode() + b"\0" + str(len(blob)).encode() + b"\0" + blob
                archive.write_bytes(bytes(out))
            else:
                archive.write_bytes(b"fake-volume-tar-" + directory.parent.name.encode())
            return CommandResult(returncode=0)
        raise AssertionError(f"unexpected command: {argv!r}")


def _rewrite_manifest_checksum(bundle: Path, artifact_name: str) -> None:
    """Recompute + persist the manifest sha256 for one artifact (test helper)."""
    import hashlib
    import json

    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256((bundle / artifact_name).read_bytes()).hexdigest()
    for art in manifest["artifacts"]:
        if art["name"] == artifact_name:
            art["sha256"] = digest
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
