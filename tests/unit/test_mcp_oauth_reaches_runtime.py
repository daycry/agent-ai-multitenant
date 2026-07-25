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


def test_the_provider_is_built_against_the_servers_base_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El SDK deriva los endpoints de discovery/token de la BASE, no del `/mcp`."""
    from agent_runtime import mcp_tools

    seen: dict[str, Any] = {}
    sentinel = object()

    def _fake_build(*, server_url: str, storage: Any, redirect_uri: str, **_: Any) -> object:
        seen.update(server_url=server_url, storage=storage, redirect_uri=redirect_uri)
        return sentinel

    monkeypatch.setattr(mcp_tools, "build_oauth_provider", _fake_build)
    config = MCPServerConfig(
        name="atlassian-remote",
        transport="streamable_http",
        url=_ATLASSIAN_REMOTE_URL,
        oauth_ref="vault:secret/data/mcp-oauth/t1/p1/atlassian-remote",
    )

    assert mcp_tools.build_oauth_auth(config, vault_resolver=object()) is sentinel
    assert seen["server_url"] == "https://mcp.atlassian.com"
    assert seen["storage"]._auth_ref == config.oauth_ref


def test_no_oauth_ref_means_no_auth() -> None:
    """Regresión: un servidor sin OAuth sigue conectando exactamente igual."""
    from agent_runtime import mcp_tools

    config = MCPServerConfig(name="local", transport="stdio", command="x")
    assert mcp_tools.build_oauth_auth(config, vault_resolver=object()) is None


def test_oauth_without_a_vault_resolver_fails_loud() -> None:
    """Sin resolver no hay token: conectar igualmente daría un 401 opaco del
    servidor remoto en vez de decir qué falta."""
    from agent_runtime import mcp_tools
    from shared_mcp import MCPAuthError

    config = MCPServerConfig(
        name="atlassian-remote",
        transport="streamable_http",
        url=_ATLASSIAN_REMOTE_URL,
        oauth_ref="vault:secret/data/mcp-oauth/t1/p1/atlassian-remote",
    )
    with pytest.raises(MCPAuthError, match="Vault"):
        mcp_tools.build_oauth_auth(config, vault_resolver=None)
