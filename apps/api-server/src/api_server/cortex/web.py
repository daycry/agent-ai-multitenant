"""Web del córtex provider-agnóstica (ADR 0067) — ``web_fetch`` + ``web_search``.

A diferencia de la web NATIVA del Claude Agent SDK (ADR 0076, sólo claude_sdk), estas
son **host tools** que ejecuta el api-server por tool-calling, así que funcionan con
CUALQUIER provider del catálogo (claude_sdk / copilot / azure / ollama).

Reglas no negociables (Principio 2 — egress controlado):

  * TODA salida a Internet va por el ``egress-proxy`` (tinyproxy con allowlist). El
    api-server NUNCA abre una conexión directa: el httpx client se construye con
    ``proxy=<egress-proxy-url>``. El proxy es configurable por env
    (``API_SERVER_EGRESS_PROXY_URL``; el nombre viejo
    ``API_SERVER_CORTEX_EGRESS_PROXY_URL`` sigue valiendo como alias — ADR 0165,
    que le añadió un consumidor que no es del córtex).
  * ANTES de cualquier GET se valida la URL con :func:`assert_safe_url` (anti-SSRF):
    sólo http/https, host no privado/loopback/metadata, puerto permitido. El
    resolver DNS se inyecta en tests.
  * El contenido devuelto entra como DATOS, NUNCA se ejecuta: ``web_fetch`` saca el
    HTML peligroso (``<script>`` / ``<style>`` / ``<head>``), extrae el texto visible
    y lo trunca a ``max_bytes``.

``web_search`` habla con un **proveedor de búsqueda** del catálogo cerrado (igual que
LLM en ADR 0021): ``SearXNGProvider`` (self-host, sin key — el camino testeable por
defecto) o ``BraveSearchProvider`` (API key resuelta de Vault/env). La selección la
gobierna el setting ``cortex.web_search_provider``.
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog

from api_server.cortex.web_safety import Resolver, assert_safe_url

logger = structlog.get_logger(__name__)

# Tope por defecto del cuerpo devuelto por ``web_fetch`` (256 KiB, ADR 0067 §3).
DEFAULT_MAX_BYTES = 262_144
# Timeout por defecto de un GET de egress.
DEFAULT_TIMEOUT_S = 10.0
# Tope de resultados de búsqueda por defecto.
DEFAULT_SEARCH_LIMIT = 5
# User-Agent honesto para los GET (un servidor que filtre bots no nos confunde con
# un navegador real, y queda claro en sus logs quién pide).
_USER_AGENT = "agentic-cortex/1.0 (+web-fetch)"


class WebSearchError(RuntimeError):
    """Fallo en una operación web del córtex (proveedor no configurado, HTTP no-2xx,
    respuesta con forma inesperada). El mensaje NUNCA filtra una API key."""


# ---------------------------------------------------------------------------
# Cliente HTTP por el egress-proxy
# ---------------------------------------------------------------------------
def _build_proxied_client(proxy_url: str | None, timeout: float) -> httpx.AsyncClient:
    """Construye un :class:`httpx.AsyncClient` que sale SOLO por el egress-proxy.

    Si ``proxy_url`` es vacío/None levantamos :class:`WebSearchError` en vez de abrir
    una conexión directa: la regla es "nunca salir sin proxy". ``follow_redirects`` se
    deja en False — cada salto debe re-validarse con anti-SSRF, así que un redirect lo
    trata el caller (aquí no auto-seguimos a un Location potencialmente privado)."""
    if not proxy_url:
        raise WebSearchError(
            "no hay egress-proxy configurado (API_SERVER_EGRESS_PROXY_URL); "
            "la web del córtex NO sale a Internet sin el proxy"
        )
    return httpx.AsyncClient(
        proxy=proxy_url,
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": _USER_AGENT},
    )


def _build_direct_client(timeout: float) -> httpx.AsyncClient:
    """Cliente HTTP DIRECTO (sin egress-proxy) para el buscador INTERNO de
    confianza (searxng en la red del docker).

    El egress-proxy es para salidas a INTERNET (Brave, las URLs de resultados
    en web_fetch); el hop api-server→searxng es tráfico interno de servicio y
    NO debe atravesarlo — el proxy no alcanza el searxng interno («All
    connection attempts failed», visto en vivo). searxng hace su propia salida
    a los buscadores por su cuenta."""
    return httpx.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        headers={"User-Agent": _USER_AGENT},
    )


# ---------------------------------------------------------------------------
# Saneo de HTML → texto (stdlib, sin dependencias nuevas)
# ---------------------------------------------------------------------------
class _TextExtractor(HTMLParser):
    """Extrae el ``<title>`` + el texto visible, descartando ``script``/``style``/``head``.

    Pensado como saneo defensivo: el contenido externo entra como DATO. No
    reconstruye estructura — sólo recoge texto plano legible, que es lo que el
    córtex necesita para razonar/RAG."""

    _SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "svg"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._in_head = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
        elif tag == "head":
            self._in_head = True
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag == "head":
            self._in_head = False
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        # El texto del <head> (que no sea el title) no es contenido visible.
        if self._in_head:
            return
        stripped = data.strip()
        if stripped:
            self.text_parts.append(stripped)


def _sanitize_html(html: str) -> tuple[str | None, str]:
    """Devuelve ``(title, text)`` extraídos de ``html`` (scripts/estilos fuera).

    ``title`` es ``None`` cuando el documento no trae ``<title>``. El texto se
    normaliza colapsando espacios para que el truncado por bytes sea predecible."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # un HTML salvaje no debe tumbar el fetch
        logger.warning("cortex.web_fetch.parse_failed")
    title = " ".join(" ".join(parser.title_parts).split()) or None
    text = " ".join(" ".join(parser.text_parts).split())
    return title, text


