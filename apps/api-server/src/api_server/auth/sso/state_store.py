"""Short-lived OIDC login state — anti-CSRF ``state`` + replay-guard ``nonce``.

The OIDC authorization-code flow spans two HTTP requests:

  1. ``GET /auth/sso/{tenant_id}/oidc/login`` mints a random ``state``
     and ``nonce``, stashes them (plus the tenant they belong to) here,
     and redirects the browser to the IdP.
  2. ``GET /auth/sso/oidc/callback?...&state=...`` looks the ``state``
     back up. A missing/expired/mismatched ``state`` is a CSRF attempt
     (or a stale tab) → the callback 400s. The stored ``nonce`` is then
     compared against the ID token's ``nonce`` claim to defeat token
     replay.

Why Redis and not a signed cookie: the callback is a top-level browser
navigation initiated by the IdP, so a SameSite cookie may not ride
along on every IdP. Keeping the state server-side (keyed by the random
value the IdP echoes back) sidesteps that and gives us single-use
semantics for free — the callback deletes the key on consumption, so a
captured ``state`` cannot be replayed.

The record carries the ``tenant_id`` from the login URL so the callback
(which has no tenant in its path) resolves the right tenant's SSO
config — and cannot be tricked into using another tenant's.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from uuid import UUID

from redis.asyncio import Redis

_KEY_PREFIX = "sso:oidc:state:"
# OIDC state/nonce: 32 bytes of urlsafe entropy (~43 chars). Plenty to
# make guessing infeasible without bloating the redirect URL.
_TOKEN_BYTES = 32


def _key(state: str) -> str:
    return f"{_KEY_PREFIX}{state}"


def new_token() -> str:
    """A fresh URL-safe random token for ``state`` / ``nonce``."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


@dataclass(frozen=True)
class LoginState:
    """The server-side half of an in-flight OIDC login."""

    tenant_id: UUID
    nonce: str
    redirect_uri: str


class OIDCStateStore:
    """Redis-backed, single-use store for the login ``state``."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def create(self, state: str, login_state: LoginState, *, ttl_seconds: int) -> None:
        payload = {
            "tenant_id": str(login_state.tenant_id),
            "nonce": login_state.nonce,
            "redirect_uri": login_state.redirect_uri,
        }
        await self._redis.set(_key(state), json.dumps(payload), ex=ttl_seconds)

    async def consume(self, state: str) -> LoginState | None:
        """Atomically fetch-and-delete the state. Returns ``None`` when the
        ``state`` is unknown or already consumed/expired — single-use."""
        key = _key(state)
        # GETDEL is atomic: no window where two concurrent callbacks both
        # see the same state as valid.
        raw = await self._redis.getdel(key)
        if raw is None:
            return None
        data = json.loads(raw)
        return LoginState(
            tenant_id=UUID(data["tenant_id"]),
            nonce=data["nonce"],
            redirect_uri=data["redirect_uri"],
        )


__all__ = ["LoginState", "OIDCStateStore", "new_token"]
