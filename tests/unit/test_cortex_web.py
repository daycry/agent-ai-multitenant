"""Unit: web_fetch + web_search del córtex (ADR 0067).

Toda la red se MOCKEA con ``httpx.MockTransport`` — los tests NUNCA salen a
Internet. Se cubre:

  * ``web_fetch``: GET por el proxy, saneo de HTML (strip de scripts/estilos,
    extracción de texto), truncado a ``max_bytes``, anti-SSRF (una URL privada
    lanza ANTES de cualquier GET).
  * ``SearXNGProvider`` / ``BraveSearchProvider``: normalizan la respuesta a
    ``[{title, url, snippet}]``.
  * ``web_search`` + selección de proveedor por setting (searxng default; brave
    sin key → error claro, no crash).
"""

from __future__ import annotations

import httpx
import pytest
from api_server.cortex.web import (
    BraveSearchProvider,
    SearXNGProvider,
    WebSearchError,
    select_web_search_provider,
    web_fetch,
    web_search,
)
from api_server.cortex.web_safety import UnsafeUrlError

pytestmark = pytest.mark.unit

# Resolver inyectable que mapea todo a una IP pública (no toca DNS real).
_PUBLIC = lambda host, port: ["8.8.8.8"]  # noqa: E731


def _client(handler) -> httpx.AsyncClient:
    """Un AsyncClient con un MockTransport que despacha cada request a ``handler``."""
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ===========================================================================
# web_fetch
# ===========================================================================
@pytest.mark.asyncio
async def test_web_fetch_sanitizes_html_and_extracts_text() -> None:
    html = (
        "<html><head><title>Hello Title</title>"
        "<script>alert('xss')</script><style>.x{}</style></head>"
        "<body><h1>Heading</h1><p>Some <b>bold</b> text.</p>"
        "<script>steal()</script></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=html)

    async with _client(handler) as c:
        out = await web_fetch("https://example.com/page", client=c, resolver=_PUBLIC)

    assert out["url"] == "https://example.com/page"
    assert out["title"] == "Hello Title"
    # El texto NO contiene el script ni el contenido de <style>.
    assert "alert" not in out["text"]
    assert "steal" not in out["text"]
    assert ".x{}" not in out["text"]
    # Sí contiene el texto visible.
    assert "Heading" in out["text"]
    assert "bold" in out["text"]
    assert out["truncated"] is False


@pytest.mark.asyncio
async def test_web_fetch_truncates_to_max_bytes() -> None:
    big = "<html><body>" + ("A" * 5000) + "</body></html>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=big)

    async with _client(handler) as c:
        out = await web_fetch("https://example.com/big", client=c, resolver=_PUBLIC, max_bytes=100)

    assert out["truncated"] is True
    assert len(out["text"].encode("utf-8")) <= 100


@pytest.mark.asyncio
async def test_web_fetch_rejects_private_url_before_get() -> None:
    called = {"hit": False}

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        called["hit"] = True
        return httpx.Response(200, text="should not be reached")

    # Resolver que apunta a una IP privada → assert_safe_url lanza.
    private = lambda host, port: ["169.254.169.254"]  # noqa: E731
    async with _client(handler) as c:
        with pytest.raises(UnsafeUrlError):
            await web_fetch("https://evil.example/", client=c, resolver=private)
    assert called["hit"] is False


@pytest.mark.asyncio
async def test_web_fetch_rejects_non_http_scheme() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        return httpx.Response(200, text="x")

    async with _client(handler) as c:
        with pytest.raises(UnsafeUrlError):
            await web_fetch("file:///etc/passwd", client=c, resolver=_PUBLIC)


@pytest.mark.asyncio
async def test_web_fetch_http_error_raises_web_search_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    async with _client(handler) as c:
        with pytest.raises(WebSearchError):
            await web_fetch("https://example.com/", client=c, resolver=_PUBLIC)


# ===========================================================================
# SearXNGProvider
# ===========================================================================
@pytest.mark.asyncio
async def test_searxng_provider_normalizes_results() -> None:
    payload = {
        "results": [
            {"title": "First", "url": "https://a.example/1", "content": "snippet one"},
            {"title": "Second", "url": "https://b.example/2", "content": "snippet two"},
        ]
    }
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["req"] = request
        return httpx.Response(200, json=payload)

    async with _client(handler) as c:
        provider = SearXNGProvider("http://searxng:8080", client=c, resolver=_PUBLIC)
        results = await provider.search("python asyncio", limit=5)

    assert results == [
        {"title": "First", "url": "https://a.example/1", "snippet": "snippet one"},
        {"title": "Second", "url": "https://b.example/2", "snippet": "snippet two"},
    ]
    # Pidió el endpoint /search con format=json.
    req = seen["req"]
    assert req.url.path == "/search"
    assert req.url.params.get("format") == "json"
    assert req.url.params.get("q") == "python asyncio"


@pytest.mark.asyncio
async def test_searxng_connects_directly_not_through_egress_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """searxng es infra INTERNA de confianza → cliente DIRECTO, no el egress-proxy.

    Visto en vivo: enrutar el hop api-server→searxng por el egress-proxy (que
    es para salidas a Internet) daba «All connection attempts failed». Sin
    client inyectado, el provider debe usar ``_build_direct_client`` y NUNCA
    ``_build_proxied_client`` (que además exigiría un proxy_url)."""
    from api_server.cortex import web

    used: list[str] = []

    def _fake_direct(timeout: float) -> httpx.AsyncClient:
        used.append("direct")
        return httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"results": []}))
        )

    def _fake_proxied(
        proxy_url: str | None, timeout: float
    ) -> httpx.AsyncClient:  # pragma: no cover
        used.append("proxied")
        raise AssertionError("searxng NO debe salir por el egress-proxy")

    monkeypatch.setattr(web, "_build_direct_client", _fake_direct)
    monkeypatch.setattr(web, "_build_proxied_client", _fake_proxied)

    # Sin client y sin proxy_url: antes esto reventaba con «no hay egress-proxy».
    provider = SearXNGProvider("http://searxng:8080", resolver=_PUBLIC)
    await provider.search("hola", limit=3)
    assert used == ["direct"]


