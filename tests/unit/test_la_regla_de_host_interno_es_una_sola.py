"""Qué es un «host MCP interno» se decide en UN solo sitio (`task_mk_02`, ADR 0165 D9).

El worker exime de proxy los hosts sin punto —nombres de servicio del compose— y
deja salir por el egress-proxy todo lo que tenga FQDN. El api-server va a tomar
la MISMA decisión cuando «Probar conexión» pase a salir por el proxy: si probar y
ejecutar discrepan en qué consideran interno, vuelve la asimetría que el ADR 0165
existe para cerrar, y encima en su forma más difícil de ver — un servidor que
prueba por un camino y ejecuta por otro.

Por eso la regla vive en `shared_domain`, que es el único paquete que importan
los dos lados: `shared_mcp` no vale porque `apps/workers` no lo declara en su
`pyproject`.
"""

from __future__ import annotations

import pytest
from shared_domain.mcp_hosts import internal_mcp_hosts, is_internal_mcp_host

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "host",
    ["docling", "ollama", "api-server", "searxng", "vault"],
)
def test_un_nombre_de_servicio_del_compose_es_interno(host: str) -> None:
    assert is_internal_mcp_host(host) is True


@pytest.mark.parametrize(
    "host",
    ["mcp.atlassian.com", "api.github.com", "localhost.localdomain", "10.0.0.5", "[::1]"],
)
def test_cualquier_cosa_con_punto_o_con_forma_de_ip_sale_por_el_proxy(host: str) -> None:
    assert is_internal_mcp_host(host) is False


@pytest.mark.parametrize("host", ["", "   ", None])
def test_lo_vacio_no_es_interno(host: str | None) -> None:
    assert is_internal_mcp_host(host) is False


def test_de_una_lista_de_urls_salen_los_hosts_internos_ordenados_y_sin_repetir() -> None:
    urls = [
        "http://docling:5001/mcp",
        "https://mcp.atlassian.com/v1/mcp",
        "http://docling:5001/otra",  # el mismo host, dos veces
        "http://ollama:11434/mcp",
        "",  # una entrada sin url no rompe nada
    ]

    assert internal_mcp_hosts(urls) == ["docling", "ollama"]


def test_una_url_ilegible_no_rompe_la_lista() -> None:
    assert internal_mcp_hosts(["esto no es una url", "http://docling:5001/mcp"]) == ["docling"]


def test_el_worker_usa_esta_misma_funcion_y_no_una_copia() -> None:
    """El punto 5 de `verificar-antes-de-implementar`: un mecanismo compartido que
    nadie comparte no está compartido. Si alguien reintroduce la copia en el
    worker, este test lo dice."""
    import inspect

    from workers.execution import _internal_mcp_hosts

    fuente = inspect.getsource(_internal_mcp_hosts)
    assert "internal_mcp_hosts" in fuente, "el worker dejó de delegar en la regla compartida"
    assert '"." not in host' not in fuente, "el worker volvió a llevar su propia copia de la regla"
