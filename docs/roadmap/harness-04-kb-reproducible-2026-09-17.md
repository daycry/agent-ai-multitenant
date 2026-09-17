---
plan_id: harness-04-kb-reproducible-2026-09-17
title: KB y RAG reproducibles — estado de disponibilidad, snapshot de recuperación y versionado
status: pending_approval
blocking_plan: [harness-01-contrato-run-capacidades-2026-09-17]
started_at: null
completed_at: null
estimated_duration_calendar: 1,5-2 semanas
estimated_effort_person_days: 6-8
estimated_cost_human_eur: 2.700 € – 4.800 €
estimated_cost_ai_eur: 30 € – 70 €
created_by: claude-fable-5-1-verificacion-auditoria-harness-2026-09-17
spec_sections_referenced: []
docs_language: es
priority: P1
source_audit: auditoria-harness-ciclo-kb-memoria-2026-09-17.md
---

# Plan harness-04 — KB y RAG reproducibles y conscientes de disponibilidad

## Cabecera

| Campo                 | Valor                                              |
| --------------------- | -------------------------------------------------- |
| **ID del Plan**       | `harness-04-kb-reproducible-2026-09-17`            |
| **Prioridad**         | P1                                                 |
| **Bloqueado por**     | `harness-01` (contrato de dependencias)            |
| **Coordinar con**     | `04`, `06.9`, `06.10`–`06.13` (cerrados), ADR 0102 |
| **Rama git sugerida** | `plan/harness-04-kb`                               |

> **Estado**: el gap analysis contra los planes KB existentes **ya está hecho** en la
> auditoría (filas 16, 17 y 19) y se recoge abajo. Este plan es el delta; no
> reimplementa ingestión, scopes ni catálogo.

## Gap analysis (hecho el 2026-09-17)

| Requisito del borrador                                  | Estado real                                                    | Qué hace este plan                                              |
| ------------------------------------------------------- | -------------------------------------------------------------- | --------------------------------------------------------------- |
| Ingestión con Docling, MIME, tamaño, ClamAV             | Existe (04, 06.11)                                             | Nada                                                            |
| Scopes tenant/proyecto/agente y RLS                     | Existe (06.9, `pending_human_validation`)                      | Añade test cross-tenant sobre **snapshot y cache**              |
| Catálogo y categorías                                   | Existe (06.10, 06.12, 06.13)                                   | Nada                                                            |
| `embedding_model_id` en `Chunk`                         | Existe                                                         | Añade `chunker_version`, versión de documento y `source_digest` |
| Procedencia en hits (`chunk_id`, `document_id`, scores) | Existe parcialmente                                            | Añade `document_version`, `content_digest`, `citation_id`       |
| Estado `EMPTY` ≠ `UNAVAILABLE`                          | **No existe** (`[]` en ambos casos)                            | `task_h04_01`                                                   |
| Snapshot reproducible de la recuperación                | **No existe**                                                  | `task_h04_03`, `task_h04_04`                                    |
| Check de prompt injection                               | Existe (`shared_guardrails/checks/prompt_injection.py`)        | Añade delimitación de fragmentos y corpus adversarial           |
| «Knowledge Gate» de ingestión                           | La ingestión ya pasa sólo por la API; el runtime no escribe KB | Se reduce a un test sentinela (`task_h04_06`)                   |

## Estados de recuperación

```text
AVAILABLE_WITH_RESULTS | AVAILABLE_EMPTY | UNAVAILABLE | DEGRADED | STALE | FORBIDDEN | INVALID_QUERY
```

## Tareas

### Fase A — Disponibilidad explícita

#### `task_h04_01` — La KB devuelve estado, no sólo lista

- [ ] **Título**: `POST /internal/agent/rag-search` responde
      `{state, hits, backend_version, embedding_model_id, latency_ms}`; un agente sin
      proyecto → `FORBIDDEN` (hoy `hits=[]` 200); timeout/5xx del backend →
      `UNAVAILABLE` (503 con cuerpo tipado); backend sano sin coincidencias →
      `AVAILABLE_EMPTY`. En el runtime, el prefetch de `__main__.py` deja de tragar
      la excepción y devolver lista vacía: propaga el estado al `dependency_report`
      (harness-01) y al preámbulo («la base de conocimiento no respondió; trabaja
      sin ella» vs «no hay documentos relevantes»). Con el requisito de
      conocimiento en `REQUIRED`, `UNAVAILABLE` aborta antes de llamar al modelo. La
      tool `rag_search` explícita devuelve el mismo estado en su `ToolResult.metadata`.
      **Test**: `tests/integration/test_knowledge_dependency_states.py`;
      `docker/agent-runtimes/agent-runtime/tests/test_rag_prefetch_reports_state.py`.
      **Coste**: 1,5 d.

### Fase B — Versionado y snapshot

#### `task_h04_02` — Identidad inmutable de documento y chunk

- [ ] **Título**: `documents` gana `version` (entero, sube con cada reindexado),
      `source_digest` (SHA-256 del fichero) y `superseded_by_id` nullable; `chunks`
      gana `document_version`, `chunker_version` y `content_digest`. Reindexar **no
      sobrescribe**: crea chunks de la versión nueva y marca la anterior
      `superseded`; el GC (`knowledge_gc.py`) sólo borra versiones antiguas que
      ningún snapshot referencia. Migración aditiva con backfill (`version=1`,
      digest calculado en un beat).
      **Test**: `tests/integration/test_knowledge_document_versioning.py`.
      **Coste**: 1,5 d.

