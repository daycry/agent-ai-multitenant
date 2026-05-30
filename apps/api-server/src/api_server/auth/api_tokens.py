"""Public-API token minting + hashing + verification (Plan 13 task_13_01).

The public REST API (``/api/v1``) is authenticated by a per-tenant
``X-API-Token`` header (Plan 13 Decisiones Clave: header, never a query
param; the token grants access SCOPED to its own tenant only). This
module mints those tokens and resolves a presented one against the
at-rest hash.

Mirrors the SCIM bearer-token pattern (Plan 08 task_08_08,
:mod:`api_server.auth.scim.tokens`): the raw token is a long,
high-entropy random value shown to the Tenant Admin EXACTLY ONCE at
creation and never persisted in clear — the database only stores its
SHA-256 hex digest (``token_hash``) plus a short clear ``prefix`` for
listings.

Why SHA-256 and not a salted argon2 hash (as for user passwords): the
``X-API-Token`` request arrives unauthenticated — the token *is* the
only thing that identifies the calling tenant. Resolving it therefore
needs an equality lookup by value (``WHERE token_hash = :digest``),
which a per-token-salted hash cannot support. A single deterministic
digest is safe here precisely because the token is high-entropy random
(``API_TOKEN_SECRET_BYTES`` bytes), so it is not brute-forceable and
there is no rainbow-table risk.

The minted token is shaped ``<prefix>_<secret>`` so the leading clear
``prefix`` (kept in the DB for UI listings) is recoverable from the raw
token alone and the value is self-identifying as a platform API token.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

# Fixed clear marker that prefixes every public-API token. Lets the value
# be recognised as a platform API token (and grepped out of logs/leaks),
# the way ``ghp_`` / ``sk-`` markers do for other systems.
API_TOKEN_PREFIX_MARKER = "aapt"  # agent-ai platform token
# Length of the random, clear, per-token id appended to the marker to form
# the ``prefix`` stored in clear for UI listings (e.g. ``aapt_3f9a2c7b``).
API_TOKEN_PREFIX_ID_LEN = 8
# Bytes of CSPRNG entropy in the secret tail, urlsafe-base64 encoded
# (~43 chars). Well beyond brute-force reach, which is what lets a plain
# SHA-256 digest be the at-rest form (see module docstring).
API_TOKEN_SECRET_BYTES = 32
# Separator between the clear prefix and the secret tail in the raw token.
_API_TOKEN_SEP = "_"
# Default per-minute request budget a token gets when the Tenant Admin does
# not override it (Plan 13 Alcance: "default 100 req/min, configurable").
# Mirrored by ``Settings.api_token_default_rate_limit`` and the
# ``api_tokens.rate_limit`` server_default.
DEFAULT_API_TOKEN_RATE_LIMIT = 100


@dataclass(frozen=True, slots=True)
class GeneratedApiToken:
    """A freshly minted API token in its three forms.

    - ``token`` is the raw, clear value shown to the Tenant Admin once and
      never persisted. Shaped ``<prefix>_<secret>``.
    - ``prefix`` is the leading clear id (``<marker>_<id>``) stored in the
      DB for UI listings; non-secret.
    - ``token_hash`` is the SHA-256 hex digest of ``token`` — the only
      form that reaches the DB.
    """

    token: str
    prefix: str
    token_hash: str


def generate_api_token() -> GeneratedApiToken:
    """Mint a fresh public-API token and its at-rest forms.

    The returned :class:`GeneratedApiToken` carries the clear ``token``
    (shown to the operator once), the clear ``prefix`` for listings, and
    the ``token_hash`` to persist. The clear ``token`` is never stored.
    """
    prefix_id = secrets.token_hex(API_TOKEN_PREFIX_ID_LEN // 2)
    prefix = f"{API_TOKEN_PREFIX_MARKER}{_API_TOKEN_SEP}{prefix_id}"
    secret = secrets.token_urlsafe(API_TOKEN_SECRET_BYTES)
    token = f"{prefix}{_API_TOKEN_SEP}{secret}"
    return GeneratedApiToken(token=token, prefix=prefix, token_hash=hash_api_token(token))


def hash_api_token(token: str) -> str:
    """Return the SHA-256 hex digest of ``token`` (the at-rest form).

    Deterministic so an unauthenticated ``X-API-Token`` request can be
    matched by an equality lookup on ``api_tokens.token_hash``.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def prefix_of(token: str) -> str:
    """Recover the clear ``prefix`` (``<marker>_<id>``) from a raw token.

    Returns the leading ``marker_id`` segment used for UI listings. Falls
    back to the whole value if the token is not in the expected shape.
    """
    parts = token.split(_API_TOKEN_SEP)
    if len(parts) >= 3 and parts[0] == API_TOKEN_PREFIX_MARKER:
        return f"{parts[0]}{_API_TOKEN_SEP}{parts[1]}"
    return token


def verify_api_token(presented: str, token_hash: str) -> bool:
    """Constant-time check that ``presented`` hashes to ``token_hash``.

    Uses :func:`secrets.compare_digest` so the comparison time does not
    leak how many leading characters of the digest matched.
    """
    return secrets.compare_digest(hash_api_token(presented), token_hash)


__all__ = [
    "API_TOKEN_PREFIX_ID_LEN",
    "API_TOKEN_PREFIX_MARKER",
    "API_TOKEN_SECRET_BYTES",
    "DEFAULT_API_TOKEN_RATE_LIMIT",
    "GeneratedApiToken",
    "generate_api_token",
    "hash_api_token",
    "prefix_of",
    "verify_api_token",
]
