"""Integration tests for optional at-rest backup encryption (Plan 12 task_12_02).

The full-backup engine's external commands (pg_dump / tar) are MOCKED via the
:class:`workers.backup.CommandRunner` seam — no real backup of the live stack
runs here. The encryption layer itself uses the REAL ``cryptography`` AES-256-GCM
primitive (a round-trip on real bytes is the whole point), keyed by a Vault
secret resolved through the workers' :class:`workers.secrets.StaticSecretsProvider`.

The tests assert:

  * ENABLED — when ``backup_encryption_enabled`` is set, the bundle is collapsed
    into a single ``bundle.tar.enc`` blob, the plaintext artifacts are gone, the
    manifest records ``encrypted: true``, and the blob does NOT start with the
    plaintext tar bytes.
  * ROUND-TRIP — encrypt(plaintext) → decrypt(ciphertext) == original, with the
    Vault key.
  * TAMPER — flipping one ciphertext byte makes decryption raise
    :class:`BackupEncryptionError` (GCM authentication).
  * DISABLED — with the flag off, behaviour is unchanged: plaintext DB dump +
    volume tars, ``encrypted: false``, and NO ``.enc`` blob.
  * NO KEY LEAK — the Vault key value never appears in any file on disk under the
    backup root, nor in the structlog output.
"""

from __future__ import annotations

import json
import logging
import os
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
from workers.backup_encryption import (
    BackupEncryptionError,
    BackupEncryptor,
    EnvSecretsProvider,
)
from workers.secrets import StaticSecretsProvider

pytestmark = pytest.mark.integration

# The secret value that stands in for the Vault-resolved AES-256 key material.
# A long, distinctive string so a leak test can grep for it.
_VAULT_KEY_NAME = "backup_encryption_key"
_VAULT_KEY_VALUE = "s3cr3t-vault-backup-key-MUST-NOT-LEAK-0123456789abcdef"

_NOW = datetime(2026, 5, 30, 3, 0, 0, tzinfo=UTC)


@dataclass
class FakeRunner:
    """Records argv + fabricates the artifacts the real command would write.

    Handles pg_dump (directory dump), the per-volume tar, AND the bundle-collapse
    tar the encryption step runs: that last one reads the existing member files
    and concatenates their bytes into the archive so the round-trip test operates
    on real, recoverable content.
    """

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
            (out_dir / "3434.dat.gz").write_bytes(b"fake-table-data")
        elif argv[0] == "tar":
            archive = Path(_arg_value(argv, "--file="))
            directory = Path(_arg_value(argv, "--directory="))
            members = [t for t in argv[3:] if not t.startswith("--")]
            if members and members != ["."]:
                # Bundle-collapse tar: fold the named members' bytes into the
                # archive with a tiny header per member, so a later "untar"
                # (the test does it manually) can recover them.
                archive.write_bytes(_fake_tar(directory, members))
            else:
                # Per-volume tar (members == ["."]). ``directory`` is
                # ``<root>/<volume>/_data`` — embed the VOLUME name (its parent)
                # so a round-trip test can recognise the captured volume.
                volume = directory.parent.name
                archive.write_bytes(b"fake-volume-tar-gz-bytes-" + volume.encode())
        return CommandResult(returncode=0)


def _fake_tar(directory: Path, members: list[str]) -> bytes:
    """A trivial deterministic 'tar': ``name\\0len\\0bytes`` per member."""
    out = bytearray(b"FAKETAR\0")
    for name in members:
        target = directory / name
        if target.is_dir():
            blob = b"".join(p.read_bytes() for p in sorted(target.rglob("*")) if p.is_file())
        else:
            blob = target.read_bytes()
        out += name.encode() + b"\0" + str(len(blob)).encode() + b"\0" + blob
    return bytes(out)


def _arg_value(argv: list[str], prefix: str) -> str:
    for token in argv:
        if token.startswith(prefix):
            return token[len(prefix) :]
    raise AssertionError(f"no arg with prefix {prefix!r} in {argv!r}")


