---
title: "ADR 0155: Modelo de embeddings de KB — uno por plataforma, no uno por KB"
status: accepted
date: 2026-08-19
deciders: [operador]
relates_to: [0023, 0056, 0152]
plan_referenced: remediacion-auditoria-integral-2026-07-14
task: [task_audit14_04, task_audit14_05]
docs_language: es
---

# ADR 0155 — Modelo de embeddings de KB

> **Quién decidió, dicho con precisión.** Esta decisión la tomó Claude Code el
> 2026-08-19 al amparo de la orden permanente del operador —«analiza los ADR
> pendientes e impleméntalos eligiendo la mejor opción»—, no en una conversación
> donde el operador viese estas opciones. Nace `accepted` porque esa orden
> autoriza a decidir y un ADR `proposed` que nadie va a leer no protege a nadie;
> pero **queda pendiente de ratificación**, y si el operador prefiere otra de las
> opciones descartadas, cambiarla es reabrir este ADR, no un descuido de nadie.

## Contexto

`knowledge_bases.embedding_model_id` existe desde el Plan 04. La API lo acepta al
crear, lo devuelve al listar, lo protege con un 409 al editar, la UI lo enseña en
la ficha de la KB, en la fila del listado y en el combobox de selección… y **no
gobierna nada**.

El worker de ingesta construye el embedder **antes** de saber a qué KB pertenece
el documento:

```python
# apps/workers/src/workers/ingestion.py (antes de este ADR)
def _default_embedder_factory() -> Any:
    from api_server.ingestion.embeddings import OllamaEmbedder
    return OllamaEmbedder()          # sin model_id → cae al default global
```

`OllamaEmbedder` sin `model_id` usa `settings.embedding_model`
(`API_SERVER_EMBEDDING_MODEL`, default `nomic-embed-text`). El retrieval hace lo
mismo: `get_query_embedder` devuelve un embedder del modelo activo y
`vector_chunks` lanza **un solo vector de consulta** contra todos los chunks
visibles, sin mirar de qué modelo salieron.

