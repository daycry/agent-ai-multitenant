"""Key RINGS for the platform's at-rest and signing keys (prod-05 task_prod05_01).

Every secret-at-rest family in this codebase used to be a SINGLE key derived by
SHA-256 from one env var (``API_SERVER_SSO_ENCRYPTION_KEY``,
``API_SERVER_NOTIFICATION_ENCRYPTION_KEY``,
``API_SERVER_INCOMING_WEBHOOK_ENCRYPTION_KEY``) and fed to a bare
:class:`cryptography.fernet.Fernet`. That shape makes rotation impossible in
practice: the instant the env var changes, every ciphertext already in the
database becomes an ``InvalidToken``. The audit (gap2-3/gap2-4) measured the
blast radius — rotating the SSO key alone invalidates every stored OIDC client
secret, every SAML SP key AND every TOTP seed, which with
``API_SERVER_ADMIN_REQUIRE_MFA=true`` locks the System Admins out of ``/admin``.

This module is the one place that turns "the operator's configuration" into a
**key ring**: an ORDERED tuple of raw configuration strings where

  * the FIRST key encrypts (so new writes land on the newest key), and
  * EVERY key decrypts (so ciphertext written under an older key still reads).

That is exactly :class:`cryptography.fernet.MultiFernet`, and it makes the
three-phase rotation of the runbook possible:

  1. add the new key at the HEAD of ``*_ENCRYPTION_KEYS`` and deploy — nothing
     re-encrypts yet, everything still decrypts;
  2. run ``python -m api_server.cli reencrypt-secrets`` (task_prod05_02), which
     re-encrypts every stored ciphertext onto the head key;
  3. drop the old key from the tail and deploy again.

Backwards compatibility is a hard requirement: the SINGULAR env var keeps
working as a one-element ring, so no existing deployment breaks and no operator
has to change anything to install this.

Two things this module deliberately does NOT do:

  * it never logs a key (not even truncated) — the fingerprint helper exists for
    key-IDs in file formats, not for logging configuration;
  * it never *derives* the ring from anything but explicit configuration. A
    "guess the previous key" path is how a rotation quietly becomes a
    data-destroying operation.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Sequence

from cryptography.fernet import Fernet, MultiFernet

#: Separator between keys inside a ``*_KEYS`` env var. A comma is the same
#: convention the rest of the platform's list-valued env vars use, and it means
#: a key may not itself contain a comma (nor leading/trailing whitespace, which
#: is stripped so operators can format the list readably).
KEY_RING_SEPARATOR = ","


class KeyRingError(ValueError):
    """Raised when a key-ring configuration resolves to zero usable keys.

    Fail-closed and LOUD at the first use: a silently empty ring would mean
    "encrypt with nothing", and the alternative (falling back to some default)
    is how a production deployment ends up with a publicly known key. The
    message names the setting, never a value.
    """


def parse_key_ring(
    *,
    plural: str | None,
    singular: str | None,
    name: str,
) -> tuple[str, ...]:
    """Resolve an ordered key ring from the plural + singular settings.

    ``plural`` is the comma-separated ``*_KEYS`` value (may be ``None`` or
    blank); ``singular`` is the legacy one-key ``*_KEY`` value. Precedence:

      * ``plural`` non-blank → its entries, in order, become the ring, and the
        singular value is IGNORED. This is deliberate: during a rotation the
        operator edits one variable, and having the singular silently appended
        would resurrect a key they just retired.
      * otherwise → ``(singular,)``, i.e. the pre-rotation behaviour verbatim.

    Entries are stripped, blanks dropped, and duplicates removed while keeping
    first-seen order (a duplicated key is harmless but would make the audit of
    "which keys are live" lie).

    Raises:
        KeyRingError: the resolution produced no usable key.
    """
    raw_entries: list[str]
    if plural is not None and plural.strip():
        raw_entries = plural.split(KEY_RING_SEPARATOR)
    elif singular is not None and singular.strip():
        raw_entries = [singular]
    else:
        raise KeyRingError(
            f"{name}: no key configured (both the list and the single value are empty)"
        )

    ring: list[str] = []
    for entry in raw_entries:
        candidate = entry.strip()
        if not candidate or candidate in ring:
            continue
        ring.append(candidate)

    if not ring:
        raise KeyRingError(f"{name}: the configured key list has no non-empty entry")
    return tuple(ring)


def derive_fernet_key(raw: str) -> bytes:
    """Fold a raw configuration string into a valid 32-byte Fernet key.

    SHA-256 → urlsafe-base64, byte-for-byte the derivation the four builders
    used before this module existed. Keeping it identical is what makes the
    migration to :class:`MultiFernet` a NON-EVENT for stored ciphertext: the key
    material for a given configuration string does not change, so every existing
    row still decrypts.
    """
    if not raw:
        raise KeyRingError("cannot derive a Fernet key from an empty string")
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def build_multifernet(ring: Sequence[str]) -> MultiFernet:
    """Build the :class:`MultiFernet` for ``ring`` (first encrypts, all decrypt).

    Raises:
        KeyRingError: ``ring`` is empty (MultiFernet accepts an empty list and
            then fails with an ``IndexError`` deep inside ``encrypt``).
    """
    if not ring:
        raise KeyRingError("cannot build a MultiFernet from an empty key ring")
    return MultiFernet([Fernet(derive_fernet_key(raw)) for raw in ring])


def primary_fernet(ring: Sequence[str]) -> Fernet:
    """The single-key cipher for the HEAD of ``ring`` — the one that encrypts.

    Used by the re-encryption command to tell "already on the head key" from
    "still on an older key": Fernet tokens carry no key id, so the only way to
    know which key produced one is to try that key alone. Without this the
    command could only report "rotated everything", which is both untrue after
    the first run and useless as a dry-run signal.
    """
    if not ring:
        raise KeyRingError("cannot build a Fernet from an empty key ring")
    return Fernet(derive_fernet_key(ring[0]))


def key_fingerprint(raw: str, *, length: int = 8) -> bytes:
    """A short, non-reversible id for the key derived from ``raw``.

    SHA-256 of the DERIVED key material, truncated. Used to stamp a key id into
    a file format (the backup bundle header, task_prod05_08) so a reader can
    pick the right key out of a ring instead of brute-forcing all of them, and
    can say "I do not have key ab12cd34" instead of "InvalidTag".

    A truncated hash of a hash is not a secret leak: it is second-preimage
    resistant at 64 bits for an ATTACKER WHO ALREADY HAS the ciphertext, and it
    reveals nothing about the key that the ciphertext itself does not.
    """
    if length < 1:
        raise KeyRingError("key fingerprint length must be >= 1 byte")
    return hashlib.sha256(derive_fernet_key(raw)).digest()[:length]


__all__ = [
    "KEY_RING_SEPARATOR",
    "KeyRingError",
    "build_multifernet",
    "derive_fernet_key",
    "key_fingerprint",
    "parse_key_ring",
    "primary_fernet",
]
