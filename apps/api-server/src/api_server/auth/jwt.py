"""JWT encode/decode wrappers.

Claims used by the platform:

  sub  (subject)    user id (UUID v7, string form)
  tid  (tenant id)  optional — the active tenant of the session; absent
                    if the user is still picking one
  iat  (issued at)  unix timestamp
  exp  (expires)    unix timestamp

Signed HS256 with the shared secret from settings.

JOSE LIBRARY (prod-09 task_prod09_17, quality-10). This module used to run on
`python-jose`, which left the platform with two JOSE stacks (the SSO/OIDC flow of
Plan 08 verifies ID tokens with `joserfc`). It is now `joserfc` everywhere and
`python-jose` is out of the dependency set. The wire format is unchanged —
HS256 over the same secret — so sessions minted before the deploy keep working.

Two differences between the libraries made the swap sharper than it looks, and
both are load-bearing here:

1. **`joserfc.jwt.decode` does not validate `exp`.** It verifies the signature
   and hands back the claims of an expired token without complaint; expiry is a
   separate `JWTClaimsRegistry.validate()` call. `python-jose` did it implicitly.
   Every caller of :func:`decode_jwt` relies on the implicit behaviour —
   `auth/deps.get_principal` per request, and `routers/ws._credential_still_valid`
   on an already-open socket (authz-3), which exists ONLY to notice expiry — so
   dropping the check would have turned every session into a permanent one while
   leaving the whole suite green. Hence :data:`_CLAIMS` below, with `exp`
   *essential*: joserfc treats an absent claim as satisfied unless told
   otherwise, so "no `exp`" has to be rejected as loudly as "`exp` in the past".
2. **A `str` secret is not a key.** `joserfc` raises a bare
   `ValueError("Invalid key")` — not a `JoseError` — when handed one, so the
   secret must be wrapped in an `OctKey`, and the `except` clause has to cover
   the non-`JoseError` failures too (a signed payload that is a JSON *array*
   also lands there). Anything that escapes as a `ValueError`/`TypeError` becomes
   a 500 where a 401 is meant.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey

from api_server.config import get_settings


class InvalidTokenError(Exception):
    """Raised when a JWT fails to decode (bad signature, expired, malformed)."""


# Claim validation is opt-in in joserfc (see the module docstring). `exp` is
# essential because an unbounded session is the failure mode we cannot see;
# `iat`/`nbf` are validated when present. Reusing one registry is safe: it reads
# the clock per call, and we keep `leeway` at its 0 default on purpose — issuer
# and verifier are the same process.
_CLAIMS = jwt.JWTClaimsRegistry(exp={"essential": True})


def sign_claims(claims: dict[str, Any], *, secret: str, algorithm: str) -> str:
    """Sign an arbitrary claim set. The single place that touches `jwt.encode`.

    Used by :func:`encode_jwt` for human sessions and by
    :mod:`api_server.auth.internal_agent` for worker→api tokens — two different
    signing keys (task_prod09_03), one JOSE stack.
    """
    signed: str = jwt.encode(
        {"alg": algorithm, "typ": "JWT"},
        claims,
        OctKey.import_key(secret),
    )
    return signed


def verify_claims(token: str, *, secret: str, algorithm: str) -> dict[str, Any]:
    """Verify signature + `exp`/`iat`/`nbf` and return the claims.

    Raises :class:`InvalidTokenError` for EVERY failure mode, including the ones
    joserfc reports as plain `ValueError`/`TypeError` (see the module docstring):
    the callers turn this one exception into a 401, so anything that escapes it
    becomes a 500 on a merely invalid token.
    """
    try:
        decoded = jwt.decode(token, OctKey.import_key(secret), algorithms=[algorithm])
        claims = decoded.claims
        if not isinstance(claims, dict):
            raise InvalidTokenError("token payload is not a JSON object")
        _CLAIMS.validate(claims)
        return dict(claims)
    except JoseError as exc:
        raise InvalidTokenError(str(exc)) from exc
    except (TypeError, ValueError) as exc:
        raise InvalidTokenError(str(exc)) from exc


def encode_jwt(
    *,
    user_id: UUID,
    session_id: UUID,
    tenant_id: UUID | None = None,
    is_system_admin: bool = False,
    is_system_owner: bool = False,
    expires_in: timedelta | None = None,
    extra_claims: dict[str, Any] | None = None,
) -> str:
    """Sign a token.

    - `session_id` is mandatory — every token is bound to a server-side
      session in Redis so logout can revoke instantly.
    - `is_system_admin` lifts the user out of RLS for admin endpoints. The
      claim is still fixed at login time, but since prod-09 task_prod09_04 it is
      only a HINT: :func:`api_server.auth.deps.require_system_admin` re-reads
      ``users.is_system_admin`` from the DB on every admin request, so revoking
      admin in the database takes effect on the NEXT request instead of
      surviving in already-issued tokens for the 24 h session TTL (the Phase-0
      caveat this note used to document, finding authz-4).
    - `is_system_owner` has behaved the same way since ADR 0074.
    """
    settings = get_settings()
    now = datetime.now(tz=UTC)
    ttl = expires_in or timedelta(minutes=settings.jwt_expiration_minutes)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "sid": str(session_id),
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
    }
    if tenant_id is not None:
        claims["tid"] = str(tenant_id)
    if is_system_admin:
        claims["sys"] = True
    # `own` is a hint for cheap reads; `require_system_owner` re-checks the DB
    # (ADR 0074) so a stale token can't grant ownership on its own.
    if is_system_owner:
        claims["own"] = True
    if extra_claims:
        claims.update(extra_claims)
    return sign_claims(
        claims,
        secret=settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )


def decode_jwt(token: str) -> dict[str, Any]:
    """Return the decoded claims or raise InvalidTokenError.

    Enforces, in this order: the HS256 signature, then `exp` (present and in the
    future) plus `iat`/`nbf` when present. Every failure mode — bad signature,
    expired, malformed, wrong algorithm, payload that is not a JSON object —
    surfaces as :class:`InvalidTokenError` so the callers' 401 path covers all of
    them.
    """
    settings = get_settings()
    return verify_claims(
        token,
        secret=settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
    )
