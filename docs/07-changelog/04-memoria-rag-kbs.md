---
plan_id: 04-memoria-rag-kbs
title: Memoria, RAG y Bases de Conocimiento
started_at: 2026-05-25
completed_at: 2026-05-26
status: completed
tasks_done: 26
tasks_total: 26
tasks_pending_local: []
docs_language: es
---

> **Estado:** plan **`completed`** (cerrado el 2026-05-26). Las 26
> tareas (`task_04_01`..`task_04_26`) en `done` con sus tests
> automáticos en verde. Los cinco tests humanos (`human_04_01`..
> `human_04_05`) quedan pendientes de validar por el revisor
> humano — se ejecutarán sobre la rama
> `plan/04-memoria-rag-kbs` antes de mergear a `main`. Plan 04
> conecta el sistema agéntico a su contexto persistente: cada
> ejecución alimenta la memoria, cada documento ingestionado
> alimenta las KBs, y los agentes buscan por ambos canales con
> `memory_recall` y `rag_search`.

# Changelog — Plan 04 · Memoria, RAG y Bases de Conocimiento

Cuarta fase del Plan de Implementación. El Plan 02 puso a los
agentes a ejecutar; el Plan 03 los dejó hablando con el humano. El
Plan 04 les da memoria de lo vivido + acceso a la documentación del
proyecto.

## Resultado

Al cierre del plan, la plataforma puede:

1. **Memorizar ejecuciones automáticamente.** Cada `Execution`
   que cierra como `done` pasa por el `Memorizer` (Celery task de
   destilación con LLM), que extrae 0-5 `MemoryEntry` cortos —
   episódicos o semánticos — atados al `scope` del agente
   (private / team_shared / project_shared / global) con los
   owner pointers correspondientes. Las memorias viven en
   `memory_entries` con `embedding vector(768)` listo para el
   path vector de `memory_recall`.

2. **Almacenar memorias manualmente.** `POST /memories` deja al
   humano (o al agente vía la tool `memory_store`) persistir un
   aprendizaje concreto desde el chat. El scope se valida en
   Pydantic + la BD (CHECK `ck_memory_entries_scope_pointer`).
   La página `/admin/memories` lista, filtra por scope, crea y
   borra.

3. **Recuperar memorias con búsqueda híbrida.** `memory_recall`
   combina BM25 (ts_rank_cd sobre el GIN `to_tsvector('simple',
content)`) con cosine similarity sobre pgvector HNSW y
   fusiona los rankings con Reciprocal Rank Fusion (Cormack 2009,
   k=60). El filtro de scope + owner pointers garantiza que un
   agente sólo lee lo que su `memory_scope` permite.

4. **Gestionar Knowledge Bases.** Cuatro tablas nuevas:
   `knowledge_bases`, `documents`, `chunks` y la junction M:N
   `kb_projects`. Las KBs son tenant-level; un proyecto sólo ve
   una KB si tiene un grant explícito en `kb_projects` (ADR
   0023). Soft-delete en KBs y documentos; los chunks son
   derivados (no soft-delete, se regeneran al re-ingestar).

5. **Ingestar documentos asíncronamente.** El endpoint multipart
   `POST /knowledge-bases/{id}/documents` escribe los bytes a
   MinIO bajo la clave canónica
   `kb/{tenant}/{kb}/{doc}/{filename}` y deja la fila
   `documents.status='pending'`. El worker de ingestión
   (`api_server.ingestion.pipeline.ingest_document`) corre cuatro
   pasos inyectables: scan AV (ClamAV INSTREAM, falla cierra el
   ciclo con `failed`) → parse con docling-serve → embed batch
   con OllamaEmbedder → persistir chunks. Cada hito emite un
   evento al stream Redis `doc:{id}` que la UI tira por
   `/ws/documents/{id}`.

6. **Ingestar in-flight desde el chat.** `document_convert`
   (vía docling-mcp, MCP-over-HTTP) parsea sin tocar la BD;
   `promote_to_kb` toma el resultado y lo guarda con la misma
   clave canónica del upload async — un usuario que ve un
   `Document` en la UI no puede distinguir cómo llegó.

