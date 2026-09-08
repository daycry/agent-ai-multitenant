"""El sondeo empírico de la allowlist (`task_mk_02`, ADR 0165 D7.3).

Ni el ajuste ni el fichero pueden decir «permitido»: el ajuste es la intención y
el `filter.txt` de la imagen en marcha no se ve desde el api-server. El único que
puede responder es el proxy, y se le pregunta abriendo un CONNECT a través de él
por cada host. Lo que este fichero fija, sin red:

- Cómo se lee cada desenlace, con la tabla medida del runbook §4.2: respuesta del
  origen ⇒ permitido (el túnel abrió; lo de después no es asunto del proxy);
  `ProxyError` con `Filtered` ⇒ bloqueado; otro rechazo del proxy ⇒ error
  (ACL de cliente, D4); no llegar al proxy ⇒ error; un timeout de LECTURA con el
  túnel ya abierto ⇒ permitido.
- Que el sondeo sale por el proxy y no directo, que respeta el tope de
  concurrencia y que un host inválido no llega a sondearse.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from api_server.egress.probe import (
    ProbeResult,
    classify_probe_outcome,
    probe_hosts,
)

PROXY = "http://egress-proxy:8888"


def _request(host: str) -> httpx.Request:
    return httpx.Request("HEAD", f"https://{host}/")


# ---------------------------------------------------------------------------
# La lectura de cada desenlace
# ---------------------------------------------------------------------------
def test_una_respuesta_del_origen_es_permitido_sea_cual_sea_el_status() -> None:
    """`connect=200` y luego un 404 del origen: el proxy dejó pasar."""
    r = classify_probe_outcome("mcp.atlassian.com", httpx.Response(404, request=_request("x")))
    assert r.verdict == "permitido"


def test_un_403_filtered_del_proxy_es_bloqueado() -> None:
    r = classify_probe_outcome("mcp.atlassian.com", httpx.ProxyError("403 Filtered"))
    assert r.verdict == "bloqueado"
    assert "filter" in r.detail.lower()


def test_otro_rechazo_del_proxy_es_error_y_no_bloqueado() -> None:
    """`Unauthorized connection from …` es la ACL `Allow` de cliente (D4): decir
    «bloqueado» mandaría al operador a revisar la allowlist equivocada."""
    r = classify_probe_outcome("mcp.atlassian.com", httpx.ProxyError("403 Access denied"))
    assert r.verdict == "error"
    assert "Allow" in r.detail


def test_no_llegar_al_proxy_es_error() -> None:
    r = classify_probe_outcome("mcp.atlassian.com", httpx.ConnectError("connection refused"))
    assert r.verdict == "error"
    assert "egress-proxy" in r.detail


def test_un_connect_timeout_es_error() -> None:
    r = classify_probe_outcome("mcp.atlassian.com", httpx.ConnectTimeout("timed out"))
    assert r.verdict == "error"


def test_un_read_timeout_con_el_tunel_abierto_es_permitido() -> None:
    """Runbook §4.2, última fila: `connect=200` y luego timeout ⇒ el proxy dejó
    pasar y el fallo está más allá."""
    r = classify_probe_outcome("mcp.atlassian.com", httpx.ReadTimeout("timed out"))
    assert r.verdict == "permitido"
    assert "origen" in r.detail.lower()


def test_cualquier_otra_excepcion_es_error_con_su_tipo() -> None:
    r = classify_probe_outcome("mcp.atlassian.com", RuntimeError("?"))
    assert r.verdict == "error"
    assert "RuntimeError" in r.detail


def test_el_resultado_lleva_el_host_canonico() -> None:
    r = classify_probe_outcome("mcp.atlassian.com", httpx.ProxyError("403 Filtered"))
    assert r.host == "mcp.atlassian.com"


# ---------------------------------------------------------------------------
# El sondeo en conjunto
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sondea_cada_host_por_el_proxy_y_devuelve_en_orden() -> None:
    seen: list[tuple[str, str]] = []

    async def opener(host: str, proxy_url: str, timeout_s: float) -> httpx.Response | BaseException:
        seen.append((host, proxy_url))
        if host == "b.example.com":
            return httpx.ProxyError("403 Filtered")
        return httpx.Response(200, request=_request(host))

    results = await probe_hosts(["b.example.com", "a.example.com"], proxy_url=PROXY, opener=opener)
    assert [r.host for r in results] == ["b.example.com", "a.example.com"]
    assert [r.verdict for r in results] == ["bloqueado", "permitido"]
    assert all(proxy == PROXY for _, proxy in seen)


@pytest.mark.asyncio
async def test_sin_proxy_configurado_no_se_sondea_nada() -> None:
    """Sondear directo diría «permitido» de todo: sería el falso verde en su forma
    más pura. Sin proxy, cada host sale como error diciendo por qué."""
    called = False

    async def opener(host: str, proxy_url: str, timeout_s: float) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, request=_request(host))

    results = await probe_hosts(["a.example.com"], proxy_url="", opener=opener)
    assert called is False
    assert results == [
        ProbeResult(
            host="a.example.com",
            verdict="error",
            detail=results[0].detail,
        )
    ]
    assert "API_SERVER_EGRESS_PROXY_URL" in results[0].detail


@pytest.mark.asyncio
async def test_la_concurrencia_esta_acotada() -> None:
    """Hasta 100 hosts (tope del ajuste) no pueden salir de golpe: el proxy es
    compartido con los sandboxes."""
    en_vuelo = 0
    pico = 0

    async def opener(host: str, proxy_url: str, timeout_s: float) -> httpx.Response:
        nonlocal en_vuelo, pico
        en_vuelo += 1
        pico = max(pico, en_vuelo)
        await asyncio.sleep(0.01)
        en_vuelo -= 1
        return httpx.Response(200, request=_request(host))

    hosts = [f"h{i}.example.com" for i in range(20)]
    await probe_hosts(hosts, proxy_url=PROXY, opener=opener, concurrency=4)
    assert pico <= 4


@pytest.mark.asyncio
async def test_un_host_invalido_no_se_sondea() -> None:
    """La entrada viene validada del ajuste, pero el endpoint también acepta hosts
    sueltos en el cuerpo: uno inválido sale como error sin abrir conexión."""
    called: list[str] = []

    async def opener(host: str, proxy_url: str, timeout_s: float) -> httpx.Response:
        called.append(host)
        return httpx.Response(200, request=_request(host))

    results = await probe_hosts(["10.0.0.5", "ok.example.com"], proxy_url=PROXY, opener=opener)
    assert called == ["ok.example.com"]
    assert results[0].verdict == "error"
    assert "IP literal" in results[0].detail
    assert results[1].verdict == "permitido"


@pytest.mark.asyncio
async def test_una_excepcion_del_opener_se_clasifica_no_se_propaga() -> None:
    async def opener(host: str, proxy_url: str, timeout_s: float) -> httpx.Response:
        raise httpx.ConnectError("boom")

    results = await probe_hosts(["a.example.com"], proxy_url=PROXY, opener=opener)
    assert results[0].verdict == "error"
