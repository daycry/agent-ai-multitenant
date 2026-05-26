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

## Tests humanos del plan

Los cinco tests humanos quedan pendientes de validar antes del
merge:

- `human_04_01` — un agente recupera contexto relevante de la
  memoria del equipo en una ejecución posterior.
- `human_04_02` — un humano sube un PDF, ve el progreso de
  ingestión en vivo y luego encuentra el contenido vía `rag_search`.
- `human_04_03` — la visualización de citas resalta los bboxes
  correctos al hacer click en una cita.
- `human_04_04` — `document_convert` + `promote_to_kb` desde el
  chat persiste un documento que aparece en la UI igual que un
  upload async.
- `human_04_05` — un agente con `memory_scope=private` no ve
  memorias de otros usuarios (cross-tenant + cross-user
  isolation).

## Próximo plan

Tras cerrar este plan, el siguiente es **Plan 05** — Multi-Agente
y Coordinación Avanzada.