#### `task_h04_03` — `knowledge_retrieval_snapshots`

- [ ] **Título**: tabla tenant-aware: `execution_id`, `checkpoint_sequence` nullable,
      `origin` (prefetch o tool), `query_digest`, `filters` (scopes efectivos),
      `backend_version`, `embedding_model_id`, `reranker_version` nullable, `latency_ms`,
      `state`, `candidates` (ids + versiones + scores) y `selected` (ids +
      `content_digest` + `citation_id`). No duplica contenido: referencia
      `(chunk_id, document_version)`. Se escribe desde el endpoint interno en la
      misma transacción de la búsqueda.
      **Test**: `tests/integration/test_knowledge_retrieval_snapshot.py`.
      **Coste**: 1,5 d. **Depende de**: task_h04_02.

#### `task_h04_04` — Replay del bloque de contexto

- [ ] **Título**: `assemble_knowledge_block(snapshot_id)` reconstruye el texto
      exacto que vio el modelo a partir de `(chunk_id, document_version)`; si una
      referencia fue purgada declara `PARTIAL` y lista los `citation_id` ausentes.
      El digest del bloque ensamblado se guarda en el snapshot para compararlo en
      harness-06.
      **Test**: `tests/integration/test_knowledge_snapshot_replay.py`. **Coste**: 1 d.
      **Depende de**: task_h04_03.

### Fase C — Contenido no confiable

#### `task_h04_05` — Los fragmentos son datos, no instrucciones

- [ ] **Título**: el preámbulo delimita cada fragmento con marcadores y su
      `citation_id` (etiqueta `<kb id v>` … `</kb>`), con la instrucción explícita de
      que el contenido es datos. El check `prompt_injection` corre en `pre_llm` **por
      fragmento** (hoy corre sobre el prompt entero) y registra qué `citation_id`
      causó bloqueo o redacción. Corpus adversarial ES/EN en
      `packages/shared-guardrails/tests/fixtures/rag_injection/`.
      **Test**: `tests/security/test_rag_prompt_injection.py`;
      `docker/agent-runtimes/agent-runtime/tests/test_kb_fragments_are_delimited.py`.
      **Coste**: 1 d.

#### `task_h04_06` — Sentinela: el runtime no escribe en la KB

- [ ] **Título**: test que falla si aparece en `agent_runtime/` cualquier llamada a un
      endpoint de escritura de KB o una tool que la exponga. Hoy es cierto; el test
      lo fija.
      **Test**: `docker/agent-runtimes/agent-runtime/tests/test_runtime_never_writes_kb.py`.
      **Coste**: 0,25 d.

### Fase D — Fallos y aislamiento

#### `task_h04_07` — Caos y cross-tenant sobre prefetch, tool, cache y snapshot

- [ ] **Título**: cortar el backend, introducir timeout y devolver respuesta parcial
      o malformada; afirmar `UNAVAILABLE` / `DEGRADED` / `AVAILABLE_EMPTY` distintos
      en API, `dependency_report` y preámbulo. Intentar scopes de otro
      tenant/proyecto/agente en `rag_search`, en el snapshot y en la cache de
      embeddings. Ejecutar por auto-RAG y por tool explícita.
      **Test**: `tests/e2e/test_knowledge_backend_failure_modes.py` (runner Docker);
      `tests/security/test_knowledge_cross_tenant_isolation.py`. **Coste**: 1 d.
      **Depende de**: task_h04_01, task_h04_03.

## Criterios de cierre

- [ ] Empty y unavailable son estados distintos en API, runtime, `dependency_report` y visor.
- [ ] Una tarea con KB `REQUIRED` no sigue sin ella.
- [ ] Cada run conserva el snapshot de fragmentos usados.
- [ ] Un replay reconstruye el mismo bloque de contexto o declara `PARTIAL`.
- [ ] Documento y chunk son versionados; reindexar no destruye lo que un snapshot referencia.
- [ ] El corpus de inyección no modifica policy ni tools.
- [ ] Tests cross-tenant cubren prefetch, tool, cache y snapshot.

## Riesgos

1. **Snapshots voluminosos**: sólo referencias e ids; contenido por `(chunk_id, version)`.
2. **Replay imposible tras borrado legal**: `PARTIAL` declarado; metadatos mínimos.
3. **GC agresivo**: el GC consulta snapshots antes de borrar versiones.
4. **Confundir KB con memoria**: KB = conocimiento curado; memoria = experiencia gobernada (harness-05).

## Pruebas humanas

```yaml
- id: human_h04_01
  title: KB vacía vs KB caída
  steps:
    - Consulta sin coincidencias con backend sano.
    - Repetir con el backend detenido.
    - Ver el visor de la ejecución.
  expected: AVAILABLE_EMPTY en el primer caso; UNAVAILABLE y la política declarada en el segundo.

- id: human_h04_02
  title: Reproducir el contexto histórico
  steps:
    - Ejecutar una tarea y anotar su snapshot.
    - Reindexar el documento con contenido nuevo.
    - Reproducir el bloque de contexto desde el snapshot.
  expected: Se usa la versión histórica; mismo digest de contexto.
```
