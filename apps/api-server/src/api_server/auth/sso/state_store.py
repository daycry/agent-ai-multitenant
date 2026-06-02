"""Short-lived OIDC login state — anti-CSRF ``state`` + replay-guard ``nonce``.

The OIDC authorization-code flow spans two HTTP requests:

  1. ``GET /auth/sso/{provider_id}/oidc/login`` mints a random ``state``
     and ``nonce``, stashes them (plus the global provider they belong
     to) here, and redirects the browser to the IdP.
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

The record carries the ``provider_id`` that started the flow (ADR 0047:
auth providers are platform-global, so the flow is keyed by the global
provider, not a tenant). The callback (which has no provider in its
path) resolves the right global SSO config from it and asserts the
enabled global config still matches — a captured ``state`` can never be
steered onto a different provider.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from uuid import UUID

from redis.asyncio import Redis

_KEY_PREFIX = "sso:oidc:state:"
_SAML_KEY_PREFIX = "sso:saml:relay:"
# OIDC state/nonce: 32 bytes of urlsafe entropy (~43 chars). Plenty to
# make guessing infeasible without bloating the redirect URL.
_TOKEN_BYTES = 32


def _key(state: str) -> str:
    return f"{_KEY_PREFIX}{state}"


def _saml_key(relay_state: str) -> str:
    return f"{_SAML_KEY_PREFIX}{relay_state}"


def new_token() -> str:
    """A fresh URL-safe random token for ``state`` / ``nonce``."""
    return secrets.token_urlsafe(_TOKEN_BYTES)


@dataclass(frozen=True)
class LoginState:
    """The server-side half of an in-flight OIDC login.

    Carries the global ``provider_id`` (the ``sso_configurations`` row id)
    that started the flow (ADR 0047) so the callback resolves the same
    provider it began with — never a different one.
    """

    provider_id: UUID
    nonce: str
    redirect_uri: str


class OIDCStateStore:
    """Redis-backed, single-use store for the login ``state``."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def create(self, state: str, login_state: LoginState, *, ttl_seconds: int) -> None:
        payload = {
            "provider_id": str(login_state.provider_id),
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
            provider_id=UUID(data["provider_id"]),
            nonce=data["nonce"],
            redirect_uri=data["redirect_uri"],
        )


@dataclass(frozen=True)
class SAMLLoginState:
    """The server-side half of an in-flight SP-initiated SAML login.

    Carries the global ``provider_id`` (the ``sso_configurations`` row id;
    the ACS endpoint has no provider in its path, like the OIDC callback)
    and the ``request_id`` of the AuthnRequest, so the ACS can assert the
    response's ``InResponseTo`` matches — the SAML analogue of the OIDC
    ``state``/``nonce`` guard.
    """

    provider_id: UUID
    request_id: str


class SAMLRelayStateStore:
    """Redis-backed, single-use store keyed by the SAML ``RelayState``.

    SP-initiated logins stash the global provider + AuthnRequest id here
    keyed by a random RelayState the IdP echoes back to the ACS.
    IdP-initiated (unsolicited) responses carry no known RelayState, so
    the ACS simply finds nothing — that is expected and the caller falls
    back to the single enabled global SAML provider.
    """

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def create(
        self, relay_state: str, login_state: SAMLLoginState, *, ttl_seconds: int
    ) -> None:
        payload = {
            "provider_id": str(login_state.provider_id),
            "request_id": login_state.request_id,
        }
        await self._redis.set(_saml_key(relay_state), json.dumps(payload), ex=ttl_seconds)

    async def consume(self, relay_state: str) -> SAMLLoginState | None:
        """Atomically fetch-and-delete the relay state — single-use.

        Returns ``None`` for an unknown / already-consumed / expired
        relay state (and for IdP-initiated logins, which never created
        one)."""
        raw = await self._redis.getdel(_saml_key(relay_state))
        if raw is None:
            return None
        data = json.loads(raw)
        return SAMLLoginState(
            provider_id=UUID(data["provider_id"]),
            request_id=data["request_id"],
        )


__all__ = [
    "LoginState",
    "OIDCStateStore",
    "SAMLLoginState",
    "SAMLRelayStateStore",
    "new_token",
]
