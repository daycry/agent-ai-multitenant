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

A KEY RING, and why the format had to change (prod-05 task_prod05_08, gap2-5)
----------------------------------------------------------------------------
Version 1 of this format carried NO key id and the encryptor held exactly ONE
key. Rotating ``WORKERS_BACKUP_ENCRYPTION_KEY`` therefore made **every bundle
already written unreadable** — and unlike the database ciphertext, a backup is
precisely the thing you reach for when everything else is gone. That is not a
rotation hazard, it is a disaster-recovery hazard: the audit rated it a
definitive data loss in a DR (gap2-5).

Two changes fix it:

* the encryptor holds an ORDERED RING (``WORKERS_BACKUP_ENCRYPTION_KEYS``,
  comma-separated, falling back to the singular var) — the head key encrypts,
  every key can decrypt;
* the header gains a **key id** in format version 2, so a reader does not have to
  brute-force the ring and — crucially — can say *"I do not have key ab12cd34"*
  instead of *"InvalidTag"*. With v1 blobs, "the key is wrong" and "the archive
  is corrupt" were the same error message, which is a terrible thing to debug at
  04:00 during a restore.

The corresponding OPERATIONAL rule cannot be enforced in code and belongs in the
runbook: **keep every retired backup key alongside the bundles it encrypted.**
A key that was deleted is a bundle that is gone; this module protects forward,
it cannot resurrect a key nobody kept.

On-disk shape of an encrypted blob
----------------------------------
Version 2 (current)::

    [ MAGIC(8) | version=2 (1) | key_id(8) | nonce(12) | ciphertext+tag ]

Version 1 (still readable, forever)::

    [ MAGIC(8) | version=1 (1) | nonce(12) | ciphertext+tag ]

