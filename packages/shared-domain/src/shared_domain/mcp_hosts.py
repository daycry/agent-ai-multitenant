"""Qué es un «host MCP interno», en un solo sitio (`task_mk_02`, ADR 0165 D9).

Un servidor MCP declarado en `projects.mcp_servers` puede vivir dentro del
compose (`http://docling:5001/mcp`) o fuera (`https://mcp.atlassian.com/v1/mcp`).
La distinción no es cosmética: el sandbox recibe `HTTP_PROXY` apuntando al
egress-proxy y la red de agentes es `internal`, así que **todo lo que tenga FQDN
sale obligatoriamente por el proxy** y sólo pasa si su host está en la allowlist;
los nombres de servicio del compose se eximen por `NO_PROXY` porque el proxy vive
en otra red y no sabría alcanzarlos.

La regla —«hostname sin punto es un nombre de servicio del compose»— la aplicaba
sólo el worker. Desde el ADR 0165 la aplica también el api-server, que pasa a
probar la conexión POR el proxy. Si los dos discreparan en qué consideran
interno, volvería la asimetría que ese ADR existe para cerrar, y en su forma más
difícil de diagnosticar: un servidor que se prueba por un camino y se ejecuta por
otro. Por eso hay una función y no dos.

Vive en `shared_domain` y no en `shared_mcp` por una razón prosaica y
comprobable: `apps/workers` no declara `shared-mcp` en su `pyproject`, y sí
`shared-domain`. Un módulo compartido que uno de los dos lados no puede importar
no está compartido.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlparse

__all__ = ["internal_mcp_hosts", "is_internal_mcp_host"]


def is_internal_mcp_host(host: str | None) -> bool:
    """¿``host`` es un servicio del compose (y por tanto NO sale por el proxy)?

    Sin punto = nombre de servicio de Docker Compose. Un FQDN, una IP literal o
    una dirección IPv6 entre corchetes son externos por definición: llevan punto
    o dos puntos, y en cualquier caso el proxy es quien debe decidir sobre ellos.
    """
    if not host:
        return False
    limpio = host.strip().strip("[]")
    if not limpio:
        return False
    return "." not in limpio and ":" not in limpio


def internal_mcp_hosts(urls: Iterable[str | None]) -> list[str]:
    """Los hosts internos de una colección de URLs, ordenados y sin repetir.

    Una URL vacía o ilegible se ignora en silencio a propósito: esta función la
    llaman caminos que no pueden fallar por una entrada mal escrita en la
    configuración de un proyecto — el error de esa entrada se reporta donde se
    valida, no aquí.
    """
    hosts: set[str] = set()
    for url in urls:
        if not url:
            continue
        try:
            host = urlparse(str(url)).hostname
        except ValueError:
            continue
        if is_internal_mcp_host(host):
            assert host is not None  # lo garantiza is_internal_mcp_host
            hosts.add(host)
    return sorted(hosts)
