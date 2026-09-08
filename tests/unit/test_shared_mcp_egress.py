"""`shared_mcp` sale por el egress-proxy cuando se le pide (`task_mk_02`, ADR 0165 D9 + A2).

Tres cosas que el ADR exige y que este fichero clava:

- Una factoría `httpx` que construye clientes con `proxy=` apuntando al egress-proxy,
  con la MISMA firma que `create_mcp_http_client` del SDK, porque es lo que los dos
  transportes HTTP (`sse_client`, `streamablehttp_client`) aceptan en
  `httpx_client_factory`.
- Que `_open_streams` y `discover_tools` la reenvían a esos dos transportes y la
  ignoran en `stdio` (no hay cliente HTTP al que atarla).
- Que un fallo del CONNECT conserva su excepción original como causa: el
  discriminante del api-server tiene que mirar el TIPO (`httpx.ProxyError`) y no
  olfatear cadenas (addendum A2).
"""

from __future__ import annotations

from contextlib import AsyncExitStack

import httpcore
import httpx
import pytest
from shared_mcp import MCPServerConfig, MCPTransportError
from shared_mcp.client import _open_streams
from shared_mcp.egress import proxied_httpx_client_factory
from shared_mcp.exceptions import transport_root_cause

PROXY = "http://egress-proxy:8888"


class _FakeStreamsCM:
    def __init__(self, values: tuple[object, ...]) -> None:
        self._values = values

    async def __aenter__(self) -> tuple[object, ...]:
        return self._values

    async def __aexit__(self, *_a: object) -> bool:
        return False


# ---------------------------------------------------------------------------
# La factoría
# ---------------------------------------------------------------------------
def _proxy_url_of(client: httpx.AsyncClient) -> str | None:
    """La URL del proxy que httpx montó, leída del pool, o None si sale directo."""
    for transport in client._mounts.values():
        pool = getattr(transport, "_pool", None)
        if isinstance(pool, httpcore.AsyncHTTPProxy):
            url = pool._proxy_url
            return f"{url.scheme.decode()}://{url.host.decode()}:{url.port}"
    return None


@pytest.mark.asyncio
async def test_la_factoria_construye_un_cliente_que_sale_por_el_proxy() -> None:
    factory = proxied_httpx_client_factory(PROXY)
    async with factory() as client:
        assert _proxy_url_of(client) == PROXY


@pytest.mark.asyncio
async def test_la_factoria_respeta_la_firma_del_sdk() -> None:
    """`(headers, timeout, auth)`: lo que el SDK pasa a `httpx_client_factory`."""
    factory = proxied_httpx_client_factory(PROXY)
    auth = httpx.BasicAuth("u", "p")
    async with factory(headers={"X-Probe": "1"}, timeout=httpx.Timeout(7.0), auth=auth) as client:
        assert client.headers["X-Probe"] == "1"
        assert client.timeout == httpx.Timeout(7.0)
        assert client.auth is auth
        # El SDK sigue redirecciones en su factoría por defecto; la nuestra no
        # cambia ese contrato por el hecho de salir por el proxy.
        assert client.follow_redirects is True


def test_la_factoria_no_acepta_un_proxy_vacio() -> None:
    """Sin URL de proxy no hay «salir directo como fallback»: es un error."""
    with pytest.raises(ValueError, match="proxy"):
        proxied_httpx_client_factory("")


# ---------------------------------------------------------------------------
# Reenvío a los transportes
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_open_streams_reenvia_la_factoria_a_streamable_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_client(**kwargs: object) -> _FakeStreamsCM:
        captured.update(kwargs)
        return _FakeStreamsCM((object(), object(), object()))

    monkeypatch.setattr("shared_mcp.client.streamablehttp_client", fake_client)
    factory = proxied_httpx_client_factory(PROXY)
    cfg = MCPServerConfig(name="x", transport="streamable_http", url="https://h/mcp")
    async with AsyncExitStack() as stack:
        await _open_streams(stack, cfg, httpx_client_factory=factory)
    assert captured["httpx_client_factory"] is factory