def _config(tmp_path: Path, *, encryption_enabled: bool) -> BackupConfig:
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
    provider = StaticSecretsProvider(values={_VAULT_KEY_NAME: _VAULT_KEY_VALUE})
    return BackupEncryptor(provider=provider, vault_key_name=_VAULT_KEY_NAME)


# --------------------------------------------------------------------------- #
# Pure crypto round-trip + tamper (the BackupEncryptor in isolation).
# --------------------------------------------------------------------------- #


def test_encrypt_decrypt_round_trips_with_vault_key() -> None:
    enc = _encryptor()
    plaintext = b"the quick brown fox" * 1000

    blob = enc.encrypt_bytes(plaintext)

    # The ciphertext is not the plaintext, and round-trips back exactly.
    assert plaintext not in blob
    assert enc.decrypt_bytes(blob) == plaintext


def test_tampered_ciphertext_fails_decryption() -> None:
    enc = _encryptor()
    blob = bytearray(enc.encrypt_bytes(b"sensitive backup contents"))

    # Flip one bit deep in the ciphertext body (past the header + nonce).
    blob[-1] ^= 0x01

    with pytest.raises(BackupEncryptionError):
        enc.decrypt_bytes(bytes(blob))


def test_wrong_key_fails_decryption() -> None:
    blob = _encryptor().encrypt_bytes(b"payload")
    other = BackupEncryptor(
        provider=StaticSecretsProvider(values={_VAULT_KEY_NAME: "a-totally-different-key"}),
        vault_key_name=_VAULT_KEY_NAME,
    )
    with pytest.raises(BackupEncryptionError):
        other.decrypt_bytes(blob)


def test_truncated_blob_fails_decryption() -> None:
    with pytest.raises(BackupEncryptionError):
        _encryptor().decrypt_bytes(b"too-short")


def test_empty_vault_key_is_rejected() -> None:
    enc = BackupEncryptor(
        provider=StaticSecretsProvider(values={_VAULT_KEY_NAME: ""}),
        vault_key_name=_VAULT_KEY_NAME,
    )
    with pytest.raises(BackupEncryptionError):
        enc.encrypt_bytes(b"x")


def test_missing_vault_key_is_rejected() -> None:
    enc = BackupEncryptor(
        provider=StaticSecretsProvider(values={}),
        vault_key_name=_VAULT_KEY_NAME,
    )
    with pytest.raises(BackupEncryptionError):
        enc.encrypt_bytes(b"x")


# --------------------------------------------------------------------------- #
# The engine, end to end (mocked pg_dump/tar) — enabled vs disabled.
# --------------------------------------------------------------------------- #


