"""Interactive OAuth 2.1 «Connect» flow for remote MCP servers (ADR 0127).

The generic connector's INTERACTIVE half: a web-native authorization-code +
PKCE flow split across two HTTP requests (connect → provider consent →
callback). We drive it ourselves (mirroring the OIDC/SSO pattern) rather than
the SDK's blocking ``OAuthClientProvider`` callbacks, which are CLI-shaped and
do not fit a stateless request/response split. At RUNTIME the SDK's provider
(``shared_mcp.oauth.build_oauth_provider`` + ``VaultTokenStorage``) takes over
for the actual MCP connection + automatic refresh, reading the tokens +
DCR client_info this flow persisted to Vault.

Standard flow (verified against Atlassian's remote MCP, which supports RFC 8414
discovery + RFC 7591 DCR + PKCE + rotating refresh tokens):

1. connect: discover the authorization server (well-known), register a public
   client via DCR if we have not already (persist client_info to Vault), mint
   PKCE + state, stash the pending flow in Redis (TTL), return the provider's
   authorization URL for the browser to follow.
2. callback: pop the pending flow by ``state``, exchange the code for tokens at
   the token endpoint, persist the tokens to Vault.

Nothing here talks to the DB; the caller resolves the project/server + supplies
the Vault resolver + Redis client.
"""

from __future__ import annotations

import json
import secrets
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
import structlog
from mcp.client.auth import PKCEParameters
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from redis.asyncio import Redis
from shared_mcp.catalog import uses_oauth
from shared_mcp.oauth import VaultTokenStorage, oauth_vault_path

from api_server.mcp.config import MCPServerConfigModel

_log = structlog.get_logger(__name__)

# Redis key for one in-flight connect→callback flow, keyed by the opaque
# `state`. Single-use + short TTL: mirrors the MFA challenge store.
_PENDING_PREFIX = "mcp:oauth:pending:"
_PENDING_TTL_S = 600  # 10 minutes to complete consent

# The OAuth client we register via DCR is a PUBLIC client (PKCE is the proof;
# no client secret). Human-facing name shown on some consent screens.
_CLIENT_NAME = "agentic-platform"


class McpOAuthError(Exception):
    """A step of the interactive OAuth flow failed (discovery / DCR / exchange /
    unknown-or-expired state). The router maps it to a typed 4xx or an error
    redirect so the operator sees a useful message, not a stack trace."""


@dataclass(frozen=True)
class AuthServerMeta:
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def callback_redirect_uri(base: str, project_id: str, server_name: str) -> str:
    """The absolute redirect_uri registered with the provider + used at
    exchange. MUST be byte-identical in DCR, the authorize URL and the token
    exchange. `base` is the public API base (origin + api prefix)."""
    return f"{base.rstrip('/')}/projects/{project_id}/mcp-servers/{server_name}/oauth/callback"


async def discover_auth_server(server_url: str, client: httpx.AsyncClient) -> AuthServerMeta:
    """Discover the authorization server metadata (RFC 8414).

    Tries the origin-root well-known first (what Atlassian exposes), then the
    RFC 8414 path-insertion variant as a fallback for servers that host it
    under the resource path.
    """
    origin = _origin(server_url)
    path = urlsplit(server_url).path.rstrip("/")
    candidates = [f"{origin}/.well-known/oauth-authorization-server"]
    if path:
        candidates.append(f"{origin}/.well-known/oauth-authorization-server{path}")
    last_err: str = "no candidate URLs"
    for url in candidates:
        try:
            resp = await client.get(url, headers={"Accept": "application/json"})
        except httpx.HTTPError as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            continue
        if resp.status_code != 200:
            last_err = f"{url} → HTTP {resp.status_code}"
            continue
        data = resp.json()
        auth_ep = data.get("authorization_endpoint")
        token_ep = data.get("token_endpoint")
        if not auth_ep or not token_ep:
            last_err = f"{url} → metadata missing authorization/token endpoint"
            continue
        return AuthServerMeta(
            authorization_endpoint=str(auth_ep),
            token_endpoint=str(token_ep),
            registration_endpoint=(
                str(data["registration_endpoint"]) if data.get("registration_endpoint") else None
            ),
        )
    raise McpOAuthError(f"OAuth discovery failed for {server_url!r}: {last_err}")