@pytest.mark.asyncio
async def test_open_streams_reenvia_la_factoria_a_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_client(**kwargs: object) -> _FakeStreamsCM:
        captured.update(kwargs)
        return _FakeStreamsCM((object(), object()))

    monkeypatch.setattr("shared_mcp.client.sse_client", fake_client)
    factory = proxied_httpx_client_factory(PROXY)
    cfg = MCPServerConfig(name="x", transport="sse", url="https://h/sse")
    async with AsyncExitStack() as stack:
        await _open_streams(stack, cfg, httpx_client_factory=factory)
    assert captured["httpx_client_factory"] is factory


@pytest.mark.asyncio
async def test_sin_factoria_los_transportes_usan_la_del_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El default del SDK no se pisa con `None`: se omite el kwarg."""
    captured: dict[str, object] = {}

    def fake_client(**kwargs: object) -> _FakeStreamsCM:
        captured.update(kwargs)
        return _FakeStreamsCM((object(), object(), object()))

    monkeypatch.setattr("shared_mcp.client.streamablehttp_client", fake_client)
    cfg = MCPServerConfig(name="x", transport="streamable_http", url="https://h/mcp")
    async with AsyncExitStack() as stack:
        await _open_streams(stack, cfg)
    assert "httpx_client_factory" not in captured


@pytest.mark.asyncio
async def test_stdio_ignora_la_factoria(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_stdio(params: object) -> _FakeStreamsCM:
        captured["params"] = params
        return _FakeStreamsCM((object(), object()))

    monkeypatch.setattr("shared_mcp.client.stdio_client", fake_stdio)
    cfg = MCPServerConfig(name="x", transport="stdio", command="echo")
    async with AsyncExitStack() as stack:
        await _open_streams(stack, cfg, httpx_client_factory=proxied_httpx_client_factory(PROXY))
    assert "params" in captured


@pytest.mark.asyncio
async def test_discover_tools_reenvia_la_factoria(monkeypatch: pytest.MonkeyPatch) -> None:
    """El camino que usan los call sites del api-server es `discover_tools`, no
    `_open_streams`: si el kwarg se perdiera aquí, el resto sería decorativo."""
    from shared_mcp import discovery as discovery_module

    seen: dict[str, object] = {}

    class _FakeSession:
        init_result = None

        async def list_tools(self) -> list[object]:
            return []

    class _FakeConnect:
        def __init__(self, config: object, **kwargs: object) -> None:
            seen.update(kwargs)

        async def __aenter__(self) -> _FakeSession:
            return _FakeSession()

        async def __aexit__(self, *_a: object) -> bool:
            return False

    monkeypatch.setattr(discovery_module.MCPClient, "connect", _FakeConnect)
    factory = proxied_httpx_client_factory(PROXY)
    cfg = MCPServerConfig(name="x", transport="streamable_http", url="https://h/mcp")
    await discovery_module.discover_tools(cfg, httpx_client_factory=factory)
    assert seen["httpx_client_factory"] is factory


# ---------------------------------------------------------------------------
# La causa raíz sobrevive al envoltorio (A2)
# ---------------------------------------------------------------------------
def test_la_causa_raiz_atraviesa_el_from_directo() -> None:
    proxy_error = httpx.ProxyError("403 Filtered")
    try:
        raise MCPTransportError("failed to open transport") from proxy_error
    except MCPTransportError as exc:
        assert transport_root_cause(exc) is proxy_error


def test_la_causa_raiz_atraviesa_un_grupo_de_excepciones() -> None:
    """El SDK levanta `BaseExceptionGroup` desde el TaskGroup de anyio; el cliente
    lo envuelve con `from eg`. La causa útil es la hoja, no el grupo."""
    proxy_error = httpx.ProxyError("403 Filtered")
    grupo = BaseExceptionGroup("unhandled", [proxy_error])
    try:
        raise MCPTransportError("failed to open transport") from grupo
    except MCPTransportError as exc:
        assert transport_root_cause(exc) is proxy_error


def test_la_causa_raiz_atraviesa_grupos_anidados_y_mcp_intermedios() -> None:
    connect_error = httpx.ConnectError("All connection attempts failed")
    interno = MCPTransportError("initialize() failed")
    interno.__cause__ = BaseExceptionGroup("inner", [connect_error])
    grupo = BaseExceptionGroup("outer", [BaseExceptionGroup("mid", [interno])])
    try:
        raise MCPTransportError("MCP session failed") from grupo
    except MCPTransportError as exc:
        assert transport_root_cause(exc) is connect_error


def test_sin_causa_devuelve_none() -> None:
    assert transport_root_cause(MCPTransportError("solo")) is None