def test_enabled_backup_produces_encrypted_blob_and_no_plaintext(tmp_path: Path) -> None:
    runner = FakeRunner()
    cfg = _config(tmp_path, encryption_enabled=True)
    engine = BackupEngine(cfg, runner=runner, encryptor=_encryptor(), now=_NOW)

    result = engine.run_full_backup()

    # Only the encrypted blob + the manifest live in the bundle.
    names = sorted(p.name for p in result.bundle_dir.iterdir())
    assert names == ["bundle.tar.enc", "manifest.json"]
    # The plaintext DB dump dir + the volume tars are gone.
    assert not (result.bundle_dir / "postgres").exists()
    assert not (result.bundle_dir / "minio_data.tar.gz").exists()

    manifest = json.loads((result.bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["encrypted"] is True
    kinds = [a["kind"] for a in manifest["artifacts"]]
    assert kinds == ["encrypted_bundle"]
    blob_art = manifest["artifacts"][0]
    assert blob_art["path"] == "bundle.tar.enc"
    assert len(blob_art["sha256"]) == 64
    assert blob_art["size_bytes"] > 0


def test_enabled_backup_blob_round_trips_to_original_bundle(tmp_path: Path) -> None:
    runner = FakeRunner()
    cfg = _config(tmp_path, encryption_enabled=True)
    enc = _encryptor()
    engine = BackupEngine(cfg, runner=runner, encryptor=enc, now=_NOW)

    result = engine.run_full_backup()

    blob = (result.bundle_dir / "bundle.tar.enc").read_bytes()
    # Decrypt with the Vault key → the original collapsed bundle tar.
    recovered_tar = enc.decrypt_bytes(blob)
    # Our fake tar embeds the DB dump + volume bytes verbatim — they survive.
    assert b"fake-toc" in recovered_tar
    assert b"fake-table-data" in recovered_tar
    assert b"fake-volume-tar-gz-bytes-minio_data" in recovered_tar
    # The blob on disk is NOT the plaintext tar.
    assert recovered_tar not in blob


def test_disabled_backup_is_plaintext_bundle_unchanged(tmp_path: Path) -> None:
    runner = FakeRunner()
    cfg = _config(tmp_path, encryption_enabled=False)
    # No encryptor needed when disabled.
    engine = BackupEngine(cfg, runner=runner, now=_NOW)

    result = engine.run_full_backup()

    # Plaintext artifacts present; no encrypted blob.
    assert (result.bundle_dir / "postgres").is_dir()
    assert (result.bundle_dir / "minio_data.tar.gz").exists()
    assert (result.bundle_dir / "redis_data.tar.gz").exists()
    assert not (result.bundle_dir / "bundle.tar.enc").exists()

    manifest = json.loads((result.bundle_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["encrypted"] is False
    kinds = [a["kind"] for a in manifest["artifacts"]]
    assert kinds == ["pg_dump", "volume_tar", "volume_tar"]
    # No bundle-collapse tar ran (only pg_dump + 2 per-volume tars).
    verbs = [c[0] for c in runner.calls]
    assert verbs == ["pg_dump", "tar", "tar"]


def test_enabled_without_encryptor_fails_cleanly(tmp_path: Path) -> None:
    runner = FakeRunner()
    cfg = _config(tmp_path, encryption_enabled=True)
    # encryption_enabled but NO encryptor injected → clean failure, no bundle.
    engine = BackupEngine(cfg, runner=runner, encryptor=None, now=_NOW)

    from workers.backup import BackupError

    with pytest.raises(BackupError, match="no BackupEncryptor"):
        engine.run_full_backup()

    bundles = list(cfg.backup_root.iterdir()) if cfg.backup_root.exists() else []
    assert bundles == []


# --------------------------------------------------------------------------- #
# The key must never be written to disk or logged.
# --------------------------------------------------------------------------- #


def test_vault_key_never_written_to_disk(tmp_path: Path) -> None:
    runner = FakeRunner()
    cfg = _config(tmp_path, encryption_enabled=True)
    engine = BackupEngine(cfg, runner=runner, encryptor=_encryptor(), now=_NOW)

    result = engine.run_full_backup()

    # Scan EVERY file under the backup root for the raw key value.
    key_bytes = _VAULT_KEY_VALUE.encode("utf-8")
    for path in result.bundle_dir.rglob("*"):
        if path.is_file():
            assert key_bytes not in path.read_bytes(), f"key leaked into {path}"


def test_vault_key_never_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    runner = FakeRunner()
    cfg = _config(tmp_path, encryption_enabled=True)
    engine = BackupEngine(cfg, runner=runner, encryptor=_encryptor(), now=_NOW)

    with caplog.at_level(logging.DEBUG):
        engine.run_full_backup()

    # The key VALUE never appears in any captured log record; the key NAME may.
    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert _VAULT_KEY_VALUE not in blob


# --------------------------------------------------------------------------- #
# The default Vault/env-backed provider.
# --------------------------------------------------------------------------- #


def test_env_secrets_provider_reads_prefixed_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WORKERS_BACKUP_ENCRYPTION_KEY", _VAULT_KEY_VALUE)
    provider = EnvSecretsProvider()

    fetched = provider.fetch([_VAULT_KEY_NAME])

    assert fetched == {_VAULT_KEY_NAME: _VAULT_KEY_VALUE}


def test_env_secrets_provider_omits_unset_keys() -> None:
    os.environ.pop("WORKERS_BACKUP_ENCRYPTION_KEY", None)
    provider = EnvSecretsProvider()

    assert provider.fetch([_VAULT_KEY_NAME]) == {}
