"""La mitad privilegiada del ADR 0131 (opción C): emitir y refrescar el token.

El sandbox no habla con Vault. Pide el access token por el API interno y aquí se
hace lo que requiere privilegio: leer Vault, canjear el refresh token y volver a
guardarlo. Lo que baja al contenedor no confiable es solo un access token
acotado a un servidor — ni la llave del almacén, ni la credencial de larga
duración.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

pytestmark = pytest.mark.unit

_TENANT = "11111111-1111-1111-1111-111111111111"
_PROJECT = "22222222-2222-2222-2222-222222222222"
_SERVER = "atlassian-remote"
_URL = "https://mcp.atlassian.com/v1/sse"


class _FakeResolver:
    """Vault de mentira: un dict por ruta, con las mismas semánticas de lectura."""

    def __init__(self, entries: dict[str, dict[str, str]] | None = None) -> None:
        self.entries = dict(entries or {})
        self.writes: list[tuple[str, dict[str, str]]] = []

    def resolve(self, ref: str) -> dict[str, str]:
        from shared_mcp import MCPAuthError

        if ref not in self.entries:
            raise MCPAuthError(f"no entry at {ref}")
        return dict(self.entries[ref])

    def write(self, ref: str, entry: dict[str, str]) -> None:
        self.entries[ref] = dict(entry)
        self.writes.append((ref, dict(entry)))


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self) -> dict[str, Any]:
        return self._payload


class _FakeHttpClient:
    """Cliente HTTP de mentira. `get` sirve el documento de discovery (RFC 8414)
    que el refresco necesita para saber a qué token endpoint ir."""

    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.posts: list[tuple[str, dict[str, str]]] = []

    async def get(self, url: str, *, headers: Any = None) -> _FakeResponse:
        return _FakeResponse(
            200,
            {
                "issuer": "https://mcp.atlassian.com",
                "authorization_endpoint": "https://mcp.atlassian.com/authorize",
                "token_endpoint": "https://mcp.atlassian.com/token",
            },
        )

    async def post(self, url: str, *, data: dict[str, str], headers: Any = None) -> _FakeResponse:
        self.posts.append((url, dict(data)))
        return self._response


def _entry(*, access: str, refresh: str | None = None, client: bool = True) -> dict[str, str]:
    token: dict[str, Any] = {"access_token": access, "token_type": "Bearer"}
    if refresh:
        token["refresh_token"] = refresh
    entry = {"oauth_token": json.dumps(token)}
    if client:
        entry["oauth_client_info"] = json.dumps(
            {"client_id": "cid-1", "redirect_uris": ["http://localhost/cb"]}
        )
    return entry


def _path() -> str:
    from shared_mcp.oauth import oauth_vault_path

    return oauth_vault_path(tenant_id=_TENANT, project_id=_PROJECT, server_name=_SERVER)


async def _issue(resolver: Any, http_client: Any, *, refresh: bool = False) -> Any:
    from api_server.mcp_oauth_flow import issue_access_token

    return await issue_access_token(
        tenant_id=_TENANT,
        project_id=_PROJECT,
        server_name=_SERVER,
        server_url=_URL,
        resolver=resolver,
        http_client=http_client,
        refresh=refresh,
    )


@pytest.mark.asyncio
async def test_the_stored_access_token_is_handed_over_without_touching_the_network() -> None:
    resolver = _FakeResolver({_path(): _entry(access="tok-guardado")})
    client = _FakeHttpClient(_FakeResponse(500, {}))

    grant = await _issue(resolver, client)

    assert grant.access_token == "tok-guardado"
    assert grant.token_type == "Bearer"
    assert client.posts == [], "sin refresco no hay ida al proveedor"


@pytest.mark.asyncio
async def test_a_server_that_was_never_connected_says_so() -> None:
    """El motivo tiene que ser accionable: lo arregla un humano conectándolo, no
    un reintento."""
    from api_server.mcp_oauth_flow import McpOAuthError

    with pytest.raises(McpOAuthError, match="no está conectado"):
        await _issue(_FakeResolver(), _FakeHttpClient(_FakeResponse(200, {})))


@pytest.mark.asyncio
async def test_refresh_exchanges_persists_and_returns_the_new_token() -> None:
    resolver = _FakeResolver({_path(): _entry(access="viejo", refresh="rt-1")})
    client = _FakeHttpClient(
        _FakeResponse(
            200, {"access_token": "nuevo", "token_type": "Bearer", "refresh_token": "rt-2"}
        )
    )

    grant = await _issue(resolver, client, refresh=True)

    assert grant.access_token == "nuevo"
    assert client.posts and client.posts[0][1]["grant_type"] == "refresh_token"
    assert client.posts[0][1]["refresh_token"] == "rt-1"
    stored = json.loads(resolver.entries[_path()]["oauth_token"])
    assert stored["access_token"] == "nuevo" and stored["refresh_token"] == "rt-2"


@pytest.mark.asyncio
async def test_a_refresh_that_omits_the_refresh_token_keeps_the_old_one() -> None:
    """RFC 6749 §6: en un refresco el servidor PUEDE omitir `refresh_token`, y
    entonces el anterior sigue siendo válido. Guardar la respuesta tal cual
    borraría el único refresh que teníamos y el SIGUIENTE refresco fallaría —
    el servidor quedaría desconectado hasta que un humano lo reconectase.
    """
    resolver = _FakeResolver({_path(): _entry(access="viejo", refresh="rt-1")})
    client = _FakeHttpClient(_FakeResponse(200, {"access_token": "nuevo", "token_type": "Bearer"}))

    await _issue(resolver, client, refresh=True)

    stored = json.loads(resolver.entries[_path()]["oauth_token"])
    assert stored["refresh_token"] == "rt-1", "se perdió el refresh token en el refresco"


@pytest.mark.asyncio
async def test_the_dcr_client_registration_survives_a_refresh() -> None:
    """Un refresco escribe solo la mitad del token; perder el registro DCR
    obligaría a re-registrar el cliente en el proveedor."""
    resolver = _FakeResolver({_path(): _entry(access="viejo", refresh="rt-1")})
    client = _FakeHttpClient(_FakeResponse(200, {"access_token": "nuevo", "token_type": "Bearer"}))

    await _issue(resolver, client, refresh=True)

    assert "oauth_client_info" in resolver.entries[_path()]


@pytest.mark.asyncio
async def test_no_refresh_token_asks_for_a_reconnect_instead_of_looping() -> None:
    from api_server.mcp_oauth_flow import McpOAuthError

    resolver = _FakeResolver({_path(): _entry(access="viejo")})

    with pytest.raises(McpOAuthError, match="volver a conectarlo"):
        await _issue(resolver, _FakeHttpClient(_FakeResponse(200, {})), refresh=True)


@pytest.mark.asyncio
async def test_a_rejected_refresh_surfaces_the_providers_status() -> None:
    from api_server.mcp_oauth_flow import McpOAuthError

    resolver = _FakeResolver({_path(): _entry(access="viejo", refresh="rt-1")})
    client = _FakeHttpClient(_FakeResponse(400, {"error": "invalid_grant"}))

    with pytest.raises(McpOAuthError, match="400"):
        await _issue(resolver, client, refresh=True)


def test_the_vault_path_is_keyed_by_tenant_and_project() -> None:
    """La frontera de credenciales es la ruta. El endpoint la compone con el
    tenant del token y el proyecto del run — nunca con lo que mande el sandbox."""
    from shared_mcp.oauth import oauth_vault_path

    mine = oauth_vault_path(tenant_id=_TENANT, project_id=_PROJECT, server_name=_SERVER)
    theirs = oauth_vault_path(tenant_id="otro-tenant", project_id=_PROJECT, server_name=_SERVER)
    assert mine != theirs
    assert _TENANT in mine and _PROJECT in mine
