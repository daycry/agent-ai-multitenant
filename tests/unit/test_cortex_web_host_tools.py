"""Unit: host tools provider-agnósticas ``web_search`` / ``web_fetch`` (ADR 0067).

A diferencia de ``test_cortex_web_tools.py`` (la web NATIVA del Claude Agent SDK,
ADR 0076, sólo claude_sdk), aquí se prueban las **host tools** del catálogo del córtex
(``cortex/tools.py``): las ejecuta el api-server por tool-calling, así que valen para
cualquier provider. Están GATED por ``web_enabled`` en el :class:`CortexToolContext`:

  * sus schemas aparecen SOLO cuando ``web_enabled=True``;
  * ``run_cortex_tool`` las despacha;
  * el saneo/truncado se hace en post (la tool devuelve datos, nunca ejecuta).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from api_server.cortex.tools import (
    CORTEX_TOOLS,
    CortexToolContext,
    cortex_enabled_tool_names,
    cortex_tool_schemas,
    run_cortex_tool,
)

pytestmark = pytest.mark.unit


def _ctx(*, web_enabled: bool) -> CortexToolContext:
    # session=None: las web tools no tocan la DB. Las de memoria sí, pero no se
    # ejercitan en estos tests.
    return CortexToolContext(
        session=None,  # type: ignore[arg-type]
        owner_user_id=uuid4(),
        tenant_id=uuid4(),
        web_enabled=web_enabled,
    )


# ---------------------------------------------------------------------------
# El catálogo incluye las dos host tools web.
# ---------------------------------------------------------------------------
def test_web_tools_in_catalog() -> None:
    assert "web_search" in CORTEX_TOOLS
    assert "web_fetch" in CORTEX_TOOLS


# ---------------------------------------------------------------------------
# Gating: los nombres habilitados incluyen las web tools SOLO si web_enabled.
# ---------------------------------------------------------------------------
def test_enabled_names_exclude_web_when_disabled() -> None:
    names = cortex_enabled_tool_names(web_enabled=False)
    assert "cortex_remember" in names
    assert "cortex_recall_more" in names
    assert "web_search" not in names
    assert "web_fetch" not in names


def test_enabled_names_include_web_when_enabled() -> None:
    names = cortex_enabled_tool_names(web_enabled=True)
    assert "web_search" in names
    assert "web_fetch" in names


# ---------------------------------------------------------------------------
# Los schemas reflejan el gating.
# ---------------------------------------------------------------------------
def test_schemas_hide_web_when_disabled() -> None:
    schemas = cortex_tool_schemas(cortex_enabled_tool_names(web_enabled=False))
    names = {s["name"] for s in schemas}
    assert "web_search" not in names
    assert "web_fetch" not in names


def test_schemas_show_web_when_enabled() -> None:
    schemas = cortex_tool_schemas(cortex_enabled_tool_names(web_enabled=True))
    names = {s["name"] for s in schemas}
    assert "web_search" in names
    assert "web_fetch" in names
    # Cada web tool trae un schema bien formado (name + parameters.object).
    for s in schemas:
        if s["name"] in ("web_search", "web_fetch"):
            assert s["parameters"]["type"] == "object"


# ---------------------------------------------------------------------------
# run_cortex_tool despacha web_search (con un provider inyectado por el ctx).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_web_search_dispatches() -> None:
    class _FakeProvider:
        async def search(self, query: str, *, limit: int = 5) -> list[dict[str, str]]:
            return [{"title": "T", "url": "https://x/1", "snippet": "snip"}]

    ctx = _ctx(web_enabled=True)
    # Seam de inyección: el ctx puede portar un provider de búsqueda ya construido.
    object.__setattr__(ctx, "web_search_provider", _FakeProvider())

    result = await run_cortex_tool("web_search", ctx, {"query": "hello", "limit": 3})
    assert result["count"] == 1
    assert result["results"][0]["url"] == "https://x/1"


# ---------------------------------------------------------------------------
# run_cortex_tool despacha web_fetch (con un httpx mock inyectado por el ctx).
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_run_web_fetch_dispatches_and_sanitizes() -> None:
    import httpx

    html = (
        "<html><head><title>Doc</title><script>x()</script></head>"
        "<body><p>Texto visible</p></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, html=html)

    ctx = _ctx(web_enabled=True)
    object.__setattr__(ctx, "web_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    object.__setattr__(ctx, "web_resolver", lambda host, port: ["8.8.8.8"])

    try:
        result = await run_cortex_tool("web_fetch", ctx, {"url": "https://example.com/doc"})
    finally:
        await ctx.web_client.aclose()  # type: ignore[attr-defined]

    assert result["title"] == "Doc"
    assert "Texto visible" in result["text"]
    assert "x()" not in result["text"]
    assert result["truncated"] is False
