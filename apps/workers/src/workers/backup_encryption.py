"""Optional AES-256 at-rest encryption of a backup bundle (Plan 12 task_12_02).

Plan 12 Decisiones Clave: *"Cifrado opcional en reposo del backup (AES-256 con
clave del Vault)."* This module is the OPTIONAL layer that plugs in **after**
:mod:`workers.backup` has assembled a bundle. When encryption is enabled it
wraps the bundle into a single encrypted blob and records that in the manifest;
when disabled, behaviour is unchanged (the plaintext bundle is left as-is).

Crypto primitive
----------------
AES-256-GCM (:class:`cryptography.hazmat.primitives.ciphers.aead.AESGCM`) — a
real, pip-clean, FIPS-grade AEAD already shipped via ``cryptography``. GCM is
*authenticated*: a single bit flipped in the ciphertext (or the nonce, or the
associated data) makes :meth:`AESGCM.decrypt` raise :class:`InvalidTag`, so a
tampered backup fails loudly at restore time instead of silently producing
garbage. AES-256 = a 32-byte key; we enforce exactly that.

The Vault key — never plaintext, never logged
----------------------------------------------
The encryption key is resolved through the workers' existing secret seam
(:class:`workers.secrets.SecretsProvider` — ``fetch(keys) -> {name: value}``),
the same Protocol the agent-runtime credential injector uses. Production wires a
Vault-backed provider there; tests inject :class:`workers.secrets.StaticSecretsProvider`.
The raw Vault secret string is folded to a 32-byte AES-256 key via SHA-256
(mirroring the SSO / notification Fernet-key-derivation precedent), so any
non-empty Vault value is a usable key. The derived key lives only in memory for
the duration of one encrypt/decrypt call; it is NEVER written to disk and NEVER
logged (we log the Vault *key name*, not the value).

On-disk shape of an encrypted blob
----------------------------------
``[ MAGIC(8) | version(1) | nonce(12) | ciphertext+tag ]`` — a tiny self-
describing header so the restore side (Plan 12 Phase C) can recognise + decrypt
without out-of-band metadata. The nonce is a fresh 12 random bytes per blob
(GCM's standard nonce size); the GCM tag is appended to the ciphertext by
``cryptography``. The magic + version are passed to GCM as *associated data* so
tampering with the header is also detected.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import structlog
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from workers.secrets import SecretsProvider

_log = structlog.get_logger("workers.backup_encryption")

# File extension appended to an encrypted bundle blob.
ENCRYPTED_SUFFIX = ".enc"

# Self-describing header: magic + a 1-byte format version, so the restore side
# can sanity-check a blob before attempting to decrypt it.
_MAGIC = b"AGENTBK1"  # 8 bytes
_FORMAT_VERSION = 1
_HEADER = _MAGIC + bytes([_FORMAT_VERSION])  # 9 bytes, also the GCM AAD

# AES-256 key length (bytes) and the GCM nonce length (bytes, the standard 96-bit
# GCM nonce). Not magic numbers — the AES-GCM spec fixes both.
_AES_256_KEY_LEN = 32
_GCM_NONCE_LEN = 12

# The well-known field inside the Vault KV entry that holds the backup key,
# mirroring how the SSO layer keys its Vault secrets by a single field name.
VAULT_BACKUP_KEY_FIELD = "backup_encryption_key"


# Env var prefix the env-backed provider reads the backup key from. The
# environment is how production injects a Vault-resolved secret into a worker
# process (the platform's standard Vault → env injection, never committed,
# never logged): WORKERS_BACKUP_ENCRYPTION_KEY=<vault secret>.
_ENV_KEY_PREFIX = "WORKERS_"


@dataclass(frozen=True)
class EnvSecretsProvider:
    """Default :class:`workers.secrets.SecretsProvider` for the backup key.

    Resolves each requested key from the process environment, upper-cased and
    prefixed (``backup_encryption_key`` → ``WORKERS_BACKUP_ENCRYPTION_KEY``).
    Production injects the Vault-resolved value into that env at deploy time —
    the secret is never in code, never in the DB, and is not logged here. A
    full Vault client (hvac) plugs in behind the same :class:`SecretsProvider`
    seam without touching the engine, mirroring how the rest of the codebase
    defers live Vault wiring (``workers.secrets``, ``api_server.auth.sso``).
    """

    def fetch(self, keys: Sequence[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for key in keys:
            env_name = _ENV_KEY_PREFIX + key.upper()
            value = os.environ.get(env_name)
            if value is not None:
                out[key] = value
        return out


class BackupEncryptionError(RuntimeError):
    """Raised when encryption or decryption of a backup blob fails.

    Decryption failures (tampered ciphertext, wrong key, truncated blob) all
    funnel here so the restore side gets one clear, non-leaky error type — the
    underlying cause is never echoed with key material.
    """


def _derive_key(raw_secret: str) -> bytes:
    """Fold a Vault secret string into a 32-byte AES-256 key.

    SHA-256 of the UTF-8 secret — exactly 32 bytes, so any non-empty Vault value
    is a valid AES-256 key (mirrors the SSO/notification Fernet derivation). The
    raw secret and the derived key are kept only in local scope and never logged.
    """
    if not raw_secret:
        raise BackupEncryptionError("the resolved Vault backup key is empty")
    key = hashlib.sha256(raw_secret.encode("utf-8")).digest()
    assert len(key) == _AES_256_KEY_LEN  # — invariant of sha256
    return key


class BackupEncryptor:
    """Encrypts / decrypts backup blobs with a Vault-resolved AES-256 key.

    The key is fetched lazily (once) from the injected
    :class:`workers.secrets.SecretsProvider` and cached in memory for the
    lifetime of this instance — never persisted, never logged.
    """

    def __init__(self, *, provider: SecretsProvider, vault_key_name: str) -> None:
        self._provider = provider
        self._vault_key_name = vault_key_name
        self._key: bytes | None = None

    def _resolve_key(self) -> bytes:
        """Resolve + derive the AES-256 key from Vault, cached after first use."""
        if self._key is None:
            try:
                fetched = self._provider.fetch([self._vault_key_name])
            except KeyError as exc:
                # Some providers (StaticSecretsProvider) raise on an absent key
                # rather than omitting it — normalise to our clean error type.
                raise BackupEncryptionError(
                    f"Vault provider has no backup key {self._vault_key_name!r}"
                ) from exc
            raw = fetched.get(self._vault_key_name)
            if raw is None:
                raise BackupEncryptionError(
                    f"Vault provider returned no value for backup key " f"{self._vault_key_name!r}"
                )
            # Log the KEY NAME, never the value.
            _log.debug("backup.encryption.key_resolved", vault_key_name=self._vault_key_name)
            self._key = _derive_key(raw)
        return self._key

    # -- public API ---------------------------------------------------------

    def encrypt_bytes(self, plaintext: bytes) -> bytes:
        """Return ``header | nonce | ciphertext+tag`` for ``plaintext``."""
        key = self._resolve_key()
        nonce = os.urandom(_GCM_NONCE_LEN)
        ciphertext: bytes = AESGCM(key).encrypt(nonce, plaintext, _HEADER)
        return _HEADER + nonce + ciphertext

    def decrypt_bytes(self, blob: bytes) -> bytes:
        """Reverse :meth:`encrypt_bytes`.

        Raises :class:`BackupEncryptionError` on a tampered/truncated blob, a
        wrong key, or an unrecognised header (GCM's :class:`InvalidTag`).
        """
        if len(blob) < len(_HEADER) + _GCM_NONCE_LEN:
            raise BackupEncryptionError("encrypted blob is too short / truncated")
        header = blob[: len(_HEADER)]
        if header[: len(_MAGIC)] != _MAGIC:
            raise BackupEncryptionError("not an agent-platform encrypted backup blob")
        nonce = blob[len(_HEADER) : len(_HEADER) + _GCM_NONCE_LEN]
        ciphertext = blob[len(_HEADER) + _GCM_NONCE_LEN :]
        key = self._resolve_key()
        try:
            plaintext: bytes = AESGCM(key).decrypt(nonce, ciphertext, header)
            return plaintext
        except InvalidTag as exc:
            # Tampered ciphertext, wrong key, or corrupted header — never echo
            # the cause with key material.
            raise BackupEncryptionError(
                "failed to decrypt backup blob (tampered, truncated, or wrong key)"
            ) from exc

    def encrypt_file(self, plaintext_path: Path, ciphertext_path: Path) -> int:
        """Encrypt a file on disk → ``ciphertext_path``. Returns blob size.

        Streams the whole plaintext into memory once (a backup blob is a tar
        already sized by the operator's retention policy); AES-GCM is a one-shot
        AEAD so chunked streaming would need a framed format we deliberately
        avoid for the first cut.
        """
        plaintext = plaintext_path.read_bytes()
        blob = self.encrypt_bytes(plaintext)
        ciphertext_path.write_bytes(blob)
        return len(blob)

    def decrypt_file(self, ciphertext_path: Path, plaintext_path: Path) -> int:
        """Decrypt a blob on disk → ``plaintext_path``. Returns plaintext size."""
        blob = ciphertext_path.read_bytes()
        plaintext = self.decrypt_bytes(blob)
        plaintext_path.write_bytes(plaintext)
        return len(plaintext)


__all__ = [
    "ENCRYPTED_SUFFIX",
    "VAULT_BACKUP_KEY_FIELD",
    "BackupEncryptionError",
    "BackupEncryptor",
    "EnvSecretsProvider",
]
