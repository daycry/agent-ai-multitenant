"""Backup blobs carry a key id and read from a ring (prod-05 task_prod05_08, gap2-5).

Of all the keys in this platform, the backup key is the one where a bad rotation
is unrecoverable: a backup is what you reach for when everything else is gone, so
"the old bundles no longer decrypt" is not an inconvenience, it is the DR failing
at the moment it is needed. Format v1 had no key id and the encryptor held exactly
one key, so rotating ``WORKERS_BACKUP_ENCRYPTION_KEY`` silently orphaned every
historical bundle.

The assertions below are ordered by how expensive the corresponding bug is:

1. **A v1 bundle written before this change still restores.** Nothing about the
   fix may cost us the bundles that already exist on disk. Their header is
   hand-built here from the AES-GCM spec, not from the module under test, so this
   test would still be meaningful if the module's constants were wrong.
2. **A bundle written under the retired key still restores** after the new key
   goes in at the head.
3. **A missing key says so.** With v1, "wrong key" and "corrupt archive" were the
   same ``InvalidTag``; at 04:00 in a restore that distinction is the difference
   between "fetch the old key from the safe" and "the backup is gone".
4. **The key id is authenticated.** It is in the GCM associated data, so an
   attacker cannot repoint a blob at a different key in the ring.
5. **New writes are v2** — otherwise the whole key-id story never actually ships.
"""

from __future__ import annotations

import hashlib
import os

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from workers.backup_encryption import (
    BackupEncryptionError,
    BackupEncryptor,
    key_id_for,
    parse_key_ring,
)
from workers.secrets import StaticSecretsProvider

pytestmark = pytest.mark.unit

_KEY_NAME = "backup_encryption_key"
_KEYS_NAME = "backup_encryption_keys"

_OLD = "the-backup-key-being-retired-2026-05"
_NEW = "the-backup-key-taking-over-2026-07"
_LOST = "a-backup-key-nobody-kept"

_MAGIC = b"AGENTBK1"


def _derive(raw: str) -> bytes:
    """The derivation, recomputed from first principles (SHA-256 of the UTF-8
    secret). Deliberately not imported: it is the compatibility contract with
    every bundle already on disk."""
    return hashlib.sha256(raw.encode("utf-8")).digest()


def _encryptor(*, single: str | None = None, ring: str | None = None) -> BackupEncryptor:
    values: dict[str, str] = {}
    if single is not None:
        values[_KEY_NAME] = single
    if ring is not None:
        values[_KEYS_NAME] = ring
    return BackupEncryptor(provider=StaticSecretsProvider(values=values), vault_key_name=_KEY_NAME)


def _legacy_v1_blob(raw_key: str, plaintext: bytes) -> bytes:
    """A format-v1 blob, built the way the pre-prod-05 code built them:
    ``MAGIC | 0x01 | nonce(12) | ct+tag`` with the 9-byte header as the AAD."""
    header = _MAGIC + bytes([1])
    nonce = os.urandom(12)
    ciphertext = AESGCM(_derive(raw_key)).encrypt(nonce, plaintext, header)
    return header + nonce + ciphertext


# ---------------------------------------------------------------------------
# 1. Legacy bundles must stay readable
# ---------------------------------------------------------------------------
def test_a_version_1_bundle_still_restores_after_the_rotation() -> None:
    """The bundles already sitting in MinIO were written with no key id. If this
    ever fails, deploying prod-05 is itself the data loss it set out to prevent."""
    legacy = _legacy_v1_blob(_OLD, b"a bundle from before the key id existed")
    rotated = _encryptor(ring=f"{_NEW},{_OLD}")
    assert rotated.decrypt_bytes(legacy) == b"a bundle from before the key id existed"


def test_a_version_1_bundle_reads_with_a_single_key_ring_too() -> None:
    """The un-rotated deployment: one key configured the old way, and the blob is
    byte-identical to what that deployment used to produce."""
    legacy = _legacy_v1_blob(_OLD, b"payload")
    assert _encryptor(single=_OLD).decrypt_bytes(legacy) == b"payload"


def test_a_version_1_bundle_whose_key_was_dropped_says_so_clearly() -> None:
    """No key id to name, so the message has to explain the SHAPE of the problem
    instead of surfacing an InvalidTag the operator cannot act on."""
    orphan = _legacy_v1_blob(_LOST, b"unrecoverable")
    with pytest.raises(BackupEncryptionError) as excinfo:
        _encryptor(ring=f"{_NEW},{_OLD}").decrypt_bytes(orphan)
    message = str(excinfo.value)
    assert "version-1" in message
    assert "WORKERS_BACKUP_ENCRYPTION_KEYS" in message


# ---------------------------------------------------------------------------
# 2. The rotation itself
# ---------------------------------------------------------------------------
def test_a_bundle_written_under_the_retired_key_still_restores() -> None:
    before = _encryptor(single=_OLD).encrypt_bytes(b"yesterday's bundle")
    after = _encryptor(ring=f"{_NEW},{_OLD}")
    assert after.decrypt_bytes(before) == b"yesterday's bundle"


def test_new_bundles_are_written_with_the_head_key() -> None:
    """Otherwise dropping the old key from the tail destroys everything written
    during the rotation window — and every roundtrip test would still pass."""
    encryptor = _encryptor(ring=f"{_NEW},{_OLD}")
    blob = encryptor.encrypt_bytes(b"today's bundle")

    assert blob[len(_MAGIC) + 1 : len(_MAGIC) + 9] == key_id_for(_derive(_NEW))
    # And a deployment that has ONLY the new key can read it: the old key is
    # genuinely no longer required for new bundles.
    assert _encryptor(single=_NEW).decrypt_bytes(blob) == b"today's bundle"


