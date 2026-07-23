"""ADR 0127 — generic OAuth connector for remote MCP servers.

Unit tests for the VERIFIABLE core: the Vault read+write resolver, the
``VaultTokenStorage`` round-trip (incl. the refresh-preserves-DCR
invariant), the ``auth=`` wiring into the HTTP transports, and the
catalog ``auth_kind`` values. The live provider handshake (browser
consent against a real Atlassian/GitHub authorization server) is NOT
covered here — it is not exercisable headless (ADR 0127 residual risk).
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack

import httpx
import pytest
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from shared_mcp.auth import StaticVaultResolver
from shared_mcp.catalog import CATALOG
from shared_mcp.client import _open_streams
from shared_mcp.exceptions import MCPAuthError
from shared_mcp.oauth import (
    VaultTokenStorage,
    _raise_on_interactive,
    build_client_metadata,
    build_oauth_provider,
    oauth_vault_path,
)
from shared_mcp.types import MCPServerConfig

_REF = "vault:secret/data/mcp-oauth/t1/p1/atlassian-remote"

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Vault write (the gap that made static bearer tokens go stale)
# ---------------------------------------------------------------------------
def test_static_resolver_write_then_resolve_round_trip() -> None:
    r = StaticVaultResolver()
    r.write(_REF, {"a": "1", "b": "2"})
    assert r.resolve(_REF) == {"a": "1", "b": "2"}


def test_static_resolver_write_overwrites_whole_entry() -> None:
    r = StaticVaultResolver(values={_REF: {"stale": "x"}})
    r.write(_REF, {"fresh": "y"})
    # KV-v2 create_or_update semantics: replace, not merge.
    assert r.resolve(_REF) == {"fresh": "y"}


def test_static_resolver_write_does_not_alias_caller_dict() -> None:
    r = StaticVaultResolver()
    payload = {"k": "v"}
    r.write(_REF, payload)
    payload["k"] = "mutated"
    assert r.resolve(_REF) == {"k": "v"}


# ---------------------------------------------------------------------------
# oauth_vault_path — per (tenant, project, server) key
# ---------------------------------------------------------------------------
def test_oauth_vault_path_is_keyed_by_tenant_project_server() -> None:
    p = oauth_vault_path(tenant_id="t1", project_id="p1", server_name="atlassian-remote")
    assert p == "vault:secret/data/mcp-oauth/t1/p1/atlassian-remote"
    # Different tenants never collide → clean per-tenant credential boundary.
    other = oauth_vault_path(tenant_id="t2", project_id="p1", server_name="atlassian-remote")
    assert other != p


# ---------------------------------------------------------------------------
# VaultTokenStorage round-trip
# ---------------------------------------------------------------------------
@pytest.fixture
def storage() -> VaultTokenStorage:
    return VaultTokenStorage(StaticVaultResolver(), _REF)


@pytest.mark.asyncio
async def test_get_tokens_none_when_nothing_stored(storage: VaultTokenStorage) -> None:
    assert await storage.get_tokens() is None
    assert await storage.get_client_info() is None


@pytest.mark.asyncio
async def test_set_then_get_tokens_round_trip(storage: VaultTokenStorage) -> None:
    tok = OAuthToken(access_token="AT", refresh_token="RT", expires_in=3600, scope="read:jira")
    await storage.set_tokens(tok)
    got = await storage.get_tokens()
    assert got is not None
    assert got.access_token == "AT"
    assert got.refresh_token == "RT"
    assert got.expires_in == 3600
    assert got.scope == "read:jira"


@pytest.mark.asyncio
async def test_set_then_get_client_info_round_trip(storage: VaultTokenStorage) -> None:
    info = OAuthClientInformationFull(
        client_id="CID",
        client_secret="CSEC",
        redirect_uris=["http://localhost:8000/cb"],  # type: ignore[list-item]
    )
    await storage.set_client_info(info)
    got = await storage.get_client_info()
    assert got is not None
    assert got.client_id == "CID"
    assert got.client_secret == "CSEC"


@pytest.mark.asyncio
async def test_token_refresh_preserves_client_info(storage: VaultTokenStorage) -> None:
    # DCR registration first, then a token-only refresh must NOT orphan it.
    info = OAuthClientInformationFull(
        client_id="CID",
        redirect_uris=["http://localhost:8000/cb"],  # type: ignore[list-item]
    )
    await storage.set_client_info(info)
    await storage.set_tokens(OAuthToken(access_token="AT1", refresh_token="RT1"))
    # simulate the SDK's auto-refresh writing a rotated token back
    await storage.set_tokens(OAuthToken(access_token="AT2", refresh_token="RT2"))

    got_tok = await storage.get_tokens()
    got_info = await storage.get_client_info()
    assert got_tok is not None and got_tok.access_token == "AT2"
    assert got_info is not None and got_info.client_id == "CID"


@pytest.mark.asyncio
async def test_setting_client_info_preserves_existing_tokens(storage: VaultTokenStorage) -> None:
    await storage.set_tokens(OAuthToken(access_token="AT", refresh_token="RT"))
    await storage.set_client_info(
        OAuthClientInformationFull(
            client_id="CID",
            redirect_uris=["http://localhost:8000/cb"],  # type: ignore[list-item]
        )
    )
    got = await storage.get_tokens()
    assert got is not None and got.access_token == "AT"


@pytest.mark.asyncio
async def test_stored_token_is_json_not_cleartext_fields(storage: VaultTokenStorage) -> None:
    # The Vault entry holds ONE json blob per half, not exploded fields —
    # so resolve() stays a dict[str,str] and the token never leaks as a
    # loose "access_token" env/header key by accident.
    await storage.set_tokens(OAuthToken(access_token="AT", refresh_token="RT"))
    entry = storage._read_entry()
    assert set(entry) <= {"oauth_token", "oauth_client_info"}
    assert json.loads(entry["oauth_token"])["access_token"] == "AT"


# ---------------------------------------------------------------------------
# client_metadata + provider construction
# ---------------------------------------------------------------------------
def test_build_client_metadata_is_public_pkce_client() -> None:
    meta = build_client_metadata(redirect_uri="http://localhost:8000/cb", scopes="read:jira")
    assert len(meta.redirect_uris or []) == 1
    assert meta.token_endpoint_auth_method == "none"
    assert "refresh_token" in meta.grant_types
    assert meta.scope == "read:jira"


def test_build_oauth_provider_returns_httpx_auth() -> None:
    provider = build_oauth_provider(
        server_url="https://mcp.atlassian.com",
        storage=VaultTokenStorage(StaticVaultResolver(), _REF),
        redirect_uri="http://localhost:8000/cb",
        scopes="read:jira",
    )
    assert isinstance(provider, OAuthClientProvider)
    assert isinstance(provider, httpx.Auth)


@pytest.mark.asyncio
async def test_default_runtime_handlers_fail_loud_not_hang() -> None:
    # At autonomous runtime the default handlers must raise (there is no
    # human to consent) rather than block for the whole OAuth timeout.
    with pytest.raises(MCPAuthError):
        await _raise_on_interactive("https://example/authorize")
    with pytest.raises(MCPAuthError):
        await _raise_on_interactive()


# ---------------------------------------------------------------------------
# auth= wiring into the HTTP transports
# ---------------------------------------------------------------------------
class _FakeStreamsCM:
    def __init__(self, values: tuple[object, ...]) -> None:
        self._values = values

    async def __aenter__(self) -> tuple[object, ...]:
        return self._values

    async def __aexit__(self, *_a: object) -> bool:
        return False


@pytest.mark.asyncio
async def test_auth_forwarded_to_streamable_http(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_client(**kwargs: object) -> _FakeStreamsCM:
        captured.update(kwargs)
        return _FakeStreamsCM((object(), object(), object()))  # streamable yields 3

    monkeypatch.setattr("shared_mcp.client.streamablehttp_client", fake_client)
    sentinel = object()
    cfg = MCPServerConfig(name="x", transport="streamable_http", url="https://h/mcp")
    async with AsyncExitStack() as stack:
        await _open_streams(stack, cfg, auth=sentinel)  # type: ignore[arg-type]
    assert captured["auth"] is sentinel


@pytest.mark.asyncio
async def test_auth_forwarded_to_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_client(**kwargs: object) -> _FakeStreamsCM:
        captured.update(kwargs)
        return _FakeStreamsCM((object(), object()))  # sse yields 2

    monkeypatch.setattr("shared_mcp.client.sse_client", fake_client)
    sentinel = object()
    cfg = MCPServerConfig(name="x", transport="sse", url="https://h/sse")
    async with AsyncExitStack() as stack:
        await _open_streams(stack, cfg, auth=sentinel)  # type: ignore[arg-type]
    assert captured["auth"] is sentinel


@pytest.mark.asyncio
async def test_stdio_ignores_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_stdio(_params: object) -> _FakeStreamsCM:
        captured["called"] = True
        return _FakeStreamsCM((object(), object()))

    monkeypatch.setattr("shared_mcp.client.stdio_client", fake_stdio)
    cfg = MCPServerConfig(name="x", transport="stdio", command="echo")
    async with AsyncExitStack() as stack:
        # auth is accepted but simply not forwarded (no HTTP client).
        await _open_streams(stack, cfg, auth=object())  # type: ignore[arg-type]
    assert captured["called"] is True


# ---------------------------------------------------------------------------
# catalog auth_kind (ADR 0127)
# ---------------------------------------------------------------------------
def test_atlassian_sidecar_is_auth_kind_sidecar() -> None:
    assert CATALOG["atlassian"].auth_kind == "sidecar"


def test_atlassian_remote_is_oauth() -> None:
    assert CATALOG["atlassian-remote"].auth_kind == "oauth"
    assert CATALOG["atlassian-remote"].transport == "sse"


def test_context7_is_none_and_github_remote_is_static() -> None:
    # regression guard: the derivation still holds for the existing HTTP entries
    assert CATALOG["context7"].auth_kind == "none"
    assert CATALOG["github-remote"].auth_kind == "static"


def test_atlassian_remote_withheld_from_picker() -> None:
    # Not offered until the interactive flow is verified against the live provider.
    from api_server.routers.mcp_catalog import offered_catalog

    offered_ids = {t.id for t in offered_catalog()}
    assert "atlassian-remote" not in offered_ids
    # the sidecar + the other HTTP templates ARE offered
    assert "atlassian" in offered_ids
