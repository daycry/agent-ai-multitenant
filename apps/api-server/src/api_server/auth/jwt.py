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

from collections.abc import Sequence
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


def verify_claims_any(token: str, *, secrets: Sequence[str], algorithm: str) -> dict[str, Any]:
    """Verify against a RING of secrets: any one may have signed the token.

    This is what makes a signing-key rotation survivable (prod-05 task_prod05_04,
    gap2-7). Rotating a single-secret verifier is a flag day: every session and
    every ``AGENTIC_INTERNAL_TOKEN`` already injected into a running
    agent-runtime 401s the instant the new value is deployed, which kills plan
    executions mid-flight. With a ring, the new key is added at the head (it
    signs) while the previous one stays in the tail (it still verifies) until the
    maximum token TTL has passed.

    THE SIGNATURE — not the claims — SELECTS THE KEY, and that distinction is
    load-bearing. Once ``jwt.decode`` succeeds for a secret we stop: that secret
    signed this token, so a claim failure (expired, no ``exp``, non-object
    payload) is FINAL and must surface as itself. Retrying the remaining secrets
    after a claim failure would turn "your session expired" into "bad signature"
    — the same 401, but a wrong diagnosis in the logs — and worse, it would let a
    later key mask an expiry the earlier one had already proven.

    Raises:
        InvalidTokenError: no secret in the ring validated the signature, or the
            secret that did rejected the claims. Every failure mode funnels here
            (including joserfc's bare ``ValueError``/``TypeError``, see the module
            docstring) because the callers turn this one exception into a 401 and
            anything that escapes becomes a 500 on a merely invalid token.
    """
    if not secrets:
        # Defensive: an empty ring means "verify against nothing". Accepting the
        # token would be catastrophic; reaching joserfc with no key would be a
        # confusing 500. Reject explicitly.
        raise InvalidTokenError("no signing secret is configured")

    last_signature_error: Exception | None = None
    for secret in secrets:
        try:
            decoded = jwt.decode(token, OctKey.import_key(secret), algorithms=[algorithm])
        except (JoseError, TypeError, ValueError) as exc:
            last_signature_error = exc
            continue
        # This secret signed the token. Claim validation errors are final.
        try:
            claims = decoded.claims
            if not isinstance(claims, dict):
                raise InvalidTokenError("token payload is not a JSON object")
            _CLAIMS.validate(claims)
            return dict(claims)
        except JoseError as exc:
            raise InvalidTokenError(str(exc)) from exc
        except (TypeError, ValueError) as exc:
            raise InvalidTokenError(str(exc)) from exc

    raise InvalidTokenError(str(last_signature_error))


def verify_claims(token: str, *, secret: str, algorithm: str) -> dict[str, Any]:
    """Verify signature + `exp`/`iat`/`nbf` against ONE secret and return the claims.

    Kept as the single-secret front door for callers that legitimately have
    exactly one key (and for the tests that pin the JOSE behaviour). It is a
    one-element :func:`verify_claims_any`, so the two cannot drift.
    """
    return verify_claims_any(token, secrets=(secret,), algorithm=algorithm)


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
        # HEAD of the ring (prod-05 task_prod05_04). With the list unset this is
        # `jwt_secret` verbatim; during a rotation it is the NEW key, so every
        # token minted from the deploy onwards is already on the key that will
        # survive when the old one is dropped.
        secret=settings.jwt_secret_ring[0],
        algorithm=settings.jwt_algorithm,
    )


def decode_jwt(token: str) -> dict[str, Any]:
    """Return the decoded claims or raise InvalidTokenError.

    Enforces, in this order: the HS256 signature against EVERY secret in
    ``API_SERVER_JWT_SECRET(S)``, then `exp` (present and in the future) plus
    `iat`/`nbf` when present. Every failure mode — no secret validates the
    signature, expired, malformed, wrong algorithm, payload that is not a JSON
    object — surfaces as :class:`InvalidTokenError` so the callers' 401 path
    covers all of them.

    Verifying against the whole ring is what lets a JWT rotation keep live
    sessions alive (gap2-7): a token signed with yesterday's key still validates
    while that key remains in the tail.
    """
    settings = get_settings()
    return verify_claims_any(
        token,
        secrets=settings.jwt_secret_ring,
        algorithm=settings.jwt_algorithm,
    )
