"""Salir al MCP remoto POR el egress-proxy (`task_mk_02`, ADR 0165 D9).

El sandbox del agente sale a Internet exclusivamente por el egress-proxy: la red
de agentes es `internal` y `HTTP_PROXY` apunta al tinyproxy con su allowlist. El
api-server, en cambio, no tiene `HTTP_PROXY` —y no debe tenerlo: `httpx` lleva
`trust_env=True` y proxificaría también el tráfico interno (searxng, tts, la API
interna)—, así que hasta el ADR 0165 «Probar conexión» salía directo a Internet y
podía dar verde sobre un run que iba a morir con `403 Filtered`.

La salida es esta factoría. Los dos transportes HTTP del SDK (`sse_client`,
`streamablehttp_client`) aceptan `httpx_client_factory` con la firma
`(headers, timeout, auth) -> httpx.AsyncClient`; ésta construye el cliente con
`proxy=` y nada más. El resto del contrato —seguir redirecciones, timeout por
defecto— se copia del `create_mcp_http_client` del SDK para que la única
diferencia entre probar directo y probar por el proxy sea el proxy.
"""

from __future__ import annotations

from typing import Protocol

import httpx


class HttpxClientFactory(Protocol):
    """La firma que el SDK MCP espera en `httpx_client_factory`."""

    def __call__(
        self,
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient: ...


#: El timeout por defecto del SDK (`create_mcp_http_client`), copiado y no
#: importado: es un detalle privado (`mcp.shared._httpx_utils`) y un cambio suyo
#: silencioso alteraría el comportamiento de probar por el proxy sin que nadie
#: lo hubiera decidido aquí.
_DEFAULT_TIMEOUT = httpx.Timeout(30.0)


def proxied_httpx_client_factory(proxy_url: str) -> HttpxClientFactory:
    """Una factoría de `httpx.AsyncClient` que sale SÓLO por ``proxy_url``.

    Sin URL no hay «salir directo como fallback»: la regla del córtex (ADR 0067)
    es la misma aquí, y el fallo tiene que ser explícito para que el operador
    configure `API_SERVER_EGRESS_PROXY_URL` en vez de descubrir en producción que
    la prueba y el run recorren caminos distintos.
    """
    if not proxy_url or not proxy_url.strip():
        raise ValueError(
            "proxied_httpx_client_factory: falta la URL del egress-proxy; el cliente MCP "
            "no sale a Internet sin proxy"
        )
    proxy = proxy_url.strip()

    def factory(
        headers: dict[str, str] | None = None,
        timeout: httpx.Timeout | None = None,
        auth: httpx.Auth | None = None,
    ) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            proxy=proxy,
            headers=headers,
            timeout=timeout if timeout is not None else _DEFAULT_TIMEOUT,
            auth=auth,
            follow_redirects=True,
        )

    return factory


__all__ = ["HttpxClientFactory", "proxied_httpx_client_factory"]