The nonce is a fresh 12 random bytes per blob (GCM's standard nonce size); the
GCM tag is appended to the ciphertext by ``cryptography``. The WHOLE header —
magic, version and, in v2, the key id — is passed to GCM as *associated data*, so
tampering with the key id is detected just like tampering with the ciphertext:
an attacker cannot redirect a blob at a different key in the ring.

The magic string keeps its trailing ``1`` (``AGENTBK1``): it is a magic NUMBER,
not a version, and every v1 blob on disk starts with it. The version byte is what
versions the format.
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
# can sanity-check a blob before attempting to decrypt it. The magic is a magic
# NUMBER (its trailing "1" is not a version) and must never change: every v1
# bundle ever written starts with these 8 bytes.
_MAGIC = b"AGENTBK1"  # 8 bytes

#: v1: no key id. Read forever, never written again.
_FORMAT_VERSION_V1 = 1
#: v2 (prod-05 task_prod05_08): key id in the header. What we write today.
_FORMAT_VERSION = 2

# Length of the key id stamped into a v2 header. 8 bytes of a SHA-256 of the
# DERIVED key: enough that two keys in one ring colliding is not a practical
# concern, short enough that the header stays negligible. It is an IDENTIFIER,
# not a secret, and it reveals nothing the ciphertext does not.
_KEY_ID_LEN = 8

_HEADER_V1 = _MAGIC + bytes([_FORMAT_VERSION_V1])  # 9 bytes, also the v1 GCM AAD
_VERSION_OFFSET = len(_MAGIC)
_V1_HEADER_LEN = len(_HEADER_V1)
_V2_HEADER_LEN = _V1_HEADER_LEN + _KEY_ID_LEN

# AES-256 key length (bytes) and the GCM nonce length (bytes, the standard 96-bit
# GCM nonce). Not magic numbers — the AES-GCM spec fixes both.
_AES_256_KEY_LEN = 32
_GCM_NONCE_LEN = 12

# The well-known field inside the Vault KV entry that holds the backup key,
# mirroring how the SSO layer keys its Vault secrets by a single field name.
VAULT_BACKUP_KEY_FIELD = "backup_encryption_key"

# Separación de dominio de la huella de custodia (prod-04 task_prod_04_07): la
# huella nunca puede confundirse con —ni servir de oráculo para— otro uso del
# mismo hash. Cambiarla invalida los fingerprints ya registrados en custodia.
_FINGERPRINT_DOMAIN = b"agentic-platform/backup-key-fingerprint/v1\0"

#: Separator inside the plural key variable. Same convention as the api-server's
#: ``*_ENCRYPTION_KEYS`` rings.
KEY_RING_SEPARATOR = ","


# Env var prefix the env-backed provider reads the backup key from. The
# environment is how production injects a Vault-resolved secret into a worker
# process (the platform's standard Vault → env injection, never committed,
# never logged): WORKERS_BACKUP_ENCRYPTION_KEY=<vault secret>.
_ENV_KEY_PREFIX = "WORKERS_"


@dataclass(frozen=True)
class EnvSecretsProvider:
    """Default :class:`workers.secrets.SecretsProvider` for the backup key.

    Resolves each requested key from the process environment, upper-cased and
    prefixed (``backup_encryption_key`` → ``WORKERS_BACKUP_ENCRYPTION_KEY``,
    ``backup_encryption_keys`` → ``WORKERS_BACKUP_ENCRYPTION_KEYS``).
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


def key_id_for(derived_key: bytes) -> bytes:
    """The 8-byte header id of a DERIVED AES key.

    SHA-256 of the key material, truncated. Not reversible, not a secret: it
    names the key so a reader can select it, or report the one it lacks.
    """
    return hashlib.sha256(derived_key).digest()[:_KEY_ID_LEN]


def parse_key_ring(raw: str) -> tuple[str, ...]:
    """Split a comma-separated key list: stripped, blanks dropped, deduped.

    Mirrors ``api_server.auth.crypto_keys.parse_key_ring`` (the workers app does
    not import the api-server's auth package for one function). Duplicates are
    collapsed so the ring's length is an honest count of distinct keys.
    """
    ring: list[str] = []
    for entry in raw.split(KEY_RING_SEPARATOR):
        candidate = entry.strip()
        if candidate and candidate not in ring:
            ring.append(candidate)
    return tuple(ring)


class BackupEncryptor:
    """Encrypts / decrypts backup blobs with a RING of Vault-resolved AES keys.

    The ring is fetched lazily (once) through the injected
    :class:`workers.secrets.SecretsProvider` and cached in memory for the
    lifetime of this instance — never persisted, never logged.

    The HEAD key encrypts; ANY key can decrypt. That asymmetry is the whole
    feature: a bundle written months ago under a key that is now third in the
    ring still restores, which is the difference between a rotation and a
    permanent loss of every historical backup (gap2-5).
    """

    def __init__(
        self,
        *,
        provider: SecretsProvider,
        vault_key_name: str,
        vault_keys_name: str | None = None,
    ) -> None:
        self._provider = provider
        self._vault_key_name = vault_key_name
        # By convention the plural of the same name (``..._key`` → ``..._keys``),
        # so no call site has to pass it and the env var an operator sets is
        # predictable: WORKERS_BACKUP_ENCRYPTION_KEYS.
        self._vault_keys_name = vault_keys_name or f"{vault_key_name}s"
        self._ring: tuple[bytes, ...] | None = None

    # -- key resolution -----------------------------------------------------

    def _fetch_optional(self, name: str) -> str | None:
        """One provider lookup that tolerates "not configured".

        Providers disagree on how they report an absent key:
        :class:`EnvSecretsProvider` omits it, ``StaticSecretsProvider`` raises
        ``KeyError``. Normalising here is what lets the plural variable be
        genuinely optional instead of a breaking change for every existing caller.
        """
        try:
            fetched = self._provider.fetch([name])
        except KeyError:
            return None
        value = fetched.get(name)
        return value if value else None

    def _resolve_ring(self) -> tuple[bytes, ...]:
        """Resolve + derive the ordered AES key ring, cached after first use.

        Precedence mirrors the api-server rings: the plural list WINS, and the
        singular value is then ignored — so retiring a key is "delete it from the
        list" and nothing else.
        """
        if self._ring is not None:
            return self._ring

        raw_ring: tuple[str, ...]
        plural = self._fetch_optional(self._vault_keys_name)
        if plural is not None and plural.strip():
            raw_ring = parse_key_ring(plural)
            source = self._vault_keys_name
        else:
            singular = self._fetch_optional(self._vault_key_name)
            if singular is None:
                raise BackupEncryptionError(
                    f"Vault provider returned no value for backup key "
                    f"{self._vault_key_name!r} (nor {self._vault_keys_name!r})"
                )
            raw_ring = (singular,)
            source = self._vault_key_name

        if not raw_ring:
            raise BackupEncryptionError(
                f"the backup key list {self._vault_keys_name!r} has no non-empty entry"
            )
        ring = tuple(_derive_key(raw) for raw in raw_ring)
        # Log the KEY NAME + the ring SIZE + the key IDS, never a value. The ids
        # are what an operator matches against a bundle header when a restore
        # complains about a key it does not have.
        _log.debug(
            "backup.encryption.key_ring_resolved",
            vault_key_name=source,
            ring_size=len(ring),
            key_ids=[key_id_for(key).hex() for key in ring],
        )
        self._ring = ring
        return ring

    @property
    def key_ids(self) -> tuple[str, ...]:
        """Hex key ids of the ring, head first. Safe to log and to show an operator."""
        return tuple(key_id_for(key).hex() for key in self._resolve_ring())

    # -- public API ---------------------------------------------------------

    def key_fingerprint(self) -> str:
        """Huella SHA-256 de la clave ACTIVA (prod-04 task_prod_04_07).

        Sirve para una sola cosa, y es importante: comprobar que la clave con la
        que se está cifrando el bundle es la MISMA que alguien depositó en
        custodia offsite. El control técnico no puede probar que el sobre sellado
        contiene la clave — solo puede probar que la clave activa es la declarada.

        Se calcula sobre la clave DERIVADA (que ya es un SHA-256 del secreto) y
        con separación de dominio, para que la huella no sea reutilizable como
        oráculo en ningún otro contexto y no revele el secreto. Es seguro
        escribirla en el manifest y en un registro de custodia; NO lo es la clave.

        Con el anillo de claves (prod-05) la huella es la de la clave **HEAD**,
        que es la que cifra: es la única que, si se pierde, deja sin abrir los
        bundles que se están produciendo. Las claves históricas del anillo solo
        descifran, y su custodia se controla por separado.
        """
        head = self._resolve_ring()[0]
        digest = hashlib.sha256(_FINGERPRINT_DOMAIN + head).hexdigest()
        return digest

    def encrypt_bytes(self, plaintext: bytes) -> bytes:
        """Return ``header | nonce | ciphertext+tag`` for ``plaintext`` (format v2).

        The header carries the key id of the HEAD key, so a future restore can
        select the right key out of a ring instead of trying all of them.
        """
        key = self._resolve_ring()[0]
        header = _MAGIC + bytes([_FORMAT_VERSION]) + key_id_for(key)
        nonce = os.urandom(_GCM_NONCE_LEN)
        ciphertext: bytes = AESGCM(key).encrypt(nonce, plaintext, header)
        return header + nonce + ciphertext

    def decrypt_bytes(self, blob: bytes) -> bytes:
        """Reverse :meth:`encrypt_bytes`, for a v2 OR a legacy v1 blob.

        v2: the key id selects exactly one key from the ring. A key id the ring
        does not contain is reported as such — naming the id and the ids we DO
        have — because "wrong key" and "corrupt archive" must not share an error
        message during a restore.

        v1: no key id exists, so every key in the ring is tried. Legacy blobs stay
        readable for as long as their key is in the ring.

        Raises :class:`BackupEncryptionError` on a tampered/truncated blob, a
        missing key, or an unrecognised header.
        """
        if len(blob) < _V1_HEADER_LEN + _GCM_NONCE_LEN:
            raise BackupEncryptionError("encrypted blob is too short / truncated")
        if blob[: len(_MAGIC)] != _MAGIC:
            raise BackupEncryptionError("not an agent-platform encrypted backup blob")

        version = blob[_VERSION_OFFSET]
        ring = self._resolve_ring()

        if version == _FORMAT_VERSION_V1:
            header = blob[:_V1_HEADER_LEN]
            nonce = blob[_V1_HEADER_LEN : _V1_HEADER_LEN + _GCM_NONCE_LEN]
            ciphertext = blob[_V1_HEADER_LEN + _GCM_NONCE_LEN :]
            for key in ring:
                try:
                    return bytes(AESGCM(key).decrypt(nonce, ciphertext, header))
                except InvalidTag:
                    continue
            raise BackupEncryptionError(
                "failed to decrypt a version-1 backup blob: no key in the ring "
                f"({len(ring)} configured) decrypts it. Version-1 blobs carry no "
                "key id, so the key that wrote this bundle must be present in "
                "WORKERS_BACKUP_ENCRYPTION_KEYS — or the blob is tampered."
            )

        if version != _FORMAT_VERSION:
            raise BackupEncryptionError(
                f"unsupported backup blob format version {version} (this build "
                f"reads 1 and {_FORMAT_VERSION}). The bundle was written by a "
                "NEWER platform version; restore it with that version."
            )

        if len(blob) < _V2_HEADER_LEN + _GCM_NONCE_LEN:
            raise BackupEncryptionError("encrypted blob is too short / truncated")
        header = blob[:_V2_HEADER_LEN]
        wanted = header[_V1_HEADER_LEN:_V2_HEADER_LEN]
        nonce = blob[_V2_HEADER_LEN : _V2_HEADER_LEN + _GCM_NONCE_LEN]
        ciphertext = blob[_V2_HEADER_LEN + _GCM_NONCE_LEN :]

        selected = next((candidate for candidate in ring if key_id_for(candidate) == wanted), None)
        if selected is None:
            raise BackupEncryptionError(
                f"this bundle was encrypted with backup key id {wanted.hex()}, "
                f"which is not in the configured ring ({', '.join(self.key_ids)}). "
                "Add that key back to WORKERS_BACKUP_ENCRYPTION_KEYS — a retired "
                "backup key must be kept for as long as the bundles it encrypted."
            )
        try:
            return bytes(AESGCM(selected).decrypt(nonce, ciphertext, header))
        except InvalidTag as exc:
            # The key id matched, so this is NOT a wrong-key situation: the blob
            # (or its header) has been altered.
            raise BackupEncryptionError(
                "failed to decrypt backup blob: the key id matches the ring but "
                "the authentication tag does not — the bundle is tampered or "
                "truncated."
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
    "KEY_RING_SEPARATOR",
    "VAULT_BACKUP_KEY_FIELD",
    "BackupEncryptionError",
    "BackupEncryptor",
    "EnvSecretsProvider",
    "key_id_for",
    "parse_key_ring",
]