async def ensure_registered_client(
    storage: VaultTokenStorage,
    meta: AuthServerMeta,
    redirect_uri: str,
    client: httpx.AsyncClient,
) -> OAuthClientInformationFull:
    """Return the DCR client, registering it once and persisting to Vault.

    Reuses a previously-registered client_info (per tenant/project/server) so we
    do not re-register on every connect — and so the RUNTIME SDK provider finds
    the same client_id for refresh. DCR gives no re-download token, so what we
    register is what we persist.
    """
    existing = await storage.get_client_info()
    if existing is not None and existing.client_id:
        # Keep the registration but make sure our current redirect_uri is one of
        # its redirect_uris (the public base can change between deploys).
        uris = [str(u) for u in (existing.redirect_uris or [])]
        if redirect_uri in uris:
            return existing

    if meta.registration_endpoint is None:
        raise McpOAuthError(
            "server does not advertise a registration_endpoint and no client is "
            "pre-registered (dynamic client registration unavailable)"
        )
    body = {
        "client_name": _CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
    }
    try:
        resp = await client.post(meta.registration_endpoint, json=body)
    except httpx.HTTPError as exc:
        raise McpOAuthError(f"DCR request failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code not in (200, 201):
        raise McpOAuthError(f"DCR rejected: HTTP {resp.status_code}: {resp.text[:300]}")
    info = OAuthClientInformationFull.model_validate(resp.json())
    await storage.set_client_info(info)
    return info


@dataclass(frozen=True)
class _PendingFlow:
    tenant_id: str
    project_id: str
    server_name: str
    token_endpoint: str
    client_id: str
    redirect_uri: str
    code_verifier: str
    scope: str | None


async def _store_pending(redis: Redis, state: str, flow: _PendingFlow) -> None:
    await redis.set(
        f"{_PENDING_PREFIX}{state}",
        json.dumps(flow.__dict__),
        ex=_PENDING_TTL_S,
    )


async def _pop_pending(redis: Redis, state: str) -> _PendingFlow:
    key = f"{_PENDING_PREFIX}{state}"
    raw = await redis.get(key)
    if raw is None:
        raise McpOAuthError("unknown or expired OAuth state (re-start «Connect»)")
    await redis.delete(key)  # single-use
    data = json.loads(raw)
    return _PendingFlow(**data)


async def begin_flow(
    *,
    tenant_id: str,
    project_id: str,
    server_name: str,
    server_url: str,
    redirect_base: str,
    resolver: object,
    redis: Redis,
    http_client: httpx.AsyncClient,
    scope: str | None = None,
) -> str:
    """Discover + register + mint the authorization URL. Returns the URL the
    browser must be sent to for consent."""
    storage = VaultTokenStorage(
        resolver,  # type: ignore[arg-type]
        oauth_vault_path(tenant_id=tenant_id, project_id=project_id, server_name=server_name),
    )
    redirect_uri = callback_redirect_uri(redirect_base, project_id, server_name)

    meta = await discover_auth_server(server_url, http_client)
    info = await ensure_registered_client(storage, meta, redirect_uri, http_client)
    client_id = info.client_id
    if not client_id:
        raise McpOAuthError("registered client has no client_id")

    pkce = PKCEParameters.generate()
    state = secrets.token_urlsafe(32)
    await _store_pending(
        redis,
        state,
        _PendingFlow(
            tenant_id=tenant_id,
            project_id=project_id,
            server_name=server_name,
            token_endpoint=meta.token_endpoint,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_verifier=pkce.code_verifier,
            scope=scope,
        ),
    )
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": pkce.code_challenge,
        "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = scope
    return f"{meta.authorization_endpoint}?{urlencode(params)}"


async def complete_flow(
    *,
    state: str,
    code: str,
    resolver: object,
    redis: Redis,
    http_client: httpx.AsyncClient,
) -> str:
    """Exchange the authorization code for tokens + persist them. Returns the
    server_name that was connected (for the redirect back to the UI)."""
    flow = await _pop_pending(redis, state)
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": flow.redirect_uri,
        "client_id": flow.client_id,
        "code_verifier": flow.code_verifier,
    }
    try:
        resp = await http_client.post(
            flow.token_endpoint,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.HTTPError as exc:
        raise McpOAuthError(f"token exchange request failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise McpOAuthError(f"token exchange rejected: HTTP {resp.status_code}: {resp.text[:300]}")
    token = OAuthToken.model_validate(resp.json())

    storage = VaultTokenStorage(
        resolver,  # type: ignore[arg-type]
        oauth_vault_path(
            tenant_id=flow.tenant_id,
            project_id=flow.project_id,
            server_name=flow.server_name,
        ),
    )
    await storage.set_tokens(token)
    _log.info(
        "mcp_oauth.connected",
        project_id=flow.project_id,
        server=flow.server_name,
        has_refresh=bool(token.refresh_token),
    )
    return flow.server_name


@dataclass(frozen=True)
class AccessGrant:
    """Lo ÚNICO que sale hacia el sandbox: un access token y su tipo."""

    access_token: str
    token_type: str


async def issue_access_token(
    *,
    tenant_id: str,
    project_id: str,
    server_name: str,
    server_url: str,
    resolver: object,
    http_client: httpx.AsyncClient,
    refresh: bool = False,
) -> AccessGrant:
    """El access token vigente de un servidor OAuth, refrescándolo si hace falta.

    La mitad privilegiada de la opción C del ADR 0131. El sandbox NO habla con
    Vault: pide el token por el API interno y aquí se hace lo que requiere
    privilegio —leer Vault, canjear el refresh token y volver a guardarlo—. Así
    el contenedor no confiable no tiene ni un token de Vault (la llave del
    almacén) ni el refresh token (la credencial de larga duración): solo un
    access token acotado a un servidor y efímero.

    El refresco lo dispara un 401 REAL del servidor remoto (``refresh=True``), no
    una cuenta atrás: la entrada de Vault no guarda un vencimiento absoluto —
    ``OAuthToken`` solo trae ``expires_in``—, y adivinarlo con el reloj falla con
    la deriva horaria y con los servidores que revocan antes de tiempo. El
    disparador honesto es el rechazo del propio servidor.
    """
    storage = VaultTokenStorage(
        resolver,  # type: ignore[arg-type]
        oauth_vault_path(tenant_id=tenant_id, project_id=project_id, server_name=server_name),
    )
    token = await storage.get_tokens()
    if token is None:
        raise McpOAuthError(
            f"el servidor {server_name!r} no está conectado: no hay token guardado. "
            "Conéctalo desde el panel del proyecto."
        )
    if not refresh:
        return AccessGrant(token.access_token, token.token_type)

    if not token.refresh_token:
        raise McpOAuthError(
            f"el token de {server_name!r} caducó y el proveedor no dio refresh_token: "
            "hay que volver a conectarlo desde el panel."
        )
    client_info = await storage.get_client_info()
    if client_info is None:
        raise McpOAuthError(
            f"el servidor {server_name!r} no tiene registro de cliente (DCR) guardado: "
            "hay que volver a conectarlo desde el panel."
        )

    meta = await discover_auth_server(server_url, http_client)
    data = {
        "grant_type": "refresh_token",
        "refresh_token": token.refresh_token,
        "client_id": client_info.client_id,
    }
    if client_info.client_secret:
        data["client_secret"] = client_info.client_secret
    try:
        resp = await http_client.post(
            meta.token_endpoint,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    except httpx.HTTPError as exc:
        raise McpOAuthError(f"refresh request failed: {type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise McpOAuthError(f"refresh rejected: HTTP {resp.status_code}: {resp.text[:300]}")

    fresh = OAuthToken.model_validate(resp.json())
    # RFC 6749 §6: en un refresco el servidor PUEDE omitir `refresh_token`, y
    # entonces el anterior sigue siendo válido. Guardar la respuesta tal cual
    # borraría el único refresh que teníamos y el SIGUIENTE refresco fallaría —
    # el servidor quedaría desconectado hasta que un humano lo reconectase.
    if not fresh.refresh_token:
        fresh = fresh.model_copy(update={"refresh_token": token.refresh_token})
    await storage.set_tokens(fresh)
    _log.info("mcp_oauth.refreshed", project_id=project_id, server=server_name)
    return AccessGrant(fresh.access_token, fresh.token_type)


def find_server_url(payload_servers: list[dict[str, object]], server_name: str) -> str | None:
    """Return the declared server's `url` (HTTP transports only), or None."""
    for raw in payload_servers:
        if not isinstance(raw, dict) or raw.get("name") != server_name:
            continue
        try:
            model = MCPServerConfigModel.model_validate(raw)
        except Exception:  # - malformed entry → treat as not found
            return None
        return model.url
    return None


def serialise_servers_for_run(
    servers: Iterable[Mapping[str, Any]], *, tenant_id: str, project_id: str
) -> list[dict[str, Any]]:
    """Project ``project.mcp_servers`` into what the run request carries.

    The one thing this adds is ``oauth_ref``: the Vault pointer to a server's
    OAuth state (ADR 0127). The runtime cannot derive it — the persisted config
    has no ``auth_kind`` (only the catalog does, keyed by URL) and the runtime
    does not know its tenant/project. So the dispatch, which knows both,
    resolves it here and the runtime just reads it (task_wf_12, B-03).

    Without this the run opened the session with no ``auth=`` and the remote
    server answered 401 — the interactive "Connect" flow was complete and
    delivered nothing to the one place it existed for, autonomous execution.

    Returns COPIES: mutating the JSONB list in place would mark the ``projects``
    row dirty and write the pointer back to the database.
    """
    out: list[dict[str, Any]] = []
    for raw in servers:
        server = dict(raw)
        name = str(server.get("name") or "")
        if name and not server.get("oauth_ref") and uses_oauth(server.get("url")):
            server["oauth_ref"] = oauth_vault_path(
                tenant_id=tenant_id, project_id=project_id, server_name=name
            )
        out.append(server)
    return out


__all__ = [
    "AuthServerMeta",
    "McpOAuthError",
    "begin_flow",
    "callback_redirect_uri",
    "complete_flow",
    "discover_auth_server",
    "ensure_registered_client",
    "find_server_url",
    "serialise_servers_for_run",
]
