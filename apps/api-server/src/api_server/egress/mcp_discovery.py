"""Descubrir un MCP desde el api-server por el MISMO camino que el run (`task_mk_02`, ADR 0165 D9).

Hasta el ADR 0165, `POST /projects/{id}/mcp/test-connection` e `import-tools`
llamaban a `discover_tools` desde el api-server, que vive en `agentic-net` y no
tiene `HTTP_PROXY`: la prueba salía directa a Internet. El sandbox, en cambio,
sale obligatoriamente por el egress-proxy y su allowlist. O sea: «Probar
conexión» podía salir en verde sobre un run que iba a morir con `403 Filtered`
(MK-15). Este módulo cierra esa asimetría en tres decisiones, todas puras y sin
red, para que el router sólo tenga que llamarlas:

1. **Quién sale por el proxy** (:func:`egress_client_factory_for`): los
   servidores HTTP con host externo. Un `stdio` no tiene host. Un host sin
   punto es un servicio del compose y se exime igual que hace el worker por
   `NO_PROXY` — con la MISMA función, `shared_domain.mcp_hosts`, porque dos
   copias de esa regla son la asimetría en su forma más difícil de ver.
2. **Qué significa el fallo** (:func:`classify_discovery_failure`): por el TIPO
   de la causa raíz, no por el texto (addendum A2). `httpx.ProxyError` en el
   CONNECT con `Filtered` ⇒ el host no está en la allowlist; otro rechazo del
   proxy o no poder conectar con él ⇒ el api-server no puede usar el proxy
   (D4); 401/403 del ORIGEN ⇒ la credencial. Confundir el primero con el
   tercero manda al operador a rotar un token que está bien.
3. **Qué avisar al guardar** (:func:`egress_warnings_for_servers`, D11): un
   servidor cuyo host aún no está permitido se guarda con 200 y un aviso
   tipado. Bloquearlo dejaría al `tenant_admin` sin poder declarar justo el
   artefacto con el que pide la apertura.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final, Literal
from urllib.parse import urlparse

import httpx
from shared_domain.mcp_hosts import is_internal_mcp_host
from shared_mcp import (
    HttpxClientFactory,
    MCPAuthError,
    MCPServerConfig,
    MCPTransportError,
    proxied_httpx_client_factory,
    transport_root_cause,
)

from api_server.egress.mcp_allowlist import InvalidMcpHostError, normalise_host

__all__ = [
    "EGRESS_BLOCKED",
    "EGRESS_HOST_NOT_ALLOWLISTED",
    "EGRESS_PROXY_UNAVAILABLE",
    "RUNBOOK",
    "DiscoveryFailure",
    "EgressProxyNotConfiguredError",
    "classify_discovery_failure",
    "egress_client_factory_for",
    "egress_warnings_for_servers",
    "server_host",
]

#: Códigos tipados que el panel puede ramificar. Los cuatro históricos
#: (`AUTH_ERROR`, `TRANSPORT_ERROR`, `CONFIG_ERROR`, `UNKNOWN_ERROR`) siguen
#: existiendo; estos dos son los que D9 añade y que antes se veían como un
#: `TRANSPORT_ERROR` con un `403 Filtered` crudo dentro.
EGRESS_BLOCKED: Final = "EGRESS_BLOCKED"
EGRESS_PROXY_UNAVAILABLE: Final = "EGRESS_PROXY_UNAVAILABLE"
#: El código del aviso de guardado (D11). No es un error: viaja en un 200.
EGRESS_HOST_NOT_ALLOWLISTED: Final = "EGRESS_HOST_NOT_ALLOWLISTED"

RUNBOOK = "docs/06-runbooks/egress-mcp-allowlist.md"

DiscoveryErrorCode = Literal[
    "AUTH_ERROR",
    "TRANSPORT_ERROR",
    "UNKNOWN_ERROR",
    "EGRESS_BLOCKED",
    "EGRESS_PROXY_UNAVAILABLE",
    # ADR 0166 (D5, L1): los dos que añade el import de tools.
    "OAUTH_NOT_CONNECTED",
    "TOO_MANY_TOOLS",
]


class EgressProxyNotConfiguredError(RuntimeError):
    """Un MCP remoto pide salir y no hay `API_SERVER_EGRESS_PROXY_URL`.

    No se degrada a «directo»: sería reintroducir a mano el falso verde que
    este módulo existe para cerrar.
    """


@dataclass(frozen=True)
class DiscoveryFailure:
    """Lo que el router convierte en `HTTPException` + `McpTestConnectionError`."""

    status_code: int
    error_code: DiscoveryErrorCode
    message: str


def server_host(config: MCPServerConfig) -> str | None:
    """El hostname de la `url` del servidor, o ``None`` si no la tiene (stdio)."""
    if config.transport == "stdio" or not config.url:
        return None
    try:
        return urlparse(config.url).hostname
    except ValueError:
        return None


def egress_client_factory_for(
    config: MCPServerConfig, proxy_url: str | None
) -> HttpxClientFactory | None:
    """La factoría httpx con la que descubrir ``config``: proxificada si el host es
    externo, ``None`` (cliente directo del SDK) si es interno o no hay host.

    Levanta :class:`EgressProxyNotConfiguredError` si hace falta el proxy y no
    está configurado.
    """
    host = server_host(config)
    if host is None or is_internal_mcp_host(host):
        return None
    if not proxy_url or not proxy_url.strip():
        raise EgressProxyNotConfiguredError(
            f"el servidor MCP {config.name!r} apunta al host externo {host!r} y el api-server "
            "no tiene egress-proxy configurado (API_SERVER_EGRESS_PROXY_URL): la prueba no "
            "sale a Internet sin proxy, igual que el sandbox"
        )
    return proxied_httpx_client_factory(proxy_url)


def _proxy_said_filtered(exc: httpx.ProxyError) -> bool:
    """¿El proxy rechazó el DESTINO? tinyproxy responde `403 Filtered` a un CONNECT
    hacia un host fuera del filtro, y `403 Access denied` cuando el que no le
    gusta es el cliente (ACL `Allow`). La frase de estado es lo que httpx guarda
    en el mensaje del `ProxyError`; medido en el runbook §4.2."""
    return "filtered" in str(exc).lower()


def _origin_rejected_credentials(cause: BaseException) -> bool:
    """Un 401/403 con el túnel ya abierto llega como respuesta HTTP del origen."""
    if isinstance(cause, httpx.HTTPStatusError):
        return cause.response.status_code in (401, 403)
    return False


def _classify_proxy_failure(cause: BaseException | None, host: str) -> DiscoveryFailure | None:
    """Los fallos que SÓLO existen porque el intento salió por el egress-proxy.

    ``None`` si la causa no es del proxy: el llamante sigue con el resto de la
    clasificación (credencial del origen, transporte, desconocido).
    """
    if isinstance(cause, httpx.ProxyError):
        if _proxy_said_filtered(cause):
            return DiscoveryFailure(
                422,
                EGRESS_BLOCKED,
                f"El egress-proxy rechazó la conexión: `{host}` no está en la allowlist de "
                "hosts MCP remotos de la plataforma. Pídele a un System Admin que lo añada en "
                "Sistema → Egress (`egress.mcp_allowed_hosts`) y que aplique el cambio al proxy "
                f"siguiendo `{RUNBOOK}`. Hasta entonces, las ejecuciones con este servidor "
                "fallarán igual que esta prueba.",
            )
        return DiscoveryFailure(
            502,
            EGRESS_PROXY_UNAVAILABLE,
            f"El egress-proxy rechazó al api-server como cliente ({cause}). No es la allowlist "
            "de hosts: revisa que la IP del api-server caiga en las redes `Allow` de "
            f"tinyproxy (runbook `{RUNBOOK}` §5).",
        )
    if isinstance(cause, httpx.ConnectError | httpx.ConnectTimeout):
        return DiscoveryFailure(
            502,
            EGRESS_PROXY_UNAVAILABLE,
            f"No se pudo conectar con el egress-proxy ({cause}). La prueba sale por el mismo "
            "proxy que el sandbox, así que hasta que el proxy responda ni la prueba ni las "
            "ejecuciones alcanzarán este servidor. Comprueba `API_SERVER_EGRESS_PROXY_URL` y "
            "`docker compose ps egress-proxy`.",
        )
    return None


def classify_discovery_failure(
    exc: BaseException, *, config: MCPServerConfig, proxied: bool
) -> DiscoveryFailure:
    """Traduce una excepción del descubrimiento a `(status, código, mensaje accionable)`.

    ``proxied`` dice si el intento salió por el egress-proxy: el mismo
    `httpx.ConnectError` es «no llego al proxy» cuando sí, y «el servidor no
    contesta» cuando no.
    """
    host = server_host(config) or config.name

    if isinstance(exc, MCPAuthError):
        return DiscoveryFailure(401, "AUTH_ERROR", str(exc))

    cause = transport_root_cause(exc)

    if proxied:
        del_proxy = _classify_proxy_failure(cause, host)
        if del_proxy is not None:
            return del_proxy

    if cause is not None and _origin_rejected_credentials(cause):
        return DiscoveryFailure(
            401,
            "AUTH_ERROR",
            f"El servidor MCP `{host}` rechazó la credencial ({cause}). El transporte llegó: "
            "esto no es la allowlist de egress.",
        )

    if isinstance(exc, MCPTransportError):
        return DiscoveryFailure(502, "TRANSPORT_ERROR", str(exc))

    return DiscoveryFailure(502, "UNKNOWN_ERROR", f"{type(exc).__name__}: {exc}")


def egress_warnings_for_servers(
    servers: Iterable[Any], *, allowed_hosts: Iterable[str]
) -> list[dict[str, str]]:
    """Los avisos D11 para las entradas de `project.mcp_servers` cuyo host externo
    aún no está en la allowlist de plataforma.

    Puro e indulgente a propósito: es informativo y viaja en respuestas 200, así
    que una fila rara en el JSONB se ignora en vez de tumbar el GET. La
    comparación es por host canónico (minúsculas, sin punto final), que es como
    el ajuste los guarda.
    """
    permitidos: set[str] = set()
    for h in allowed_hosts:
        try:
            permitidos.add(normalise_host(h))
        except InvalidMcpHostError:
            continue

    avisos: list[dict[str, str]] = []
    for raw in servers:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        url = raw.get("url")
        if raw.get("transport") == "stdio" or not isinstance(name, str) or not isinstance(url, str):
            continue
        try:
            host = urlparse(url).hostname
        except ValueError:
            continue
        if not host or is_internal_mcp_host(host):
            continue
        try:
            canonico = normalise_host(host)
        except InvalidMcpHostError:
            # Una URL con host inválido no llega a guardarse (fail-closed de forma,
            # D11); si está aquí es una fila anterior a esa regla. No es asunto
            # de la allowlist y no se avisa por ella.
            continue
        if canonico in permitidos:
            continue
        avisos.append(
            {
                "server": name,
                "host": canonico,
                "code": EGRESS_HOST_NOT_ALLOWLISTED,
                "message": (
                    f"Guardado. El host `{canonico}` no está hoy en la allowlist de egress de la "
                    "plataforma; pídele su apertura a un System Admin (Sistema → Egress). Hasta "
                    "entonces, las ejecuciones y las pruebas con este servidor fallarán."
                ),
            }
        )
    return avisos
