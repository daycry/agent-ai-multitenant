"""Short-lived MFA challenge token (Plan 08 task_08_09).

When a user with a confirmed TOTP factor passes the password step, the
login endpoint does NOT mint a session. Instead it stashes the *interim*
result here — who authenticated (first factor done) but not yet which
session — keyed by a random, single-use challenge token, and returns that
token with ``status: mfa_required``. The user then POSTs the token + a
TOTP code to ``/auth/mfa/totp/verify``; only then is the real Redis
session (:class:`SessionStore`) + JWT issued.

This is deliberately NOT a :class:`SessionStore` entry:

  * it carries no ``sid`` and grants NO access — ``get_principal`` will
    never accept it, because it is stored under a different Redis key
    namespace and is not a JWT;
  * it is single-use (consumed with ``GETDEL``) and short-lived (a tight
    TTL), so a captured challenge token cannot be replayed and expires
    quickly if the second factor is never completed.

The record carries the ``tenant_id`` the session will be bound to (the
SSO flows pick a tenant; local login has none yet, stored as ``None``)
and whether the first factor was system-admin, so the completed session
matches exactly what the non-MFA path would have issued.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from uuid import UUID

from redis.asyncio import Redis

_KEY_PREFIX = "mfa:challenge:"
# 32 bytes of urlsafe entropy (~43 chars) — same strength as the OIDC
# state token. Unguessable, so it cannot be brute-forced inside its TTL.
_TOKEN_BYTES = 32


def _key(token: str) -> str:
    return f"{_KEY_PREFIX}{token}"


def new_challenge_token() -> str:
    """A fresh URL-safe random single-use challenge token."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


@dataclass(frozen=True)
class MfaChallenge:
    """The interim, first-factor-done state of a login awaiting MFA.

    Holds NO session id — it grants no access until the second factor
    completes and a real session is minted.
    """

    user_id: UUID
    tenant_id: UUID | None
    is_system_admin: bool


class MfaChallengeStore:
    """Redis-backed, single-use store for the MFA challenge token."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def create(self, token: str, challenge: MfaChallenge, *, ttl_seconds: int) -> None:
        payload = {
            "user_id": str(challenge.user_id),
            "tenant_id": str(challenge.tenant_id) if challenge.tenant_id else None,
            "is_system_admin": challenge.is_system_admin,
        }
        await self._redis.set(_key(token), json.dumps(payload), ex=ttl_seconds)

    async def consume(self, token: str) -> MfaChallenge | None:
        """Atomically fetch-and-delete the challenge — single-use.

        Returns ``None`` for an unknown / already-consumed / expired token,
        so a captured token cannot be replayed.
        """
        raw = await self._redis.getdel(_key(token))
        if raw is None:
            return None
        data = json.loads(raw)
        tenant_raw = data.get("tenant_id")
        return MfaChallenge(
            user_id=UUID(data["user_id"]),
            tenant_id=UUID(tenant_raw) if tenant_raw else None,
            is_system_admin=bool(data.get("is_system_admin", False)),
        )


__all__ = [
    "MfaChallenge",
    "MfaChallengeStore",
    "new_challenge_token",
]
