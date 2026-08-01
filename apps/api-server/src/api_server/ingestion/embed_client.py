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

from functools import lru_cache

import httpx

from api_server.ingestion.embeddings import OllamaEmbedder

__all__ = [
    "get_shared_embed_client",
    "reset_shared_embed_client_cache",
    "shared_ollama_embedder",
]


@lru_cache(maxsize=1)
def get_shared_embed_client() -> httpx.AsyncClient:
    """El cliente httpx compartido del proceso contra Ollama.

    Mismo patrón que :func:`api_server.auth.deps.get_redis`: singleton perezoso
    cacheado, reseteable en tests. Con un cliente único el keep-alive hacia
    Ollama sobrevive entre requests en vez de rehacerse en cada una.

    El timeout es el mismo que el default de ``OllamaEmbedder`` (60 s): embeber
    un lote grande es lento y el que manda aquí es el modelo, no la red.
    """
    return httpx.AsyncClient(
        timeout=60.0,
        limits=httpx.Limits(max_keepalive_connections=10, max_connections=20),
    )


def reset_shared_embed_client_cache() -> None:
    """Test hook: olvida el cliente cacheado (espejo de ``reset_redis_cache``).

    NO lo cierra: cerrarlo pide un `await` y este hook se llama desde el
    `finally` síncrono de las fixtures. El cliente huérfano lo recoge el GC junto
    con su bucle de eventos.
    """
    get_shared_embed_client.cache_clear()


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
