"""SCIM bearer-token minting + hashing (Plan 08 task_08_08).

CLAUDE.md principle: no plaintext secrets in the DB. A SCIM token is a
long, high-entropy random value shown to the operator EXACTLY ONCE at
mint time; the database only ever stores its SHA-256 hex digest
(``token_hash``) plus a short clear ``token_prefix`` for UI
disambiguation.

Why SHA-256 and not a salted argon2 hash (as for user passwords): the
SCIM request arrives unauthenticated — the token *is* the only thing
that identifies the calling tenant. Resolving it therefore needs an
equality lookup by value (``WHERE token_hash = :digest``), which a
per-token-salted hash cannot support. A single deterministic digest is
safe here precisely because the token is high-entropy random
(``SCIM_TOKEN_BYTES`` bytes), so it is not brute-forceable and there is
no rainbow-table risk.
"""

from __future__ import annotations

import hashlib
import secrets

# 32 bytes of CSPRNG entropy, urlsafe-base64 encoded (~43 chars). Well
# beyond brute-force reach, which is what lets a plain SHA-256 digest be
# the at-rest form (see module docstring).
SCIM_TOKEN_BYTES = 32
# Characters of the clear token kept in `token_prefix` for the UI to tell
# multiple tokens apart without revealing the value.
SCIM_TOKEN_PREFIX_LEN = 8


def generate_scim_token() -> str:
    """Return a fresh, high-entropy SCIM bearer token (clear text).

    Shown to the operator once at mint time and never persisted in clear —
    only :func:`hash_scim_token` of it reaches the DB.
    """
    return secrets.token_urlsafe(SCIM_TOKEN_BYTES)


def hash_scim_token(token: str) -> str:
    """Return the SHA-256 hex digest of ``token`` (the at-rest form).

    Deterministic so the unauthenticated SCIM request can be matched by an
    equality lookup on ``scim_tokens.token_hash``.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_prefix(token: str) -> str:
    """The leading clear characters stored for UI disambiguation."""
    return token[:SCIM_TOKEN_PREFIX_LEN]


__all__ = [
    "SCIM_TOKEN_BYTES",
    "SCIM_TOKEN_PREFIX_LEN",
    "generate_scim_token",
    "hash_scim_token",
    "token_prefix",
]