@pytest.mark.asyncio
async def test_searxng_provider_respects_limit() -> None:
    payload = {
        "results": [{"title": str(i), "url": f"https://x/{i}", "content": ""} for i in range(10)]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with _client(handler) as c:
        provider = SearXNGProvider("http://searxng:8080", client=c, resolver=_PUBLIC)
        results = await provider.search("q", limit=3)

    assert len(results) == 3


# ===========================================================================
# BraveSearchProvider
# ===========================================================================
@pytest.mark.asyncio
async def test_brave_provider_normalizes_and_sends_key_header() -> None:
    payload = {
        "web": {
            "results": [
                {"title": "Brave1", "url": "https://a/1", "description": "desc one"},
            ]
        }
    }
    seen: dict[str, httpx.Request] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["req"] = request
        return httpx.Response(200, json=payload)

    async with _client(handler) as c:
        provider = BraveSearchProvider(
            "secret-key",
            base_url="https://api.search.brave.com/res/v1/web/search",
            client=c,
            resolver=_PUBLIC,
        )
        results = await provider.search("hello", limit=5)

    assert results == [{"title": "Brave1", "url": "https://a/1", "snippet": "desc one"}]
    # La key viaja en la cabecera de Brave, NO en la URL.
    req = seen["req"]
    assert req.headers.get("X-Subscription-Token") == "secret-key"
    assert "secret-key" not in str(req.url)


def test_brave_provider_requires_key() -> None:
    with pytest.raises(WebSearchError):
        BraveSearchProvider("", base_url="https://api.search.brave.com/res/v1/web/search")


# ===========================================================================
# select_web_search_provider + web_search
# ===========================================================================
def test_select_provider_defaults_to_searxng() -> None:
    provider = select_web_search_provider(
        provider_name="searxng",
        searxng_url="http://searxng:8080",
        brave_api_key=None,
        brave_url="https://api.search.brave.com/res/v1/web/search",
    )
    assert isinstance(provider, SearXNGProvider)


def test_select_provider_brave_when_configured() -> None:
    provider = select_web_search_provider(
        provider_name="brave",
        searxng_url="http://searxng:8080",
        brave_api_key="key",
        brave_url="https://api.search.brave.com/res/v1/web/search",
    )
    assert isinstance(provider, BraveSearchProvider)


def test_select_provider_brave_without_key_errors() -> None:
    # Proveedor brave sin key → error claro, NO crash silencioso.
    with pytest.raises(WebSearchError):
        select_web_search_provider(
            provider_name="brave",
            searxng_url="http://searxng:8080",
            brave_api_key=None,
            brave_url="https://api.search.brave.com/res/v1/web/search",
        )


def test_select_provider_unknown_name_errors() -> None:
    with pytest.raises(WebSearchError):
        select_web_search_provider(
            provider_name="bing",
            searxng_url="http://searxng:8080",
            brave_api_key=None,
            brave_url="https://api.search.brave.com/res/v1/web/search",
        )


def test_select_provider_searxng_without_url_errors() -> None:
    with pytest.raises(WebSearchError):
        select_web_search_provider(
            provider_name="searxng",
            searxng_url="",
            brave_api_key=None,
            brave_url="https://api.search.brave.com/res/v1/web/search",
        )


@pytest.mark.asyncio
async def test_web_search_uses_injected_provider() -> None:
    payload = {"results": [{"title": "T", "url": "https://x/1", "content": "snip"}]}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    async with _client(handler) as c:
        provider = SearXNGProvider("http://searxng:8080", client=c, resolver=_PUBLIC)
        results = await web_search("query", limit=5, provider=provider)

    assert results == [{"title": "T", "url": "https://x/1", "snippet": "snip"}]
