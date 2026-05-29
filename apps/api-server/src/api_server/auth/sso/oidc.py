"""Generic OIDC authorization-code flow (Plan 08 task_08_01).

The flow, end to end:

  1. **Discovery** — fetch ``<issuer>/.well-known/openid-configuration``
     to learn the authorize / token / userinfo / jwks endpoints. Cached
     per issuer for the process lifetime (IdP metadata is static).
  2. **Authorize URL** — build the redirect to the IdP with the
     requested scopes, the random ``state`` (anti-CSRF) and ``nonce``
     (ID-token replay guard).
  3. **Callback** — exchange the ``code`` for tokens at the token
     endpoint (HTTP Basic client auth), verify the ID token's signature
     against the IdP's JWKS, and assert ``iss`` / ``aud`` / ``nonce``.
  4. **Userinfo** — pull the user's claims (email, name, ...) from the
     userinfo endpoint using the access token.

Network is fully injectable: every HTTP call goes through an
``httpx.AsyncClient`` passed in by the caller, so tests wire a
``MockTransport`` and never touch a real IdP.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

import httpx
from joserfc import jwt as joserfc_jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

# OIDC requires the `openid` scope; we always include it even if a
# tenant's config forgot to list it.
OPENID_SCOPE = "openid"
_DISCOVERY_SUFFIX = "/.well-known/openid-configuration"
# Algorithms we accept on the ID token signature. RS256 is the OIDC
# baseline mandatory algorithm; we explicitly allow-list it (and the
# common ES/RS family) and reject anything else — notably `none`.
_ALLOWED_ID_TOKEN_ALGS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512"]


class OIDCError(Exception):
    """Any failure in the OIDC flow (discovery, token, userinfo, validation).

    The router maps this onto a 4xx for client-attributable problems
    (bad state/nonce) and a 502/500 for IdP-side faults; the message is
    safe to surface to logs but kept generic for the client.
    """


@dataclass(frozen=True)
class ResolvedOIDCConfig:
    """The per-tenant OIDC config with the client secret already resolved.

    Built by the router from a ``sso_configurations`` row +
    :func:`api_server.auth.sso.secrets.resolve_client_secret`. The flow
    itself never touches Vault / the DB — it only sees plaintext.
    """

    issuer: str
    client_id: str
    client_secret: str
    scopes: list[str] = field(default_factory=lambda: [OPENID_SCOPE, "email", "profile"])
    claim_mappings: dict[str, str] = field(default_factory=dict)

    def scope_string(self) -> str:
        """Space-delimited scopes with ``openid`` guaranteed present."""
        scopes = list(self.scopes)
        if OPENID_SCOPE not in scopes:
            scopes.insert(0, OPENID_SCOPE)
        return " ".join(scopes)


@dataclass(frozen=True)
class OIDCDiscovery:
    """The subset of the IdP's discovery document the flow needs."""

    authorization_endpoint: str
    token_endpoint: str
    userinfo_endpoint: str
    jwks_uri: str
    issuer: str


@dataclass(frozen=True)
class OIDCUserInfo:
    """Resolved identity claims for the authenticated user.

    ``email`` is the lookup key for JIT provisioning. ``claims`` is the
    full userinfo payload so future tasks (group→role mapping,
    task_08_11) can read more without another round-trip.
    """

    subject: str
    email: str
    full_name: str | None
    claims: dict[str, object]


