"""Probar y ejecutar recorren el mismo camino (`task_mk_02`, ADR 0165 D9, D11, A2).

Lo que decide `api_server.egress.mcp_discovery`, sin red:

- QUÉ servidores salen por el egress-proxy: los HTTP con host externo. Un `stdio`
  no tiene host; un host sin punto es un servicio del compose y se exime igual
  que hace el worker por `NO_PROXY` (la regla es UNA, `shared_domain.mcp_hosts`).
- QUÉ significa un fallo, mirando el TIPO de la causa raíz y no el texto: el
  proxy rechazando el CONNECT (`403 Filtered`) es «el host no está en la
  allowlist»; el proxy rechazando al cliente o no contestando es «el api-server
  no puede usar el proxy»; un 401/403 del servidor de origen sigue siendo la
  credencial. Confundir el primero con el tercero manda al operador a rotar un
  token sano (tabla de riesgos del ADR).
- QUÉ avisar al GUARDAR un servidor cuyo host aún no está permitido (D11):
  200 con aviso tipado, nunca un 4xx.
"""

from __future__ import annotations

import httpx
import pytest
from api_server.egress.mcp_discovery import (
    EGRESS_BLOCKED,
    EGRESS_PROXY_UNAVAILABLE,
    EgressProxyNotConfiguredError,
    classify_discovery_failure,
    egress_client_factory_for,
    egress_warnings_for_servers,
    server_host,
)
from shared_mcp import MCPAuthError, MCPServerConfig, MCPTransportError

PROXY = "http://egress-proxy:8888"


def _remote(url: str = "https://mcp.atlassian.com/v1/mcp") -> MCPServerConfig:
    return MCPServerConfig(name="atlassian", transport="streamable_http", url=url)


def _internal() -> MCPServerConfig:
    return MCPServerConfig(
        name="docling", transport="streamable_http", url="http://docling:5001/mcp"
    )


def _stdio() -> MCPServerConfig:
    return MCPServerConfig(name="files", transport="stdio", command="mcp-files")


# ---------------------------------------------------------------------------
# Quién sale por el proxy
# ---------------------------------------------------------------------------
def test_server_host_lee_el_hostname_de_la_url() -> None:
    assert server_host(_remote()) == "mcp.atlassian.com"
    assert server_host(_internal()) == "docling"
    assert server_host(_stdio()) is None


def test_un_mcp_remoto_sale_por_el_proxy() -> None:
    factory = egress_client_factory_for(_remote(), PROXY)
    assert factory is not None


def test_un_mcp_interno_no_sale_por_el_proxy() -> None:
    """Misma exención que `workers/execution.py` aplica por NO_PROXY: si probar y
    ejecutar discreparan en qué es interno, volvería la asimetría (D9)."""
    assert egress_client_factory_for(_internal(), PROXY) is None


def test_stdio_no_tiene_host_y_queda_fuera() -> None:
    assert egress_client_factory_for(_stdio(), PROXY) is None


def test_sin_proxy_configurado_un_remoto_no_sale_directo() -> None:
    """Nunca «directo como fallback»: eso es exactamente el falso verde de MK-15."""
    with pytest.raises(EgressProxyNotConfiguredError):
        egress_client_factory_for(_remote(), "")


def test_sin_proxy_configurado_un_interno_sigue_funcionando() -> None:
    assert egress_client_factory_for(_internal(), "") is None


# ---------------------------------------------------------------------------
# Qué significa el fallo (D9.2 + A2: por tipo, no por texto)
# ---------------------------------------------------------------------------
def _wrapped(cause: BaseException) -> MCPTransportError:
    """Como lo produce `MCPClient.connect`: envuelto y con la hoja en un grupo."""
    err = MCPTransportError("failed to open 'streamable_http' transport for server 'atlassian'")
    err.__cause__ = BaseExceptionGroup("unhandled", [cause])
    return err


def test_un_403_filtered_del_proxy_es_host_fuera_de_la_allowlist() -> None:
    failure = classify_discovery_failure(
        _wrapped(httpx.ProxyError("403 Filtered")), config=_remote(), proxied=True
    )
    assert failure.status_code == 422
    assert failure.error_code == EGRESS_BLOCKED
    # Accionable: dice el host, dónde se pide y que hay un paso de aplicación.
    assert "mcp.atlassian.com" in failure.message
    assert "Egress" in failure.message
    assert "egress-mcp-allowlist" in failure.message


def test_un_403_del_proxy_al_cliente_no_es_la_allowlist() -> None:
    """`Unauthorized connection from …` (ACL `Allow` de tinyproxy, D4): el síntoma
    para el cliente es otro 403, pero el arreglo es otro."""
    failure = classify_discovery_failure(
        _wrapped(httpx.ProxyError("403 Access denied")), config=_remote(), proxied=True
    )
    assert failure.status_code == 502
    assert failure.error_code == EGRESS_PROXY_UNAVAILABLE
    assert "Allow" in failure.message


