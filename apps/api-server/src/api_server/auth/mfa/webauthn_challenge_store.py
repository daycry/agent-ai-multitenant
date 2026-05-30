"""Single-use WebAuthn ceremony challenges in Redis (Plan 08 task_08_10).

A WebAuthn ceremony is two round-trips: the server emits options carrying a
random challenge, then later verifies the authenticator's response against
that SAME challenge. The challenge MUST be:

  * **server-generated and unpredictable** — it is the anti-replay nonce the
    authenticator signs over;
  * **single-use** — consumed with ``GETDEL`` so a captured response cannot
    be replayed against a still-valid challenge;
  * **short-lived** — a tight TTL bounds how long a ceremony may take.

We keep two namespaces so a registration challenge can never be redeemed by
the authentication verify path (or vice-versa):

  * registration is keyed by ``user_id`` — the user is logged in, so the
    JWT principal is the key, and a fresh ``register/options`` call simply
    overwrites any in-flight challenge for that user;
  * authentication is keyed by the interim MFA challenge token (the
    ``mfa_token`` minted by the password/SSO step) — that token already
    identifies the pending login, so the WebAuthn challenge rides alongside
    it and is consumed by the verify call.

Only the challenge bytes are stored (base64url-encoded for JSON); they are
not secret, but treating them as single-use nonces is what makes the
ceremony replay-safe.
"""

from __future__ import annotations

from redis.asyncio import Redis
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url

_REG_PREFIX = "mfa:webauthn:reg:"
_AUTH_PREFIX = "mfa:webauthn:auth:"


def _reg_key(user_id: str) -> str:
    return f"{_REG_PREFIX}{user_id}"


def _auth_key(mfa_token: str) -> str:
    return f"{_AUTH_PREFIX}{mfa_token}"


class WebauthnChallengeStore:
    """Redis-backed, single-use store for WebAuthn ceremony challenges."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    # ----- registration (keyed by the logged-in user) -----
    async def put_registration(self, user_id: str, challenge: bytes, *, ttl_seconds: int) -> None:
        await self._redis.set(_reg_key(user_id), bytes_to_base64url(challenge), ex=ttl_seconds)

    async def consume_registration(self, user_id: str) -> bytes | None:
        raw = await self._redis.getdel(_reg_key(user_id))
        if raw is None:
            return None
        return bytes(base64url_to_bytes(_as_str(raw)))

    # ----- authentication (keyed by the interim MFA challenge token) -----
    async def put_authentication(
        self, mfa_token: str, challenge: bytes, *, ttl_seconds: int
    ) -> None:
        await self._redis.set(_auth_key(mfa_token), bytes_to_base64url(challenge), ex=ttl_seconds)

    async def consume_authentication(self, mfa_token: str) -> bytes | None:
        raw = await self._redis.getdel(_auth_key(mfa_token))
        if raw is None:
            return None
        return bytes(base64url_to_bytes(_as_str(raw)))


def _as_str(raw: str | bytes) -> str:
    """Tolerate a Redis client configured with or without ``decode_responses``."""
    return raw.decode("utf-8") if isinstance(raw, bytes) else raw


__all__ = ["WebauthnChallengeStore"]
