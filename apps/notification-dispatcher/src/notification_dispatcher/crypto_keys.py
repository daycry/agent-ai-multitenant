"""Key ring for the channel-secret cipher (prod-05 task_prod05_01).

The READ-side twin of :mod:`api_server.auth.crypto_keys`. The dispatcher is a
SEPARATE app with its own settings prefix (``NOTIFY_``) and, by design, no import
of ``api_server`` — so the parser is duplicated here rather than shared.

That duplication is the risk this module has to carry openly: the api-server
WRITES the ciphertext in ``notification_channels.secret_encrypted`` and the
dispatcher READS it, so the two key rings must resolve IDENTICALLY from
identically-shaped configuration. Two parsers that disagree about, say, whether
whitespace is stripped would mean a channel secret that encrypts fine and then
fails to send, with an ``InvalidToken`` at 03:00 as the only symptom.

``tests/unit/test_multifernet_builders.py`` therefore runs BOTH parsers over the
same table of inputs and fails on the first divergence. Change one, change the
other — and the plural env vars must be deployed to both services in the same
window (``docs/06-runbooks/05-key-rotation.md``).
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Sequence

from cryptography.fernet import Fernet, MultiFernet

#: Separator inside ``NOTIFY_NOTIFICATION_ENCRYPTION_KEYS``. Must match the
#: api-server's ``KEY_RING_SEPARATOR``.
KEY_RING_SEPARATOR = ","


class KeyRingError(ValueError):
    """Raised when the key-ring configuration resolves to zero usable keys."""


def parse_key_ring(
    *,
    plural: str | None,
    singular: str | None,
    name: str,
) -> tuple[str, ...]:
    """Resolve the ordered key ring: plural list wins, singular is the fallback.

    Contract (kept identical to the api-server twin): entries are split on
    commas, stripped, blanks dropped, duplicates removed keeping first-seen
    order; a non-blank plural value makes the singular one IRRELEVANT; an empty
    resolution is an error rather than an empty ring.
    """
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
    """SHA-256 → urlsafe-base64. Byte-identical to the api-server derivation."""
    if not raw:
        raise KeyRingError("cannot derive a Fernet key from an empty string")
    return base64.urlsafe_b64encode(hashlib.sha256(raw.encode("utf-8")).digest())


def build_multifernet(ring: Sequence[str]) -> MultiFernet:
    """Cipher over ``ring``: the head key encrypts, every key decrypts."""
    if not ring:
        raise KeyRingError("cannot build a MultiFernet from an empty key ring")
    return MultiFernet([Fernet(derive_fernet_key(raw)) for raw in ring])


__all__ = [
    "KEY_RING_SEPARATOR",
    "KeyRingError",
    "build_multifernet",
    "derive_fernet_key",
    "parse_key_ring",
]