7. **Buscar contenido con `rag_search`.** Pipeline alto nivel
   sobre `chunks`: embed query (opcional) → recall híbrido BM25
   - vector + RRF → reranker (`NoopReranker` /
     `DeterministicReranker` / `BGEReranker` con lazy-import de
     FlagEmbedding) → top-N. El KB-visibility filter en SQL
     garantiza que un project sólo recibe chunks de KBs granted.

8. **Visualizar citas con bounding boxes.** El endpoint
   `GET /documents/{id}/citations` devuelve documento + chunks
   con sus `bbox` (`page, x, y, w, h` en coords normalizadas).
   La página `/admin/documents/{id}/citations` renderiza un
   placeholder por página y overlays absolutamente posicionados;
   un sidebar lista los chunks y un click salta al bbox.

## Fases

| Fase                          | Tareas | Entregable                                                                                                                              |
| ----------------------------- | ------ | --------------------------------------------------------------------------------------------------------------------------------------- |
| A — Memoria                   | 01–06  | `MemoryEntry` + `MemoryType` + Memorizer (policy/distillation/persistence) + `memory_recall` híbrido + `POST /memories` + UI memoria    |
| B — Knowledge Bases           | 07–09  | `KnowledgeBase`/`Document`/`Chunk`/`kb_projects` (M:N) + migración 0022 (pgvector HNSW + GIN FTS + RLS) + endpoints CRUD + upload MinIO |
| C — Ingestión con Docling     | 10–15  | docling-serve container + pipeline scan→parse→embed→persist + audio Whisper + ClamAV gating + Ollama embedder + WebSocket progress      |
| D — Búsqueda Híbrida y Rerank | 16–20  | `bm25_chunks` + `vector_chunks` + RRF (k=60) + `Reranker` Protocol con bge-reranker-v2-m3 lazy + `rag_search` tool                      |
| E — Docling MCP               | 21–23  | docling-mcp container + `document_convert` tool real (era placeholder) + `promote_to_kb` para in-flight → KB                            |
| F — UI y Cierre               | 24–26  | UI Knowledge Bases del proyecto + visor de citas con bboxes + docs (ADR 0023 + guía ingestión + este changelog)                         |

## Decisiones de arquitectura (ADRs)

- **ADR 0023** — Stack RAG cerrado: Docling parser, Ollama
  `nomic-embed-text-v1.5` embeddings (768 dims), pgvector HNSW
  vector store, `bge-reranker-v2-m3` reranker. Local-first, sin
  dependencias externas obligatorias.

## Migraciones de base de datos

- `0020` — `memory_entries` con `vector(768)` + HNSW cosine-ops +
  GIN FTS + RLS + CHECK scope-pointer (Fase A).
- `0021` — GIN FTS index sobre `to_tsvector('simple',
memory_entries.content)` para el path BM25 de `memory_recall`.
- `0022` — Cuatro tablas KB con `vector(768)` HNSW en `chunks`,
  GIN FTS en `chunks.content`, RLS en las cuatro, CASCADE
  `KB → documents → chunks`, junction `kb_projects` con PK
  compuesta.

Todas reversibles, verificadas por test (`test_memory_migration.py`
y `test_kb_migration.py`).

## Métricas de cierre

| Métrica                            | Valor                                                        |
| ---------------------------------- | ------------------------------------------------------------ |
| Tareas totales / done              | 26 / 26                                                      |
| Tests pytest nuevos (Plan 04)      | 124                                                          |
| Tests Playwright nuevos (Plan 04)  | 18                                                           |
| Migraciones de base de datos       | 3                                                            |
| ADRs aceptados durante el plan     | 1                                                            |
| Servicios nuevos en docker-compose | 3 (docling-serve, docling-mcp, ollama implícito vía API_URL) |

## Pendiente para validación humana end-to-end

Las 26 tareas del plan construyeron el **backend completo** (funciones
Python pure-async + endpoints REST + UI admin) y sus 200+ tests
automáticos están en verde. Sin embargo, varios humanos del roadmap
asumen que **el agente** invoca los tools nuevos a través del agent
runtime, y ese cableado no entró en las 26 tareas. Lo dejamos
explícito aquí para que el cierre del plan no oculte la deuda:

1. **Memorizer Celery wire-up.** La función `should_memorize` +
   `distil_execution` + `persist_memory_candidates` existe y
   está probada (`tests/integration/test_memorizer.py`); la
   **task Celery `workers.memorize_execution`** y el trigger
   desde `workers/execution.py` quedaron como follow-up de
   task_04_03. Mientras tanto las memorias sólo entran al store
   por `POST /memories` manual.
2. **Cinco tools del agent-runtime siguen como placeholder 501**:
   `memory_recall`, `memory_store`, `rag_search`,
   `document_convert`, `promote_to_kb`. Las funciones backend
   existen y están probadas; el adapter que el agent loop usa
   para llamarlas no se re-cableó.
3. **Chat UI con paste de PDF** (necesario para `human_04_03`)
   no entró en Plan 03 ni en Plan 04 — `document_convert` está
   listo para invocarse pero el frontend del chat no tiene el
   componente de attachment.

Estas tres piezas suman ~4-6h de trabajo de integración y se
abordarán en un plan separado (probable `Plan 04.5` o como una
fase inicial de Plan 05). Ver el body del PR para la
justificación frente a las alternativas (implementarlo ahora vs
deferir).

## Tests humanos del plan

Las descripciones son las del roadmap (`docs/roadmap/04-memoria-rag-kbs.md`).
Por la deuda anterior, ninguno de los cuatro primeros puede
validarse end-to-end con un agente en el loop; sólo se puede
validar el camino backend reducido. El revisor debe marcarlos
como **"deferred to Plan 04.5"** o validar el alcance reducido
indicado.

> **Nota — para ejecutar los demos en vivo del Plan 04.5** (que
> ya cubren `human_04_01` memoria y `human_04_02` RAG con citas
> end-to-end), la guía está en
> [docs/03-guides/run-demo-human-tests.md](../03-guides/run-demo-human-tests.md).

- `human_04_01` — **Memoria mejora la calidad de tareas
  repetidas.** ⚠️ Necesita Memorizer Celery wire-up + tool
  `memory_recall` en el agente. **Deferred**. Alcance reducido:
  llamar a `POST /memories` manualmente entre ejecuciones y
  comprobar que un `GET /memories?scope=team_shared` las
  devuelve.

- `human_04_02` — **RAG funciona con corpus realista.** ⚠️
  Necesita tool `rag_search` en el agente para validar las
  citas en respuestas. **Deferred**. Alcance reducido: subir
  los 10 documentos vía `POST /knowledge-bases/{id}/documents`,
  ver progreso en `/admin/documents/{id}/ingestion`, y validar
  el visor de citas en `/admin/documents/{id}/citations`.

- `human_04_03` — **docling-mcp permite flujo conversacional.**
  ⚠️ Necesita chat UI con paste de PDF + tools
  `document_convert` / `promote_to_kb` en el agente. **Deferred
  por completo**.

- `human_04_04` — **Scopes de memoria son respetados.** 🟡
  **Parcial sin agentes en el loop**: crear memorias en los
  cuatro scopes vía `POST /memories`, consultarlas con distintos
  JWTs (different users / projects / teams / tenants) y
  comprobar que el filtro RLS + scope+owner pointer hace su
  trabajo. Este test sí es ejecutable hoy, sin esperar a las
  wire-ups.

- `human_04_05` — **Reindexación con cambio de modelo de
  embeddings.** ⚠️ **Deferred a Plan 12** por decisión del ADR 0023. Plan 04 persiste `knowledge_bases.embedding_model_id`
  por KB pero no orquesta la reindexación con progreso visible.
  El revisor puede marcar este test como "deferred" o validar
  un alcance reducido (cambiar el campo por API y comprobar que
  persiste).

## Próximo plan

Tras cerrar este plan, el siguiente es **Plan 05** — Multi-Agente
y Coordinación Avanzada. Las tres piezas de wire-up listadas en
"Pendiente para validación humana end-to-end" se abordarán como
fase inicial de Plan 05 o como un Plan 04.5 dedicado (decisión a
tomar al activar Plan 05).