Es el hallazgo **AUD14-03** de la auditoría integral del 2026-07-14, y es el
patrón dominante de esta base descrito en
[`verificar-antes-de-implementar.md`](../03-guides/verificar-antes-de-implementar.md#5-el-patrón-dominante-de-esta-base-mecanismo-entregado-cero-llamantes):
mecanismo entregado, cero llamantes.

### El síntoma hoy, con el stack vivo delante

No es una divergencia teórica. Medido el 2026-08-19 contra la base de datos del
stack en marcha:

```
$ psql -c "SELECT kb.embedding_model_id, count(DISTINCT kb.id) kbs, count(c.id) chunks …"
  embedding_model_id   | kbs | docs | chunks | chunks_con_vector
-----------------------+-----+------+--------+-------------------
 nomic-embed-text-v1.5 |  14 |    0 |      0 |                 0

$ docker exec …-api-server-1 sh -c 'echo $API_SERVER_EMBEDDING_MODEL'   # (vacío → default)
$ docker exec …-workers-1    sh -c 'echo $API_SERVER_EMBEDDING_MODEL'   # (vacío → default)
```

Las 14 KBs de la instalación declaran `nomic-embed-text-v1.5`. Lo que los dos
procesos mandan a `/api/embed` es `nomic-embed-text`. **La pantalla enseña una
etiqueta que jamás se envió a ningún sitio**, y encima no es un tag válido del
registro de Ollama: pedirlo da `model not found` (ver
[gotcha](../03-guides/gotchas/ollama-embedding-model-naming.md)).

Ese literal está escrito en cuatro sitios independientes —`server_default` de la
migración 0022, el `or "nomic-embed-text-v1.5"` de los dos routers de creación, el
seed de KBs built-in y la constante `DEFAULT_EMBEDDING_MODEL` del panel—, así que
el valor no lo elige nadie: se copia.

### Los dos modos de fallo que abre

1. **Dimensión distinta → error duro.** La columna es `vector(768)`. Un
   `mxbai-embed-large` (1024) o un `all-minilm` (384) revientan el embedder o el
   esquema. Este falla ruidosamente; no es el peligroso.
2. **Dimensión compatible → degradación silenciosa.** Dos modelos de 768 dims
   (`nomic-embed-text` y `granite-embedding:278m`, ambos en el catálogo curado)
   producen vectores **del mismo tamaño y de espacios semánticos distintos**. Un
   `<=>` entre ellos devuelve un número perfectamente válido y perfectamente sin
   sentido. Nadie ve un error: el RAG simplemente recupera peor. Es la misma
   familia de fallo invisible que documenta el [ADR 0152](0152-recall-vectorial-multitenant-hnsw.md).

### El precedente que ya existe dentro de casa

La memoria (`memory_entries`, 321 filas con vector en la instalación medida) usa
el **mismo** embedder y la **misma** dimensión, y **no tiene columna de modelo por
fila**: siempre usa el modelo de plataforma. Lleva así desde el Plan 04 sin que a
nadie le haya faltado el selector. La opción A no es una hipótesis: es lo que ya
funciona en la mitad del sistema que no inventó el campo.

## Decisión

**Opción A: un único modelo de embeddings por plataforma.** El modelo activo es
`settings.embedding_model` (`API_SERVER_EMBEDDING_MODEL`, ADR 0056), es 768
dimensiones, y es el que usan la ingesta, la memoria y el retrieval.

`knowledge_bases.embedding_model_id` **no desaparece**: cambia de naturaleza. Deja
de ser _entrada del usuario_ para ser **sello de la plataforma** — el registro de
con qué modelo se produjeron los vectores de esa KB. Es la única información que
la columna puede dar honestamente, y hace falta para saber qué reindexar el día
que el modelo cambie.

### El contrato ejecutable, en cinco reglas

Las cinco están implementadas y cada una tiene test; no son intenciones.

1. **Un único punto de resolución.**
   `api_server.ingestion.embedding_contract` normaliza (`:latest` fuera, alias
   `nomic-embed-text-v1.5` → `nomic-embed-text`) y responde a tres preguntas:
   cuál es el modelo activo, si dos referencias son el mismo modelo, y qué
   grafías aceptadas equivalen a una. Nadie compara strings de modelo por su
   cuenta.

2. **La API no acepta un modelo que la plataforma no vaya a usar.** Crear una KB
   con `embedding_model_id` distinto del activo → **422**, no un 201 que guarda
   una etiqueta decorativa. Omitirlo (el caso normal) sella el modelo activo. El
   `PUT` mantiene el **409** cuando la KB ya tiene chunks —re-sellar sin
   re-embeber sería mentir sobre los vectores existentes— y añade **422** para
   cualquier modelo que no sea el de la plataforma.

3. **Lo que la API enseña es lo que produjo los vectores.** La respuesta
   devuelve el sello **canonizado** (una KB heredada con `nomic-embed-text-v1.5`
   responde `nomic-embed-text`, que es literalmente el modelo que la embebió),
   más dos campos nuevos: `platform_embedding_model` (el activo) y
   `embedding_model_stale` (sello ≠ activo). La UI ya no puede enseñar una cosa
   distinta de la que pasa.

4. **La ingesta se niega antes que mezclar.** `ingest_document` compara el sello
   de la KB con el modelo activo **antes de embeber**. Si divergen, el documento
   termina en `failed` con un mensaje que nombra los dos modelos y el arreglo, y
   se emite el evento `kb.embedding_model_mismatch`. Antes, ese caso metía
   vectores de otro espacio en la KB sin decir nada.

5. **El retrieval no mezcla espacios semánticos.** El camino vectorial filtra por
   las grafías equivalentes al modelo activo (`kb.embedding_model_id = ANY(...)`),
   así que los chunks de una KB sellada con otro modelo **no compiten** con el
   vector de consulta. BM25 los sigue viendo, así que la KB no se vuelve invisible:
   pierde el camino vectorial, que es exactamente lo que ya había perdido de
   hecho.

### Qué NO entra en esta decisión

- **El exporter Prometheus del mismatch.** El plan de remediación excluye por
  escrito «exporters y alertas Prometheus (`prod-08`)». Aquí se entrega la
  **señal** y el estado visible; el contador y la alerta los cablea prod-08.
  La señal son dos cosas distintas, una por camino:

  - en **ingesta**, el evento estructurado `kb.embedding_model_mismatch`
    (nombre estable, con `kb_id`, `kb_model` y `active_model`) más el
    documento en `failed` con el motivo escrito en la ficha;
  - en **retrieval** no se emite evento por búsqueda —sería una consulta
    extra en cada llamada para contar algo que ya está en la API—: el estado
    lo expone `embedding_model_stale` en `GET /knowledge-bases`, que es lo
    que pinta la pantalla.

- **La migración de datos.** Ver «Consecuencias».

## Consecuencias

### Rompe (a propósito) dos comportamientos de la API

| Antes                                                                          | Ahora                                               |
| ------------------------------------------------------------------------------ | --------------------------------------------------- |
| `POST /knowledge-bases {"embedding_model_id": "text-embedding-3-small"}` → 201 | **422** — la plataforma no puede usar ese modelo    |
| `PUT` con otro modelo sobre KB vacía → 200 y se guarda                         | **422** salvo que sea el modelo de la plataforma    |
| `PUT` con otro modelo sobre KB con chunks → 409                                | **409** si es el modelo activo, **422** si no lo es |

El campo sigue en el esquema OpenAPI (no se rompe la forma del SDK); lo que cambia
es que ahora **significa algo**. Los dos tests de integración que bendecían el
comportamiento viejo (`test_kb_chunk_preview.py`) se reescriben: uno de ellos
afirmaba que se podía guardar `text-embedding-3-small`, un modelo de OpenAI que
esta plataforma —Ollama, 768 dims— no puede ejecutar. Es el caso que
[`verificar-antes-de-implementar.md`](../03-guides/verificar-antes-de-implementar.md#2-un-test-puede-fijar-el-defecto)
llama «un test puede fijar el defecto».

### Una KB sellada con otro modelo deja de aceptar documentos

Es el efecto buscado, y solo aparece si alguien **cambia
`API_SERVER_EMBEDDING_MODEL` con KBs ya indexadas**. El documento queda `failed`
con el motivo escrito en la ficha. El arreglo es el procedimiento de re-ingesta de
abajo, no revertir el env var a escondidas.

En la instalación medida esto no afecta a nadie: **0 documentos y 0 chunks**, y el
único sello existente (`nomic-embed-text-v1.5`) canoniza al modelo activo.

### Procedimiento de re-ingesta (cuando cambie el modelo de plataforma)

Cambiar el modelo **invalida los embeddings existentes**. No se hace por las
bravas:

1. Comprobar que el modelo nuevo es de **768 dims** (Admin → «Ollama &
   Embeddings», o `ingestion.embedding_models.recommended_models()`). Otra
   dimensión no es un cambio de env: es una migración de esquema y otro ADR.
2. `ollama pull <modelo>` en el Ollama del stack (el one-shot `ollama-bootstrap`
   lo hace con `EMBEDDING_MODEL` del compose).
3. Poner `API_SERVER_EMBEDDING_MODEL` **en todos los servicios de aplicación**
   (el generador del instalador ya lo inyecta a la vez en `api-server` y en
   `workers`; hay test que lo fija). Con los dos procesos desalineados, la
   api-server sella un modelo y el worker embebe con otro — que es justo el
   mismatch que la regla 4 hace visible.
4. Por cada KB con chunks: `POST /knowledge-bases/{kb}/documents/{doc}/reindex`
   para cada documento. La reingesta borra los chunks del documento y los vuelve
   a crear con el modelo nuevo.
5. Cuando la KB ya no tiene chunks, `PUT {"embedding_model_id": "<activo>"}`
   re-sella (con chunks devuelve 409 a propósito).

Mientras dure la operación, las KBs a medias pierden el camino vectorial y siguen
respondiendo por BM25. Es degradación anunciada, no silenciosa.

### Queda pendiente una migración de datos (otro carril)

El sello de las filas existentes se **canoniza en lectura**, así que la API ya no
miente hoy. Pero el valor guardado sigue siendo la etiqueta heredada, y eso es
deuda: quien lea la tabla a pelo (un backup, un script, un `psql`) verá
`nomic-embed-text-v1.5`. La migración que lo cierra es una sola sentencia y **no
la escribe este carril** (hay otra tarea creando migraciones en esta misma ola y
colisionarían en la cabeza de Alembic):

```sql
-- upgrade
UPDATE knowledge_bases
   SET embedding_model_id = 'nomic-embed-text'
 WHERE embedding_model_id = 'nomic-embed-text-v1.5';

ALTER TABLE knowledge_bases
  ALTER COLUMN embedding_model_id SET DEFAULT 'nomic-embed-text';

-- downgrade: reponer el default anterior; el UPDATE no se revierte
-- (revertirlo repondría una etiqueta que no es un tag válido de Ollama).
ALTER TABLE knowledge_bases
  ALTER COLUMN embedding_model_id SET DEFAULT 'nomic-embed-text-v1.5';
```

Es segura por construcción: en la instalación medida toca 14 filas **sin un solo
vector** detrás. Y es idempotente.

Un matiz sobre el `DEFAULT`: es sólo una red. Los cuatro sitios que crean KBs
—los dos routers, el seed de built-ins y la KB de docs internas de cada
proyecto— sellan explícitamente el modelo activo desde este ADR, precisamente
para que una instalación con otro embedder no fabrique KBs nacidas desfasadas
(que rechazarían documentos desde el primer día). El default sólo alcanza a un
`INSERT` a pelo.

### Caduca si aparece un caso multi-modelo real

Esta decisión se apoya en una medición (`1` valor distinto, `0` chunks). El día
que exista una necesidad escrita de dos modelos a la vez —por ejemplo un tenant
con un embedder propio— hay que reabrir el ADR hacia la opción B, no colar el
segundo modelo por la puerta de atrás.

## Alternativas consideradas

### Opción B — registro multi-modelo de verdad

Cada KB elige modelo; el retrieval agrupa la consulta **por modelo** (un vector de
consulta por modelo presente entre las KBs visibles), la dimensión se valida por
modelo y existe un job de reindexado explícito.

Descartada, y no por dogma:

- **No hay demanda medida.** Un valor distinto en toda la instalación. Construir
  el registro sería resolver un problema que nadie tiene, y el propio plan avisa:
  «B solo si existe un caso real multi-modelo».
- **El coste no está en el `if`.** Es N embeddings de consulta por búsqueda (uno
  por modelo), un `vector(N)` por dimensión o una tabla por modelo, y un pipeline
  de reindexado que hoy no existe. Sobre un stack de una sola máquina con un solo
  Ollama, es infraestructura para una capacidad decorativa.
- **La memoria seguiría siendo mono-modelo.** `memory_entries` no tiene el campo,
  así que B dejaría la mitad del RAG en A y la otra en B — dos contratos para el
  mismo `vector(768)`.

Si algún día se adopta, el trabajo de este ADR **no se tira**: el punto único de
resolución y el sello por KB son exactamente los cimientos que B necesita.

### Opción C — dejarlo como estaba y documentarlo como cosmético

Es el statu quo, y estaba **escrito**: el gotcha de naming decía «la columna sigue
defaulteando a la etiqueta `nomic-embed-text-v1.5` (es informativa…). No son el
mismo string a propósito; no los compares entre sí».

Descartada porque documentar una mentira no la convierte en verdad. Un campo que
la UI presenta como «modelo de embeddings de esta KB» y que no lo es engaña
exactamente igual con una nota al pie en un fichero que casi nadie abre. Y la nota
solo se sostenía mientras el modelo activo fuese nomic: con
`API_SERVER_EMBEDDING_MODEL=granite-embedding:278m`, las 14 fichas seguirían
diciendo `nomic-embed-text-v1.5` y no habría nada «cosmético» en eso. El gotcha se
ha reescrito con esta decisión.

## Referencias

- [ADR 0023 — Docling, embeddings y RAG](0023-docling-embeddings-rag.md)
- [ADR 0056 — Ollama en el stack, embeddings y GPU](0056-ollama-en-stack-embeddings-gpu.md)
- [ADR 0152 — Recall vectorial multi-tenant](0152-recall-vectorial-multitenant-hnsw.md)
- [Auditoría integral 2026-07-14](../roadmap/auditoria-integral-2026-07-14.md) — hallazgo AUD14-03
- [Plan de remediación](../roadmap/remediacion-auditoria-integral-2026-07-14.md) — `task_audit14_04`, `task_audit14_05`
- [Gotcha: el embedder pide `nomic-embed-text`](../03-guides/gotchas/ollama-embedding-model-naming.md)
- [Guía de ingesta de KBs](../03-guides/kb-ingestion.md)
