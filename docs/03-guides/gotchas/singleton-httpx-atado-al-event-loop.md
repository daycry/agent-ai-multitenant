---
title: "Un cliente httpx cacheado con lru_cache muere al segundo event loop, y el error sale como un 500 en un test que «solo pasa solo»"
area: python, tests, asyncio
encountered: 2026-08-14
stack: python 3.13, httpx 0.28, pytest-asyncio, functools.lru_cache
---

## Síntoma

`tests/integration/test_rag_search_wireup.py::test_rag_search_does_not_leak_ungranted_kb`
—la guarda de que un agente sin concesión de KB no ve chunks— pasa **ejecutada
sola** y falla **en lote**. La aserción que revienta es la del código de estado,
no la de la fuga:

```
assert resp.status_code == 200, resp.text   # llega un 500
```

Y en el log del `api-server`, dentro del traceback del 500:

```
File "api_server/rag/tool.py", line 91, in rag_search
    embeddings = await embedder.embed([query])
...
File "asyncio/base_events.py", line 552, in _check_closed
    raise RuntimeError('Event loop is closed')
```

Nada en el mensaje menciona ni el RAG, ni la visibilidad, ni el test anterior.

## Causa raíz

`ingestion/embed_client.get_shared_embed_client()` era un `@lru_cache(maxsize=1)`
sobre un `httpx.AsyncClient` — un singleton de **proceso** (task_prod13_05,
perf-9: un solo pool con keep-alive hacia Ollama en vez de un handshake TCP por
request).

Pero el pool que hay dentro del cliente está atado al **event loop** en el que se
abrieron sus conexiones, y un `lru_cache` no sabe nada de loops. En el api-server
las dos vidas coinciden (un único loop para todo el proceso). En cuanto el mismo
proceso abre un loop nuevo —`asyncio.run` por tarea, como hacen los workers; **un
loop por caso**, como hace pytest-asyncio— el cliente cacheado arrastra
conexiones keep-alive de un loop ya cerrado, y la primera petición que intente
reciclarlas llama a `loop.call_soon()` sobre él: `RuntimeError: Event loop is
closed`.

Dos detalles explican por qué se disfraza de flaky y de fuga de datos:

1. **Depende de que quede una conexión viva en el pool.** El primer test que
   embebe con éxito la deja; el siguiente, ya en otro loop, se la encuentra. Si
   Ollama está caído o cierra la conexión, no queda nada que reciclar y el test
   pasa. De ahí «solo pasa solo» — y de ahí que el mismo fichero pase o falle
   según la máquina.
2. **El fallo NO degrada.** `OllamaEmbedder.embed` traduce a `EmbeddingError` los
   `httpx.HTTPError` para caer a BM25 cuando Ollama no está; un `RuntimeError`
   del transporte se le escapa y sale por arriba como 500.

Es el **mismo defecto** que `db/platform_settings.py` ya documentaba para el
cliente Redis compartido, y aquí era peor: allí el error se traducía a «cache
miss» y solo costaba rendimiento; aquí tumbaba la request.

## Fix

Religar el singleton al loop en curso, no callar el error
(`apps/api-server/src/api_server/ingestion/embed_client.py`): se guarda una
referencia **débil** al loop de la última adquisición y, si el actual es otro, se
descarta el cliente cacheado para que se construya uno atado al loop de ahora.
Referencia débil porque una fuerte mantendría vivo un loop cerrado para siempre,
y comparar por `id()` sería peor: los ids se reciclan y un loop nuevo podría
hacerse pasar por el viejo.

Llamado desde código síncrono (un seed, un test de wiring) no hay loop contra el
que comparar y se conserva la ligadura anterior: manda la primera adquisición
DENTRO de un loop, que es cuando se abren conexiones.

En producción no cambia nada: mismo loop siempre ⇒ nunca se religa.

## Cómo verificar el fix

La causa raíz se reproduce en veinte líneas sin pytest, llamando al cliente
cacheado desde dos `asyncio.run` distintos (`_cached_embed_client` es el interno,
sin la religadura):

```
loop 1 -> 200
loop 2 -> RuntimeError: Event loop is closed
```

La guarda de regresión vive en
`tests/unit/test_shared_embedder_client.py::test_the_shared_client_is_rebuilt_when_the_event_loop_changes`:
dentro de un loop el cliente se reutiliza (el singleton sigue siendo singleton);
entre dos `asyncio.run` distintos, no.

Y el test que lo destapó, en lote y en aislamiento:

```bash
TEST_PG_DB_NAME=algo_unico .venv/Scripts/python.exe -m pytest \
  tests/integration/test_rag_search_wireup.py -q -p no:randomly --timeout=900
```

## La lección, que es más ancha que este bug

Un test que **solo pasa solo** acaba borrado por «flaky», y con él se va lo que
protegía — aquí, una guarda de fuga entre tenants. El precedente del repo está en
`tests/integration/conftest.py` (`_APP_REVOKED_TABLES`): otra guarda que solo
pasaba sola y que tenía razón. Antes de creerse la contaminación entre tests
(`workflow-parallel-review-source-contamination.md`), busca qué estado de
PRODUCCIÓN sobrevive entre peticiones. Un `lru_cache` sobre cualquier cosa con
conexiones abiertas es el primer sitio donde mirar.
