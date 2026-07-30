"""El OAuth de MCP llega hasta el run (task_wf_12, B-03).

ADR 0127 dejó completo el flujo interactivo: el operador pulsa «Conectar», la
plataforma hace discovery + DCR + PKCE y guarda el token (y el registro DCR) en
Vault bajo `mcp-oauth/{tenant}/{project}/{server}`. Lo que faltaba era el último
salto: en el run, `MCPToolRunner.connect` abría la sesión **sin** `auth=`, así
que el servidor remoto respondía 401 y el ADR no servía de nada en la ejecución
autónoma — justo el escenario para el que se diseñó (un bearer pegado a mano
caduca a la hora y muere a mitad de run).

Hay una pieza que el plan daba por hecha y no existía: **el `auth_kind` no se
persiste** en `project.mcp_servers` (el propio frontend lo deduce del catálogo
por URL). Así que el runtime no tenía forma de saber que un servidor usa OAuth,
ni el `tenant_id`/`project_id` con los que construir la ruta de Vault. La vía
elegida es que el DISPATCH — que sí tiene ese contexto — resuelva el puntero y
lo emita como `oauth_ref`; el runtime solo lo lee.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from shared_mcp.catalog import template_for_url, uses_oauth
from shared_mcp.oauth import oauth_vault_path
from shared_mcp.types import MCPServerConfig

pytestmark = pytest.mark.unit


_ATLASSIAN_REMOTE_URL = "https://mcp.atlassian.com/v1/mcp"


# ---------------------------------------------------------------------------
# Saber QUÉ servidor usa OAuth, sin un campo persistido
# ---------------------------------------------------------------------------
def test_the_catalog_identifies_an_oauth_server_by_url() -> None:
    assert uses_oauth(_ATLASSIAN_REMOTE_URL) is True


def test_a_static_bearer_server_is_not_oauth() -> None:
    """El remoto de GitHub usa un PAT en la cabecera: `auth_kind="static"`."""
    assert uses_oauth("https://api.githubcopilot.com/mcp/") is False


def test_an_unknown_url_is_not_oauth() -> None:
    """Un servidor propio del tenant no está en el catálogo: no se le inventa
    un flujo OAuth que nadie ha consentido."""
    assert uses_oauth("https://mcp.interno.example/mcp") is False
    assert uses_oauth(None) is False
    assert template_for_url("https://mcp.interno.example/mcp") is None


def test_a_trailing_slash_does_not_hide_the_template() -> None:
    """El operador puede haber guardado la URL con o sin barra final."""
    assert uses_oauth(_ATLASSIAN_REMOTE_URL + "/") is True


# ---------------------------------------------------------------------------
# El puntero que el dispatch emite
# ---------------------------------------------------------------------------
def test_the_oauth_ref_points_at_the_tenant_and_project_entry() -> None:
    """La frontera de credencial es el TENANT, y la impone la ruta (ADR 0127):
    cada tenant autoriza SU cuenta, sin bot compartido."""
    ref = oauth_vault_path(tenant_id="t1", project_id="p1", server_name="atlassian-remote")
    assert ref == "vault:secret/data/mcp-oauth/t1/p1/atlassian-remote"


def test_the_config_carries_the_oauth_ref_apart_from_auth_ref() -> None:
    """`auth_ref` es el secreto ESTÁTICO que `apply_vault_auth` inyecta en
    cabeceras/env. Meter ahí el estado OAuth haría que se inyectase el blob del
    token como cabecera. Son dos cosas distintas y viajan aparte."""
    config = MCPServerConfig(
        name="atlassian-remote",
        transport="streamable_http",
        url=_ATLASSIAN_REMOTE_URL,
        oauth_ref="vault:secret/data/mcp-oauth/t1/p1/atlassian-remote",
    )
    assert config.auth_ref is None
    assert config.oauth_ref == "vault:secret/data/mcp-oauth/t1/p1/atlassian-remote"


def test_the_config_defaults_to_no_oauth() -> None:
    config = MCPServerConfig(name="local", transport="stdio", command="x")
    assert config.oauth_ref is None


# ---------------------------------------------------------------------------
# El dispatch resuelve el puntero
# ---------------------------------------------------------------------------
def _serialise(servers: list[dict[str, Any]], *, tenant: str, project: str) -> list[dict[str, Any]]:
    from api_server.mcp_oauth_flow import serialise_servers_for_run

    return serialise_servers_for_run(servers, tenant_id=tenant, project_id=project)


def test_dispatch_adds_the_oauth_ref_to_an_oauth_server() -> None:
    out = _serialise(
        [
            {
                "name": "atlassian-remote",
                "transport": "streamable_http",
                "url": _ATLASSIAN_REMOTE_URL,
            }
        ],
        tenant="t1",
        project="p1",
    )
    assert out[0]["oauth_ref"] == "vault:secret/data/mcp-oauth/t1/p1/atlassian-remote"


def test_dispatch_leaves_a_non_oauth_server_untouched() -> None:
    server = {
        "name": "context7",
        "transport": "streamable_http",
        "url": "https://mcp.context7.com/mcp",
    }
    assert _serialise([server], tenant="t1", project="p1") == [server]


def test_dispatch_does_not_overwrite_an_explicit_oauth_ref() -> None:
    """Si algún día el propio config lo trae, manda el config."""
    server = {
        "name": "atlassian-remote",
        "transport": "streamable_http",
        "url": _ATLASSIAN_REMOTE_URL,
        "oauth_ref": "vault:secret/data/custom",
    }
    assert _serialise([server], tenant="t1", project="p1")[0]["oauth_ref"] == (
        "vault:secret/data/custom"
    )


def test_dispatch_copies_rather_than_mutating_the_project_row() -> None:
    """`project.mcp_servers` es JSONB de SQLAlchemy: mutarlo in-place marcaría
    la fila como sucia y escribiría el puntero en la BD."""
    server = {
        "name": "atlassian-remote",
        "transport": "streamable_http",
        "url": _ATLASSIAN_REMOTE_URL,
    }
    _serialise([server], tenant="t1", project="p1")
    assert "oauth_ref" not in server


# ---------------------------------------------------------------------------
# El runtime lo usa
# ---------------------------------------------------------------------------
def test_the_runtime_config_mapper_reads_the_oauth_ref() -> None:
    from agent_runtime.__main__ import _to_mcp_config

    config = _to_mcp_config(
        {
            "name": "atlassian-remote",
            "transport": "streamable_http",
            "url": _ATLASSIAN_REMOTE_URL,
            "oauth_ref": "vault:secret/data/mcp-oauth/t1/p1/atlassian-remote",
        }
    )
    assert config.oauth_ref == "vault:secret/data/mcp-oauth/t1/p1/atlassian-remote"


class _FakeApi:
    """Cliente del API interno de mentira: registra qué se le pide."""

    def __init__(self, tokens: list[str] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._tokens = tokens or ["tok-1", "tok-2"]

    def mcp_oauth_token(self, *, server: str, refresh: bool = False) -> dict[str, Any]:
        self.calls.append({"server": server, "refresh": refresh})
        return {"access_token": self._tokens[len(self.calls) - 1], "token_type": "Bearer"}


def _oauth_config() -> MCPServerConfig:
    return MCPServerConfig(
        name="atlassian-remote",
        transport="streamable_http",
        url=_ATLASSIAN_REMOTE_URL,
        oauth_ref="vault:secret/data/mcp-oauth/t1/p1/atlassian-remote",
    )


def test_the_sandbox_asks_the_platform_by_server_NAME_not_by_vault_path() -> None:
    """ADR 0131 opción C. Mandar el `oauth_ref` ya montado habría convertido la
    ruta de Vault en una capacidad: cambiando una cadena, el sandbox pediría la
    credencial de otro proyecto. Se manda el nombre y la ruta la compone el
    servidor con el tenant del token y el proyecto del run."""
    from agent_runtime import mcp_tools

    api = _FakeApi()
    auth = mcp_tools.build_oauth_auth(_oauth_config(), api=api)
    request = httpx.Request("POST", "https://mcp.atlassian.com/v1/sse")

    flow = auth.auth_flow(request)
    sent = next(flow)

    assert sent.headers["Authorization"] == "Bearer tok-1"
    assert api.calls == [{"server": "atlassian-remote", "refresh": False}]
    assert all("vault:" not in str(c) for c in api.calls), "la ruta de Vault no sale del servidor"


def test_a_401_refreshes_once_and_retries() -> None:
    """El refresco lo dispara un 401 REAL del remoto, no un reloj: la entrada de
    Vault no guarda vencimiento absoluto y adivinarlo falla con la deriva
    horaria y con los servidores que revocan antes de tiempo."""
    from agent_runtime import mcp_tools

    api = _FakeApi()
    auth = mcp_tools.build_oauth_auth(_oauth_config(), api=api)
    request = httpx.Request("POST", "https://mcp.atlassian.com/v1/sse")

    flow = auth.auth_flow(request)
    next(flow)
    retried = flow.send(httpx.Response(401, request=request))

    assert retried.headers["Authorization"] == "Bearer tok-2"
    assert api.calls[-1] == {"server": "atlassian-remote", "refresh": True}


def test_a_second_401_is_not_retried_forever() -> None:
    """Si el token recién emitido tampoco vale, reintentar es girar en vacío: el
    401 se propaga y el fallo del servidor llega al preámbulo del agente."""
    from agent_runtime import mcp_tools

    auth = mcp_tools.build_oauth_auth(_oauth_config(), api=_FakeApi())
    request = httpx.Request("POST", "https://mcp.atlassian.com/v1/sse")

    flow = auth.auth_flow(request)
    next(flow)
    flow.send(httpx.Response(401, request=request))
    with pytest.raises(StopIteration):
        flow.send(httpx.Response(401, request=request))


def test_the_token_is_reused_within_the_session() -> None:
    """Una petición al API interno por sesión, no por llamada a tool."""
    from agent_runtime import mcp_tools

    api = _FakeApi()
    auth = mcp_tools.build_oauth_auth(_oauth_config(), api=api)
    for _ in range(3):
        request = httpx.Request("POST", "https://mcp.atlassian.com/v1/sse")
        next(auth.auth_flow(request))
    assert len(api.calls) == 1


def test_httpx_must_read_the_body_before_resuming_the_flow() -> None:
    """`requires_response_body` no es decorativo: sin él httpx devolvería el
    control con el stream a medias y el reintento del 401 se rompería."""
    from agent_runtime import mcp_tools

    assert mcp_tools.MediatedBearerAuth.requires_response_body is True


def test_no_oauth_ref_means_no_auth() -> None:
    """Regresión: un servidor sin OAuth sigue conectando exactamente igual."""
    from agent_runtime import mcp_tools

    config = MCPServerConfig(name="local", transport="stdio", command="x")
    assert mcp_tools.build_oauth_auth(config, api=_FakeApi()) is None


def test_oauth_without_an_internal_api_fails_loud() -> None:
    """Sin API interno no hay token: conectar igualmente daría un 401 opaco del
    servidor remoto en vez de decir qué falta."""
    from agent_runtime import mcp_tools
    from shared_mcp import MCPAuthError

    with pytest.raises(MCPAuthError, match="internal API"):
        mcp_tools.build_oauth_auth(_oauth_config(), api=None)