def test_dropping_the_key_ends_the_ability_to_read_its_bundles() -> None:
    """The other half of a real retirement: if the ring still read it, "retired"
    would be a lie."""
    blob = _encryptor(single=_OLD).encrypt_bytes(b"old bundle")
    with pytest.raises(BackupEncryptionError):
        _encryptor(single=_NEW).decrypt_bytes(blob)


# ---------------------------------------------------------------------------
# 3. A missing key is diagnosable
# ---------------------------------------------------------------------------
def test_a_missing_key_is_named_by_id_not_reported_as_a_corrupt_archive() -> None:
    blob = _encryptor(single=_LOST).encrypt_bytes(b"payload")
    ring = _encryptor(ring=f"{_NEW},{_OLD}")

    with pytest.raises(BackupEncryptionError) as excinfo:
        ring.decrypt_bytes(blob)
    message = str(excinfo.value)
    assert key_id_for(_derive(_LOST)).hex() in message, message
    # ...and the ids we DO have, so the operator can tell which key to fetch.
    for present in ring.key_ids:
        assert present in message
    assert "tamper" not in message.lower(), "a missing key must not read as corruption"


def test_a_tampered_v2_body_is_reported_as_tampering_not_as_a_missing_key() -> None:
    """The mirror image: the key id matches, so the ONLY explanation is that the
    bytes changed. Conflating the two would send an operator hunting for a key
    that is already in the ring."""
    encryptor = _encryptor(ring=_NEW)
    blob = bytearray(encryptor.encrypt_bytes(b"sensitive backup contents"))
    blob[-1] ^= 0x01
    with pytest.raises(BackupEncryptionError, match="tampered"):
        encryptor.decrypt_bytes(bytes(blob))


def test_a_future_format_version_is_refused_with_an_actionable_message() -> None:
    """A bundle from a newer build must not be mistaken for a corrupt one."""
    blob = bytearray(_encryptor(single=_NEW).encrypt_bytes(b"payload"))
    blob[len(_MAGIC)] = 99
    with pytest.raises(BackupEncryptionError, match="NEWER platform version"):
        _encryptor(single=_NEW).decrypt_bytes(bytes(blob))


# ---------------------------------------------------------------------------
# 4. The key id is authenticated, not advisory
# ---------------------------------------------------------------------------
def test_rewriting_the_key_id_in_the_header_is_detected() -> None:
    """The key id is in the GCM associated data. Swapping it for another ring
    member's id must fail: otherwise an attacker with write access to the bundle
    could steer the reader at a key of their choosing, and a mismatched
    header/ciphertext pair would surface as a confusing success-then-garbage."""
    encryptor = _encryptor(ring=f"{_NEW},{_OLD}")
    blob = bytearray(encryptor.encrypt_bytes(b"payload"))
    blob[len(_MAGIC) + 1 : len(_MAGIC) + 9] = key_id_for(_derive(_OLD))

    with pytest.raises(BackupEncryptionError):
        encryptor.decrypt_bytes(bytes(blob))


def test_the_magic_and_version_are_what_the_format_promises() -> None:
    """Pins the two constants every bundle on disk depends on. The magic keeps its
    trailing "1" — it is a magic NUMBER, and changing it would make every v1
    bundle unrecognisable — while the VERSION byte moved to 2."""
    blob = _encryptor(single=_NEW).encrypt_bytes(b"x")
    assert blob[: len(_MAGIC)] == b"AGENTBK1"
    assert blob[len(_MAGIC)] == 2, "new bundles must be written in format v2"
    # header(17) + nonce(12) + tag(16) + 1 byte of plaintext
    assert len(blob) == 17 + 12 + 16 + 1


# ---------------------------------------------------------------------------
# 5. Ring resolution
# ---------------------------------------------------------------------------
def test_the_plural_variable_wins_and_the_singular_one_is_ignored() -> None:
    """Retiring a key must be "delete it from the list" and nothing else — if the
    singular value were appended, the retired key would stay live."""
    encryptor = _encryptor(single=_LOST, ring=f"{_NEW},{_OLD}")
    assert encryptor.key_ids == (
        key_id_for(_derive(_NEW)).hex(),
        key_id_for(_derive(_OLD)).hex(),
    )
    with pytest.raises(BackupEncryptionError):
        encryptor.decrypt_bytes(_encryptor(single=_LOST).encrypt_bytes(b"x"))


def test_the_ring_parser_strips_blanks_and_collapses_duplicates() -> None:
    assert parse_key_ring(" a , b ,, a ,c ") == ("a", "b", "c")
    assert parse_key_ring("") == ()


def test_no_key_configured_at_all_is_an_error_not_an_empty_ring() -> None:
    with pytest.raises(BackupEncryptionError, match="no value"):
        _encryptor().encrypt_bytes(b"x")


def test_an_empty_key_list_is_an_error() -> None:
    with pytest.raises(BackupEncryptionError):
        _encryptor(ring=" , , ").encrypt_bytes(b"x")


def test_key_ids_are_stable_and_key_specific() -> None:
    """They go into a header that must remain matchable across restarts and
    across platform versions."""
    assert key_id_for(_derive(_NEW)) == key_id_for(_derive(_NEW))
    assert key_id_for(_derive(_NEW)) != key_id_for(_derive(_OLD))
    assert len(key_id_for(_derive(_NEW))) == 8