class OIDCFlow:
    """Stateless helper that drives the OIDC authorization-code flow.

    One instance per request is fine — it holds only the injected HTTP
    client. Discovery results are cached on the class so repeated logins
    for the same issuer skip the metadata round-trip.
    """

    # issuer -> discovery. Cleared by `reset_discovery_cache()` in tests.
    _discovery_cache: ClassVar[dict[str, OIDCDiscovery]] = {}

    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http = http_client

    # -- discovery ---------------------------------------------------------
    async def discover(self, issuer: str) -> OIDCDiscovery:
        cached = self._discovery_cache.get(issuer)
        if cached is not None:
            return cached
        url = issuer.rstrip("/") + _DISCOVERY_SUFFIX
        try:
            resp = await self._http.get(url)
            resp.raise_for_status()
            doc = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError(f"OIDC discovery failed for issuer {issuer!r}") from exc
        try:
            discovery = OIDCDiscovery(
                authorization_endpoint=doc["authorization_endpoint"],
                token_endpoint=doc["token_endpoint"],
                userinfo_endpoint=doc["userinfo_endpoint"],
                jwks_uri=doc["jwks_uri"],
                issuer=doc.get("issuer", issuer),
            )
        except (KeyError, TypeError) as exc:
            raise OIDCError(f"OIDC discovery document for {issuer!r} is incomplete") from exc
        self._discovery_cache[issuer] = discovery
        return discovery

    @classmethod
    def reset_discovery_cache(cls) -> None:
        """Test hook: drop cached discovery so each test starts clean."""
        cls._discovery_cache = {}

    # -- authorize URL -----------------------------------------------------
    async def build_authorization_url(
        self,
        config: ResolvedOIDCConfig,
        *,
        redirect_uri: str,
        state: str,
        nonce: str,
    ) -> str:
        discovery = await self.discover(config.issuer)
        params = {
            "response_type": "code",
            "client_id": config.client_id,
            "redirect_uri": redirect_uri,
            "scope": config.scope_string(),
            "state": state,
            "nonce": nonce,
        }
        request = httpx.Request("GET", discovery.authorization_endpoint, params=params)
        return str(request.url)

    # -- code -> tokens ----------------------------------------------------
    async def exchange_code(
        self,
        config: ResolvedOIDCConfig,
        *,
        code: str,
        redirect_uri: str,
    ) -> dict[str, object]:
        discovery = await self.discover(config.issuer)
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": config.client_id,
        }
        try:
            resp = await self._http.post(
                discovery.token_endpoint,
                data=data,
                # Confidential client: HTTP Basic auth per RFC 6749 §2.3.1.
                auth=(config.client_id, config.client_secret),
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            tokens = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError("OIDC token exchange failed") from exc
        if not isinstance(tokens, dict) or "id_token" not in tokens:
            raise OIDCError("OIDC token response missing id_token")
        return tokens

    # -- ID token verification --------------------------------------------
    async def verify_id_token(
        self,
        config: ResolvedOIDCConfig,
        *,
        id_token: str,
        expected_nonce: str,
    ) -> dict[str, object]:
        """Verify signature (against JWKS) + iss/aud/nonce.

        Raises :class:`OIDCError` on a bad signature, a wrong issuer or
        audience, or a nonce mismatch (replay)."""
        discovery = await self.discover(config.issuer)
        try:
            jwks_resp = await self._http.get(discovery.jwks_uri)
            jwks_resp.raise_for_status()
            key_set = KeySet.import_key_set(jwks_resp.json())
        except (httpx.HTTPError, ValueError, JoseError) as exc:
            raise OIDCError("failed to fetch the IdP JWKS") from exc

        try:
            decoded = joserfc_jwt.decode(id_token, key_set, algorithms=_ALLOWED_ID_TOKEN_ALGS)
        except (JoseError, ValueError) as exc:
            raise OIDCError("ID token signature verification failed") from exc

        claims = dict(decoded.claims)
        if claims.get("iss") != discovery.issuer:
            raise OIDCError("ID token issuer mismatch")
        aud = claims.get("aud")
        aud_ok = config.client_id == aud or (isinstance(aud, list) and config.client_id in aud)
        if not aud_ok:
            raise OIDCError("ID token audience mismatch")
        if claims.get("nonce") != expected_nonce:
            raise OIDCError("ID token nonce mismatch (possible replay)")
        return claims

    # -- userinfo ----------------------------------------------------------
    async def fetch_userinfo(
        self,
        config: ResolvedOIDCConfig,
        *,
        access_token: str,
        id_token_claims: dict[str, object],
    ) -> OIDCUserInfo:
        discovery = await self.discover(config.issuer)
        try:
            resp = await self._http.get(
                discovery.userinfo_endpoint,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            resp.raise_for_status()
            userinfo = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError("OIDC userinfo request failed") from exc
        if not isinstance(userinfo, dict):
            raise OIDCError("OIDC userinfo response is not an object")

        # Merge ID-token claims under userinfo (userinfo wins on overlap).
        merged: dict[str, object] = {**id_token_claims, **userinfo}
        return self._map_claims(config, merged)

    def _map_claims(self, config: ResolvedOIDCConfig, claims: dict[str, object]) -> OIDCUserInfo:
        """Apply the tenant's claim→field mapping with OIDC-standard fallbacks."""
        mapping = config.claim_mappings
        email_claim = mapping.get("email", "email")
        name_claim = mapping.get("full_name", "name")

        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject:
            raise OIDCError("OIDC claims missing 'sub'")
        email = claims.get(email_claim)
        if not isinstance(email, str) or not email:
            raise OIDCError(f"OIDC claims missing email claim {email_claim!r}")
        raw_name = claims.get(name_claim)
        full_name = raw_name if isinstance(raw_name, str) and raw_name else None
        return OIDCUserInfo(
            subject=subject,
            email=email.lower(),
            full_name=full_name,
            claims=claims,
        )


__all__ = [
    "OIDCDiscovery",
    "OIDCError",
    "OIDCFlow",
    "OIDCUserInfo",
    "OPENID_SCOPE",
    "ResolvedOIDCConfig",
]
