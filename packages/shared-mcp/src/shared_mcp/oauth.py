"""Generic OAuth 2.1 connector for remote MCP servers (ADR 0127).

Many first-party remote MCP servers (Atlassian's official
``mcp.atlassian.com``, GitHub, Notion, Google, Linear…) authenticate
with **OAuth 2.1**, not a static bearer. A hand-pasted access token
expires (~1 h) and would die mid-run — unusable for autonomous
execution. The MCP spec defines a *common* OAuth 2.1 flow for HTTP
transports (discovery RFC 9728/8414, Dynamic Client Registration
RFC 7591, Authorization Code + PKCE, refresh), so **one** conformant
implementation serves every server of this style.

The heavy lifting is already done by the official ``mcp`` SDK's
:class:`mcp.client.auth.OAuthClientProvider` — it performs discovery,
DCR, the PKCE exchange and **automatic token refresh**, driving a
pluggable :class:`mcp.client.auth.TokenStorage`. This module supplies
the two pieces the platform must own:

1. :class:`VaultTokenStorage` — a ``TokenStorage`` backed by Vault
   (per ``(tenant, project, server)`` key), so tokens live where every
   other credential does — never in the ``Project.mcp_servers`` JSONB,
   never in the DB. The SDK's auto-refresh writes the rotated token
   back through here (which is why :mod:`shared_mcp.auth` grew a
   ``write`` method — a read-only resolver could never refresh).
2. :func:`build_oauth_provider` — wires an ``OAuthClientProvider`` to a
   ``VaultTokenStorage`` + the redirect/callback handlers the caller
   supplies (a web connect/callback pair at consent time; raising
   no-op handlers at autonomous runtime, where a valid stored token +
   refresh should mean no human is ever prompted).

Headless-verifiability note (ADR 0127 residual risk c): the real
provider handshake (browser consent + a live Atlassian/GitHub
authorization server) cannot be exercised in a headless session. What
IS unit-tested here is the storage round-trip and the provider wiring;
the live consent is deferred to an interactive session.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from shared_mcp.auth import VaultResolver
from shared_mcp.exceptions import MCPAuthError

if TYPE_CHECKING:  # pragma: no cover - typing only
    import httpx
    from mcp.client.auth import OAuthClientProvider
    from mcp.shared.auth import (
        OAuthClientInformationFull,
        OAuthClientMetadata,
        OAuthToken,
    )

# Keys inside the single Vault entry that backs one server's OAuth state.
# We keep BOTH the token and the registered-client info in one entry so a
# refresh (token only) never orphans the DCR client registration.
_TOKEN_KEY = "oauth_token"
_CLIENT_KEY = "oauth_client_info"


def oauth_vault_path(*, tenant_id: str, project_id: str, server_name: str) -> str:
    """Build the Vault pointer for one server's OAuth state.

    Keyed by ``(tenant, project, server)`` so each tenant authorises
    *its own* account — no shared bot, no shared sidecar instance. This
    is the multi-tenancy win the ADR calls out: the credential boundary
    is the tenant, enforced by the path, not by a deploy-time secret.
    """
    return f"vault:secret/data/mcp-oauth/{tenant_id}/{project_id}/{server_name}"


class VaultTokenStorage:
    """A :class:`mcp.client.auth.TokenStorage` backed by Vault.

    Structural conformance only — the SDK's ``TokenStorage`` is a
    ``typing.Protocol`` (four async methods), so we do not inherit from
    it (that would force importing the SDK at module import time; we
    keep the SDK import lazy inside the methods that need the models).

    The resolver is called synchronously inside these async methods on
    purpose: the Vault round-trip is a single short sync HTTP request
    (hvac is sync) and the whole package already made that trade in
    :mod:`shared_mcp.auth`. Nothing here is CPU-bound or long.
    """

    def __init__(self, resolver: VaultResolver, auth_ref: str) -> None:
        self._resolver = resolver
        self._auth_ref = auth_ref

    # -- internal: whole-entry read that treats "not stored yet" as {} --
    def _read_entry(self) -> dict[str, str]:
        try:
            return self._resolver.resolve(self._auth_ref)
        except MCPAuthError:
            # No entry yet == not connected yet. The SDK's contract is
            # "return None when nothing is stored", so a missing entry is
            # the normal first-connect case, not an error. A genuinely
            # unreachable Vault surfaces on the write path / on connect.
            return {}

    def _write_entry(self, entry: dict[str, str]) -> None:
        self._resolver.write(self._auth_ref, entry)

    # -- TokenStorage protocol ------------------------------------------
    async def get_tokens(self) -> OAuthToken | None:
        from mcp.shared.auth import OAuthToken

        raw = self._read_entry().get(_TOKEN_KEY)
        if not raw:
            return None
        return OAuthToken.model_validate_json(raw)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        # Preserve the client-info half of the entry across a token-only
        # refresh so DCR registration is not lost.
        entry = self._read_entry()
        entry[_TOKEN_KEY] = tokens.model_dump_json(exclude_none=True)
        self._write_entry(entry)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        from mcp.shared.auth import OAuthClientInformationFull

        raw = self._read_entry().get(_CLIENT_KEY)
        if not raw:
            return None
        return OAuthClientInformationFull.model_validate_json(raw)

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        entry = self._read_entry()
        entry[_CLIENT_KEY] = client_info.model_dump_json(exclude_none=True)
        self._write_entry(entry)


async def _raise_on_interactive(*_args: object) -> object:
    """Default redirect/callback handler for AUTONOMOUS runtime.

    At runtime a valid stored token (or a refresh via the stored refresh
    token) must satisfy the request without a human. If the SDK falls
    through to redirecting a browser, there is no human to consent — so
    we fail LOUD with a typed, actionable error rather than hang the run
    for the whole OAuth timeout. The operator's fix is to (re)run the
    interactive "Connect" flow for this server.
    """
    raise MCPAuthError(
        "OAuth consent required but no interactive session is available — "
        "connect this MCP server once from the admin panel (ADR 0127)."
    )


def build_client_metadata(
    *,
    redirect_uri: str,
    scopes: str | None = None,
) -> OAuthClientMetadata:
    """Build the RFC 7591 client metadata the SDK registers with.

    ``token_endpoint_auth_method="none"`` marks a public client (PKCE
    is the proof, no client secret) — the common case for the
    dynamically-registered clients these MCP servers issue.
    """
    from mcp.shared.auth import OAuthClientMetadata

    return OAuthClientMetadata(
        redirect_uris=[redirect_uri],  # type: ignore[list-item]  # AnyUrl coerces the str
        token_endpoint_auth_method="none",
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        scope=scopes,
    )


def build_oauth_provider(
    *,
    server_url: str,
    storage: VaultTokenStorage,
    redirect_uri: str,
    scopes: str | None = None,
    redirect_handler: Callable[[str], Awaitable[None]] | None = None,
    callback_handler: Callable[[], Awaitable[tuple[str, str | None]]] | None = None,
    timeout: float = 300.0,
) -> OAuthClientProvider:
    """Construct the SDK's ``OAuthClientProvider`` wired to Vault storage.

    ``server_url`` is the server's base (the SDK derives the discovery /
    token endpoints from it), NOT the ``/mcp`` path. The two handlers
    default to :func:`_raise_on_interactive` — correct for the runtime
    connection, where a stored/refreshed token should mean neither is
    ever called. The connect/callback web endpoints pass real handlers.

    The returned provider is an ``httpx.Auth``; pass it as ``auth=`` to
    :func:`shared_mcp.client.MCPClient.connect`.
    """
    from mcp.client.auth import OAuthClientProvider

    return OAuthClientProvider(
        server_url=server_url,
        client_metadata=build_client_metadata(redirect_uri=redirect_uri, scopes=scopes),
        storage=storage,  # VaultTokenStorage matches the SDK's TokenStorage Protocol
        redirect_handler=redirect_handler or _raise_on_interactive,  # type: ignore[arg-type]
        callback_handler=callback_handler or _raise_on_interactive,  # type: ignore[arg-type]
        timeout=timeout,
    )


def as_httpx_auth(provider: OAuthClientProvider) -> httpx.Auth:
    """Narrowing helper: an ``OAuthClientProvider`` *is* an ``httpx.Auth``.

    Exists purely so callers get a clean ``httpx.Auth``-typed value to
    hand to the transport without importing the SDK class themselves.
    """
    return provider


__all__ = [
    "VaultTokenStorage",
    "as_httpx_auth",
    "build_client_metadata",
    "build_oauth_provider",
    "oauth_vault_path",
]