def test_no_poder_conectar_con_el_proxy_es_proxy_no_disponible() -> None:
    failure = classify_discovery_failure(
        _wrapped(httpx.ConnectError("All connection attempts failed")),
        config=_remote(),
        proxied=True,
    )
    assert failure.status_code == 502
    assert failure.error_code == EGRESS_PROXY_UNAVAILABLE
    assert "egress-proxy" in failure.message


def test_el_mismo_connect_error_sin_proxy_es_transporte() -> None:
    """Un MCP interno que no contesta no tiene nada que ver con el egress."""
    failure = classify_discovery_failure(
        _wrapped(httpx.ConnectError("All connection attempts failed")),
        config=_internal(),
        proxied=False,
    )
    assert failure.status_code == 502
    assert failure.error_code == "TRANSPORT_ERROR"


def test_la_credencial_rechazada_por_el_origen_sigue_siendo_auth() -> None:
    failure = classify_discovery_failure(
        MCPAuthError("server 'atlassian' rejected our credentials"),
        config=_remote(),
        proxied=True,
    )
    assert failure.status_code == 401
    assert failure.error_code == "AUTH_ERROR"


def test_un_403_http_del_origen_no_se_confunde_con_el_proxy() -> None:
    """Con el túnel ya abierto, un 403 llega como respuesta HTTP del ORIGEN
    (`HTTPStatusError`), no como `ProxyError`: es la credencial, no la allowlist."""
    request = httpx.Request("POST", "https://mcp.atlassian.com/v1/mcp")
    response = httpx.Response(403, request=request)
    failure = classify_discovery_failure(
        _wrapped(httpx.HTTPStatusError("403 Forbidden", request=request, response=response)),
        config=_remote(),
        proxied=True,
    )
    assert failure.status_code == 401
    assert failure.error_code == "AUTH_ERROR"


def test_un_transporte_sin_causa_conocida_es_transporte() -> None:
    failure = classify_discovery_failure(
        MCPTransportError("initialize() failed: boom"), config=_remote(), proxied=True
    )
    assert failure.status_code == 502
    assert failure.error_code == "TRANSPORT_ERROR"


def test_cualquier_otra_cosa_es_desconocido() -> None:
    failure = classify_discovery_failure(RuntimeError("?"), config=_remote(), proxied=True)
    assert failure.status_code == 502
    assert failure.error_code == "UNKNOWN_ERROR"
    assert "RuntimeError" in failure.message


# ---------------------------------------------------------------------------
# El aviso al guardar (D11)
# ---------------------------------------------------------------------------
def _declared(name: str, url: str | None, transport: str = "streamable_http") -> dict[str, object]:
    return {"name": name, "transport": transport, "url": url, "command": None}


def test_un_host_externo_fuera_de_la_allowlist_avisa() -> None:
    avisos = egress_warnings_for_servers(
        [_declared("atlassian", "https://mcp.atlassian.com/v1/mcp")], allowed_hosts=[]
    )
    assert len(avisos) == 1
    aviso = avisos[0]
    assert aviso["server"] == "atlassian"
    assert aviso["host"] == "mcp.atlassian.com"
    assert aviso["code"] == "EGRESS_HOST_NOT_ALLOWLISTED"
    # Las palabras de D7.2/D11: nunca «permitido», nunca «pendiente de aplicar».
    assert "System Admin" in aviso["message"]
    assert "permitido" not in aviso["message"].lower()


def test_un_host_permitido_no_avisa() -> None:
    assert (
        egress_warnings_for_servers(
            [_declared("atlassian", "https://mcp.atlassian.com/v1/mcp")],
            allowed_hosts=["mcp.atlassian.com"],
        )
        == []
    )


def test_la_comparacion_es_por_host_canonico() -> None:
    """Mayúsculas y punto final en la URL no deben producir un aviso falso."""
    assert (
        egress_warnings_for_servers(
            [_declared("atlassian", "https://MCP.Atlassian.com./v1/mcp")],
            allowed_hosts=["mcp.atlassian.com"],
        )
        == []
    )


def test_internos_y_stdio_nunca_avisan() -> None:
    assert (
        egress_warnings_for_servers(
            [
                _declared("docling", "http://docling:5001/mcp"),
                _declared("files", None, transport="stdio"),
            ],
            allowed_hosts=[],
        )
        == []
    )


def test_una_entrada_ilegible_se_ignora() -> None:
    """El aviso es informativo: una fila rara en JSONB no puede tumbar el GET."""
    assert egress_warnings_for_servers([{"name": "x"}, "no-dict"], allowed_hosts=[]) == []  # type: ignore[list-item]
