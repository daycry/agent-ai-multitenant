"""ADR 0127 — interactive OAuth «Connect» flow engine (`mcp_oauth_flow`).

Unit tests with fakes (httpx client, Redis, Vault resolver) — no network, no
real provider. Covers discovery, DCR (register-once + reuse), the authorize-URL
minting + pending-state stash, and the code→token exchange + persistence.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

import pytest
from api_server.mcp_oauth_flow import (
    McpOAuthError,
    begin_flow,
    complete_flow,
    discover_auth_server,
    ensure_registered_client,
    find_server_url,
)
from shared_mcp.auth import StaticVaultResolver
from shared_mcp.oauth import VaultTokenStorage, oauth_vault_path

pytestmark = pytest.mark.unit

_AS_META = {
    "authorization_endpoint": "https://cf.example/v1/authorize",
    "token_endpoint": "https://cf.example/v1/token",
    "registration_endpoint": "https://cf.example/v1/register",
}
_SERVER_URL = "https://mcp.example.com/v1/mcp"


class _Resp:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict:
        assert self._payload is not None
        return self._payload


class _FakeHttp:
    """Programmable async httpx stand-in. `routes` maps (method, url-substr) →
    _Resp; `calls` records every request for assertions."""

    def __init__(self, routes: dict[tuple[str, str], _Resp]) -> None:
        self._routes = routes
        self.calls: list[tuple[str, str, dict]] = []

    def _match(self, method: str, url: str) -> _Resp:
        for (m, sub), resp in self._routes.items():
            if m == method and sub in url:
                return resp
        raise AssertionError(f"no fake route for {method} {url}")

    async def get(self, url: str, **kw: object) -> _Resp:
        self.calls.append(("GET", url, dict(kw)))
        return self._match("GET", url)

    async def post(self, url: str, **kw: object) -> _Resp:
        self.calls.append(("POST", url, dict(kw)))
        return self._match("POST", url)


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_discover_auth_server_reads_well_known() -> None:
    http = _FakeHttp({("GET", "/.well-known/oauth-authorization-server"): _Resp(200, _AS_META)})
    meta = await discover_auth_server(_SERVER_URL, http)  # type: ignore[arg-type]
    assert meta.authorization_endpoint == _AS_META["authorization_endpoint"]
    assert meta.token_endpoint == _AS_META["token_endpoint"]
    assert meta.registration_endpoint == _AS_META["registration_endpoint"]


@pytest.mark.asyncio
async def test_discover_auth_server_raises_on_404() -> None:
    http = _FakeHttp({("GET", "/.well-known/oauth-authorization-server"): _Resp(404, text="nope")})
    with pytest.raises(McpOAuthError):
        await discover_auth_server(_SERVER_URL, http)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# DCR — register once, reuse thereafter
# ---------------------------------------------------------------------------
def _meta() -> object:
    from api_server.mcp_oauth_flow import AuthServerMeta

    return AuthServerMeta(**_AS_META)


@pytest.mark.asyncio
async def test_ensure_registered_client_registers_and_persists() -> None:
    resolver = StaticVaultResolver()
    storage = VaultTokenStorage(
        resolver, oauth_vault_path(tenant_id="t", project_id="p", server_name="s")
    )
    redirect = "https://host/api/projects/p/mcp-servers/s/oauth/callback"
    reg = {
        "client_id": "CID",
        "redirect_uris": [redirect],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    http = _FakeHttp({("POST", "/v1/register"): _Resp(201, reg)})
    info = await ensure_registered_client(storage, _meta(), redirect, http)  # type: ignore[arg-type]
    assert info.client_id == "CID"
    # persisted → a second call reuses it (no second POST)
    http2 = _FakeHttp({})  # any POST would AssertionError
    info2 = await ensure_registered_client(storage, _meta(), redirect, http2)  # type: ignore[arg-type]
    assert info2.client_id == "CID"
    assert http2.calls == []


# ---------------------------------------------------------------------------
# begin_flow → authorize URL + pending state
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_begin_flow_mints_authorize_url_and_stashes_state() -> None:
    resolver = StaticVaultResolver()
    redis = _FakeRedis()
    reg = {
        "client_id": "CID",
        "redirect_uris": ["https://host/cb"],
        "token_endpoint_auth_method": "none",
    }
    http = _FakeHttp(
        {
            ("GET", "/.well-known/oauth-authorization-server"): _Resp(200, _AS_META),
            ("POST", "/v1/register"): _Resp(201, reg),
        }
    )
    url = await begin_flow(
        tenant_id="t",
        project_id="p",
        server_name="atlassian-remote",
        server_url=_SERVER_URL,
        redirect_base="https://host/api",
        resolver=resolver,
        redis=redis,
        http_client=http,  # type: ignore[arg-type]
    )
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == _AS_META["authorization_endpoint"]
    q = parse_qs(parts.query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["CID"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["redirect_uri"] == [
        "https://host/api/projects/p/mcp-servers/atlassian-remote/oauth/callback"
    ]
    state = q["state"][0]
    # pending stash present + carries the code_verifier for the exchange
    stashed = json.loads(redis.store[f"mcp:oauth:pending:{state}"])
    assert stashed["client_id"] == "CID"
    assert stashed["code_verifier"]
    assert stashed["token_endpoint"] == _AS_META["token_endpoint"]


# ---------------------------------------------------------------------------
# complete_flow → exchange + persist
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_complete_flow_exchanges_and_persists_tokens() -> None:
    resolver = StaticVaultResolver()
    redis = _FakeRedis()
    reg = {
        "client_id": "CID",
        "redirect_uris": ["https://host/cb"],
        "token_endpoint_auth_method": "none",
    }
    http = _FakeHttp(
        {
            ("GET", "/.well-known/oauth-authorization-server"): _Resp(200, _AS_META),
            ("POST", "/v1/register"): _Resp(201, reg),
        }
    )
    url = await begin_flow(
        tenant_id="t",
        project_id="p",
        server_name="s",
        server_url=_SERVER_URL,
        redirect_base="https://host/api",
        resolver=resolver,
        redis=redis,
        http_client=http,  # type: ignore[arg-type]
    )
    state = parse_qs(urlsplit(url).query)["state"][0]

    token_payload = {
        "access_token": "AT",
        "token_type": "Bearer",
        "expires_in": 3600,
        "refresh_token": "RT",
        "scope": "read:jira",
    }
    http_cb = _FakeHttp({("POST", "/v1/token"): _Resp(200, token_payload)})
    connected = await complete_flow(
        state=state,
        code="the-code",
        resolver=resolver,
        redis=redis,
        http_client=http_cb,  # type: ignore[arg-type]
    )
    assert connected == "s"
    # token persisted to Vault
    storage = VaultTokenStorage(
        resolver, oauth_vault_path(tenant_id="t", project_id="p", server_name="s")
    )
    tok = await storage.get_tokens()
    assert tok is not None and tok.access_token == "AT" and tok.refresh_token == "RT"
    # state consumed (single-use)
    assert f"mcp:oauth:pending:{state}" not in redis.store
    # exchange posted the code + verifier
    _, _, kw = http_cb.calls[0]
    assert kw["data"]["code"] == "the-code"
    assert kw["data"]["grant_type"] == "authorization_code"


@pytest.mark.asyncio
async def test_complete_flow_unknown_state_raises() -> None:
    with pytest.raises(McpOAuthError):
        await complete_flow(
            state="nope",
            code="x",
            resolver=StaticVaultResolver(),
            redis=_FakeRedis(),
            http_client=_FakeHttp({}),  # type: ignore[arg-type]
        )


# ---------------------------------------------------------------------------
# find_server_url
# ---------------------------------------------------------------------------
def test_find_server_url_returns_http_url() -> None:
    servers = [
        {"name": "atlassian-remote", "transport": "streamable_http", "url": _SERVER_URL},
        {"name": "other", "transport": "stdio", "command": "x"},
    ]
    assert find_server_url(servers, "atlassian-remote") == _SERVER_URL
    assert find_server_url(servers, "missing") is None