def _truncate_text(text: str, max_bytes: int) -> tuple[str, bool]:
    """Trunca ``text`` para que su UTF-8 no exceda ``max_bytes``.

    Devuelve ``(texto, truncated)``. El corte respeta el límite de bytes sin partir
    un carácter multibyte (decode con ``ignore`` tras el slice de bytes)."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return truncated, True


# ---------------------------------------------------------------------------
# web_fetch
# ---------------------------------------------------------------------------
async def web_fetch(
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = DEFAULT_TIMEOUT_S,
    client: httpx.AsyncClient | None = None,
    proxy_url: str | None = None,
    resolver: Resolver | None = None,
) -> dict[str, Any]:
    """GET curado de ``url`` por el egress-proxy, saneado y truncado.

    Flujo: :func:`assert_safe_url` (anti-SSRF, ANTES de cualquier red) → GET por el
    proxy → saneo del HTML (strip de script/style, extracción de texto + title) →
    truncado a ``max_bytes``. Devuelve ``{url, title?, text, truncated}``.

    ``client`` se inyecta en tests (un ``MockTransport``); en producción se construye
    uno que sale SOLO por ``proxy_url``. ``resolver`` se inyecta en el anti-SSRF.

    Lanza :class:`~api_server.cortex.web_safety.UnsafeUrlError` si la URL no es segura
    (NO se hace ningún GET) y :class:`WebSearchError` si el GET falla / responde no-2xx.
    """
    assert_safe_url(url, resolver=resolver)

    owns_client = client is None
    http = client or _build_proxied_client(proxy_url, timeout)
    try:
        try:
            response = await http.get(url)
        except httpx.HTTPError as exc:
            raise WebSearchError(f"web_fetch falló al pedir la URL: {exc}") from exc
        if response.status_code >= 400:
            raise WebSearchError(f"web_fetch HTTP {response.status_code} para la URL")
        body = response.text
    finally:
        if owns_client:
            await http.aclose()

    title, text = _sanitize_html(body)
    text, truncated = _truncate_text(text, max_bytes)
    out: dict[str, Any] = {"url": url, "text": text, "truncated": truncated}
    if title:
        out["title"] = title
    return out


# ---------------------------------------------------------------------------
# Proveedores de búsqueda (catálogo cerrado, igual que LLM en ADR 0021)
# ---------------------------------------------------------------------------
@runtime_checkable
class WebSearchProvider(Protocol):
    """Un proveedor de búsqueda web: ``search(query, limit) -> [{title,url,snippet}]``."""

    async def search(
        self, query: str, *, limit: int = DEFAULT_SEARCH_LIMIT
    ) -> list[dict[str, str]]: ...


def _normalize_limit(limit: int) -> int:
    """Acota ``limit`` a [1, 20] (cordura — un modelo no pide 10.000 resultados)."""
    return max(1, min(int(limit), 20))


class SearXNGProvider:
    """Búsqueda contra una instancia SearXNG self-host (sin API key).

    GET a ``{base_url}/search?q=&format=json``. SearXNG devuelve ``{"results": [{title,
    url, content, ...}]}``; ``content`` es el snippet. Sale SOLO por el egress-proxy.
    """

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        proxy_url: str | None = None,
        resolver: Resolver | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not base_url:
            raise WebSearchError("SearXNG no configurado (falta cortex.searxng_url)")
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._proxy_url = proxy_url
        self._resolver = resolver
        self._timeout = timeout

    async def search(
        self, query: str, *, limit: int = DEFAULT_SEARCH_LIMIT
    ) -> list[dict[str, str]]:
        limit = _normalize_limit(limit)
        endpoint = f"{self._base_url}/search"
        # Backend de confianza (cortex.searxng_url, System Admin): vive en la red
        # interna del docker con IP privada → allow_internal. El guard estricto se
        # aplica a las URLs de los resultados en web_fetch.
        assert_safe_url(endpoint, resolver=self._resolver, allow_internal=True)
        params = {"q": query, "format": "json"}

        owns = self._client is None
        # searxng es INTERNO y de confianza → cliente DIRECTO, no el egress-proxy.
        http = self._client or _build_direct_client(self._timeout)
        try:
            try:
                response = await http.get(endpoint, params=params)
            except httpx.HTTPError as exc:
                raise WebSearchError(f"SearXNG no respondió: {exc}") from exc
            if response.status_code >= 400:
                raise WebSearchError(f"SearXNG HTTP {response.status_code}")
            body = response.json()
        finally:
            if owns:
                await http.aclose()

        raw = body.get("results") if isinstance(body, dict) else None
        if not isinstance(raw, list):
            raise WebSearchError("SearXNG devolvió una respuesta sin `results`")
        out: list[dict[str, str]] = []
        for item in raw[:limit]:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "title": str(item.get("title", "")),
                    "url": str(item.get("url", "")),
                    "snippet": str(item.get("content", "")),
                }
            )
        return out


class BraveSearchProvider:
    """Búsqueda contra la Brave Search API (API key en cabecera ``X-Subscription-Token``).

    La key se resuelve de Vault/env (NUNCA viaja en la URL ni se loguea). Brave
    devuelve ``{"web": {"results": [{title, url, description, ...}]}}``; ``description``
    es el snippet. Sale SOLO por el egress-proxy.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        client: httpx.AsyncClient | None = None,
        proxy_url: str | None = None,
        resolver: Resolver | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not api_key:
            raise WebSearchError("Brave Search requiere una API key (Vault/env) y no hay ninguna")
        if not base_url:
            raise WebSearchError("Brave Search no configurado (falta el endpoint)")
        self._api_key = api_key
        self._base_url = base_url
        self._client = client
        self._proxy_url = proxy_url
        self._resolver = resolver
        self._timeout = timeout

    async def search(
        self, query: str, *, limit: int = DEFAULT_SEARCH_LIMIT
    ) -> list[dict[str, str]]:
        limit = _normalize_limit(limit)
        # Backend de confianza (config del operador): allow_internal — el guard
        # estricto se aplica a los resultados en web_fetch, no al buscador.
        assert_safe_url(self._base_url, resolver=self._resolver, allow_internal=True)
        params = {"q": query, "count": str(limit)}
        headers = {"Accept": "application/json", "X-Subscription-Token": self._api_key}

        owns = self._client is None
        http = self._client or _build_proxied_client(self._proxy_url, self._timeout)
        try:
            try:
                response = await http.get(self._base_url, params=params, headers=headers)
            except httpx.HTTPError as exc:
                raise WebSearchError(f"Brave Search no respondió: {exc}") from exc
            if response.status_code >= 400:
                # NO incluimos el cuerpo: podría reflejar la key en un mensaje de error.
                raise WebSearchError(f"Brave Search HTTP {response.status_code}")
            body = response.json()
        finally:
            if owns:
                await http.aclose()

        web = body.get("web") if isinstance(body, dict) else None
        raw = web.get("results") if isinstance(web, dict) else None
        if not isinstance(raw, list):
            raise WebSearchError("Brave Search devolvió una respuesta sin `web.results`")
        out: list[dict[str, str]] = []
        for item in raw[:limit]:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "title": str(item.get("title", "")),
                    "url": str(item.get("url", "")),
                    "snippet": str(item.get("description", "")),
                }
            )
        return out


