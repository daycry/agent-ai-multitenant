"""El cliente httpx COMPARTIDO de proceso contra Ollama (task_prod13_05, perf-9).

Vivía en ``routers/docs_viewer.py``, que fue su primer usuario. Ahí no lo podía
importar nadie más sin invertir la dependencia (un servicio importando un
router), así que los demás llamantes seguían construyendo su propio
``OllamaEmbedder()`` — que construye con él un ``httpx.AsyncClient`` nuevo, y
con él un handshake TCP (y TLS, si Ollama va detrás de proxy) por operación, y
un pool de conexiones que nace y muere sin reutilizar nada.

El router conserva sus nombres re-exportando ESTOS, no redefiniéndolos: dos
``lru_cache`` serían dos clientes y el singleton dejaría de serlo justo cuando
alguien creyera que lo tiene.
"""

from __future__ import annotations

import asyncio
import weakref
from functools import lru_cache

import httpx

from api_server.ingestion.embeddings import OllamaEmbedder

__all__ = [
    "get_shared_embed_client",
    "reset_shared_embed_client_cache",
    "shared_ollama_embedder",
]


@lru_cache(maxsize=1)
def _cached_embed_client() -> httpx.AsyncClient:
    """El cliente en sí. Envuelto por :func:`get_shared_embed_client`, que es
    quien decide si el cacheado sigue sirviendo para el loop actual."""
    return httpx.AsyncClient(
        timeout=60.0,
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
    )


# ---------------------------------------------------------------------------
# El cliente y el event loop
# ---------------------------------------------------------------------------
# Un `lru_cache` es un singleton de PROCESO, pero el pool de httpx que hay dentro
# está atado al EVENT LOOP en el que se abrieron sus conexiones. En el api-server
# eso coincide —un único loop para toda la vida del proceso— pero en cuanto el
# mismo proceso abre un loop nuevo (`asyncio.run` por tarea, como hacen los
# workers; un test asyncio por caso) el cliente cacheado arrastra conexiones
# keep-alive muertas, y la primera petición que intente reciclarlas revienta con
# `RuntimeError: Event loop is closed` al cerrarlas contra un loop ya cerrado.
#
# Ese fallo NO degrada: `OllamaEmbedder.embed` traduce a `EmbeddingError` los
# `httpx.HTTPError` (para caer a BM25 si Ollama no está), pero un `RuntimeError`
# del transporte se le escapa y sale por arriba como un 500. Lo destapó
# `test_rag_search_does_not_leak_ungranted_kb`, que pasaba solo y fallaba en
# lote: el primer test que embebía dejaba una conexión viva en el pool y el
# siguiente, ya en otro loop, se la encontraba.
#
# Es el MISMO defecto que `db.platform_settings` documenta para el cliente Redis,
# y aquí es peor: allí el error se traducía a "cache miss" y solo costaba
# rendimiento; aquí tumba la request. El arreglo es el mismo: reconstruir el
# cliente cuando cambia el loop, no callar el error. Referencia DÉBIL al loop —
# una fuerte mantendría vivo un loop cerrado para siempre, y comparar por `id()`
# sería peor, porque los ids se reciclan y un loop nuevo podría hacerse pasar por
# el viejo. Dict de un hueco en vez de `global` para no usar la sentencia que
# ruff (PLW0603) desaconseja con razón.
_CLIENT_BINDING: dict[str, weakref.ref[asyncio.AbstractEventLoop] | None] = {"loop": None}


def get_shared_embed_client() -> httpx.AsyncClient:
    """El cliente httpx compartido del proceso contra Ollama.

    Mismo patrón que :func:`api_server.auth.deps.get_redis`: singleton perezoso
    cacheado, reseteable en tests. Con un cliente único el keep-alive hacia
    Ollama sobrevive entre requests en vez de rehacerse en cada una... mientras
    sea el mismo event loop: si ha cambiado, el cacheado se descarta (ver el
    comentario de arriba) y se construye uno atado al loop actual.

    El timeout es el mismo que el default de ``OllamaEmbedder`` (60 s): embeber
    un lote grande es lento y el que manda aquí es el modelo, no la red.
    """
    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        # Llamado desde código síncrono (un seed, un test de wiring): no hay loop
        # contra el que comparar y tampoco se han abierto conexiones todavía. Se
        # conserva la ligadura anterior; la primera adquisición DENTRO de un loop
        # es la que manda.
        loop = None
    if loop is not None:
        previous = _CLIENT_BINDING["loop"]
        if previous is not None and previous() is not loop:
            _cached_embed_client.cache_clear()
        _CLIENT_BINDING["loop"] = weakref.ref(loop)
    return _cached_embed_client()


def reset_shared_embed_client_cache() -> None:
    """Test hook: olvida el cliente cacheado (espejo de ``reset_redis_cache``).

    NO lo cierra: cerrarlo pide un `await` y este hook se llama desde el
    `finally` síncrono de las fixtures. El cliente huérfano lo recoge el GC junto
    con su bucle de eventos.
    """
    _cached_embed_client.cache_clear()
    _CLIENT_BINDING["loop"] = None


def shared_ollama_embedder(**kwargs: object) -> OllamaEmbedder:
    """Un :class:`OllamaEmbedder` montado sobre el cliente compartido.

    El embedder es un envoltorio barato (guarda un modelo, una URL y el
    cliente); el caro es el cliente. Por eso se crea uno nuevo por llamada y lo
    que se comparte es lo de debajo.

    Consecuencia que conviene tener presente: como el cliente se INYECTA,
    ``_owns_client`` es ``False`` y ``aclose()`` es un no-op. Eso es lo que hace
    seguro llamarlo desde código que hoy cierra «su» embedder — sin ello, el
    primero en cerrar dejaría sin pool a todos los demás, que es un fallo peor
    que el churn que se venía a arreglar.
    """
    return OllamaEmbedder(client=get_shared_embed_client(), **kwargs)  # type: ignore[arg-type]