# Nombres del catálogo cerrado de proveedores de búsqueda (ADR 0067).
WEB_SEARCH_PROVIDERS: tuple[str, ...] = ("searxng", "brave")


def select_web_search_provider(
    *,
    provider_name: str,
    searxng_url: str,
    brave_api_key: str | None,
    brave_url: str,
    client: httpx.AsyncClient | None = None,
    proxy_url: str | None = None,
    resolver: Resolver | None = None,
) -> WebSearchProvider:
    """Construye el proveedor de búsqueda según ``provider_name`` (catálogo cerrado).

    'searxng' (default, sin key) o 'brave' (requiere key). Un nombre desconocido o un
    backend sin configurar levanta :class:`WebSearchError` con un mensaje claro — NO
    un crash silencioso ni una salida directa a Internet."""
    name = (provider_name or "").strip().lower()
    if name == "searxng":
        if not searxng_url:
            raise WebSearchError(
                "el proveedor de búsqueda es 'searxng' pero no hay URL configurada "
                "(API_SERVER_CORTEX_SEARXNG_URL / cortex.searxng_url)"
            )
        return SearXNGProvider(searxng_url, client=client, proxy_url=proxy_url, resolver=resolver)
    if name == "brave":
        if not brave_api_key:
            raise WebSearchError(
                "el proveedor de búsqueda es 'brave' pero no hay API key "
                "(Vault / API_SERVER_BRAVE_SEARCH_API_KEY)"
            )
        return BraveSearchProvider(
            brave_api_key,
            base_url=brave_url,
            client=client,
            proxy_url=proxy_url,
            resolver=resolver,
        )
    raise WebSearchError(
        f"proveedor de búsqueda desconocido {provider_name!r}; "
        f"válidos: {WEB_SEARCH_PROVIDERS} (ADR 0067)"
    )


async def web_search(
    query: str,
    *,
    limit: int = DEFAULT_SEARCH_LIMIT,
    provider: WebSearchProvider,
) -> list[dict[str, str]]:
    """Busca ``query`` con el ``provider`` dado y devuelve ``[{title, url, snippet}]``.

    El ``provider`` lo resuelve el caller (la host tool) con
    :func:`select_web_search_provider` a partir del setting + la key de Vault/env, de
    modo que esta función queda pura y trivialmente testeable con un provider mock."""
    if not query or not query.strip():
        return []
    return await provider.search(query.strip(), limit=limit)


__all__ = [
    "DEFAULT_MAX_BYTES",
    "DEFAULT_SEARCH_LIMIT",
    "WEB_SEARCH_PROVIDERS",
    "BraveSearchProvider",
    "SearXNGProvider",
    "WebSearchError",
    "WebSearchProvider",
    "select_web_search_provider",
    "web_fetch",
    "web_search",
]
