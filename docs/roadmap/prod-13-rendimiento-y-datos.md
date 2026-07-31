---
plan_id: prod-13-rendimiento-y-datos
title: Rendimiento y gestión de datos — event loop, pool, retención e índices
status: pending_approval
blocking_plan: null
started_at: null
completed_at: null
estimated_duration_calendar: 3-4 semanas
estimated_effort_person_days: 21
estimated_cost_human_eur: 9.500 € – 12.600 €
estimated_cost_ai_eur: 60 € – 120 €
created_by: auditoria-claude-2026-06
spec_sections_referenced: []
docs_language: es
priority: P1
---

# Plan prod-13 — Rendimiento y gestión de datos: event loop, pool, retención e índices

## Cabecera

| Campo                              | Valor                                                            |
| ---------------------------------- | ---------------------------------------------------------------- |
| **ID del Plan**                    | `prod-13-rendimiento-y-datos`                                    |
| **Prioridad**                      | P1                                                               |
| **Bloqueado por**                  | — (independiente; coordinación con prod-04/05/06/10, ver tareas) |
| **Tiempo estimado (calendario)**   | 3-4 semanas                                                      |
| **Tiempo estimado (persona-días)** | 21                                                               |
| **Rama git sugerida**              | `plan/prod-13-rendimiento-y-datos`                               |

> **Estado**: la fuente de verdad es el frontmatter YAML de este fichero (`status:`). El campo duplicado que había en esta tabla se retiró en prod-15 (hallazgo docsroadmap-6): se había desincronizado en 22 de 51 planes.

---

## Resumen

La auditoría de producción (2026-06-10) confirmó que los hot paths están bien
construidos (paginación con límites duros, stats en SQL, sin N+1, streams por
entidad), pero detectó tres frentes que en producción degradan o tumban la
plataforma entera:

1. **Event loop bloqueado**: la actualización de marketplace ejecuta bandit/semgrep
   por `subprocess.run` síncrono (hasta 2×120 s) y un sandbox Docker síncrono dentro
   de handlers async (perf-1); los endpoints de backup llaman boto3/paramiko/rclone
   síncronos (api-3); las lecturas hvac/Vault son síncronas y sin timeout en el path
   del chat del asistente (perf-7); el upload de documentos materializa el fichero
   entero en RAM antes del check de 50 MiB (api-2). Cualquiera de ellos congela
   TODAS las requests y WebSockets del api-server.
2. **BD sin tuning ni retención**: el pool asyncpg va con defaults (15 conexiones)
   y la transacción por request se retiene durante el turno LLM completo —
   ~15 chats concurrentes agotan el pool y toda la API falla (perf-2/db-2). El
   índice FTS de chunks no casa con la query `es_unaccent` (seq scan, perf-3),
   no existe purga de soft-deleted ni retención de append-only (db-4), faltan
   índices ((tenant_id, created_at) en executions, unicidad por tenant en
   teams/skills/agents) y el HNSW global degrada el recall multi-tenant (db-6).
3. **Endpoints sin válvulas**: listados sin paginar que arrastran MBs (api-6,
   perf-6, perf-8), `/ws/kanban` re-lee el stream global de 10k eventos por socket
   (perf-5), `/assistant/chat` sin rate limit (api-4), transiciones de estado sin
   `FOR UPDATE` (api-10) y errores crudos de PostgreSQL expuestos al cliente (api-5).

Este plan cierra los 23 hallazgos asignados (perf-1..11, db-2/4/6/7/8/9,
api-2/3/4/5/6/10) en cinco fases: A (event loop), B (pool y transacciones),
C (índices y búsqueda), D (retención y backfill), E (endpoints).

## Alcance

**Entra**:

- Desbloquear el event loop del api-server: marketplace a Celery/`to_thread`,
  backup y Vault con `to_thread` + timeouts, upload en streaming con rechazo
  temprano, clientes httpx/embedder compartidos.
- Tuning del pool async (settings explícitos) y rediseño de la transacción por
  request para NO abarcar llamadas LLM/embeds.
- Migraciones: índice FTS de chunks a `public.es_unaccent`, índice
  `(tenant_id, created_at)` en executions, unicidad parcial por tenant en
  teams/skills/agents, `BigInteger` en `source_size_bytes`.
- Jobs beat de purga de soft-deleted (ventana de gracia configurable), retención
  de append-only (`steps_log`, `audit_log`, `guardrail_events`, `notifications`)
  y backfill de chunks sin embedding.
- Paginación de conversaciones/documentos/citas, `deferred()` de columnas
  pesadas, `/ws/kanban` por canal de proyecto, rate limit en `/assistant/chat`,
  caché Redis de catálogos calientes, `FOR UPDATE` en transiciones críticas y
  saneamiento de respuestas 409.

**Queda fuera** (cubierto por otros planes de la serie o pospuesto):

- Cableado de auto-pausa/alertas de presupuesto (db-1) → **prod-06**. Aquí solo
  se prepara el índice que ese sweep necesitará (db-7).
- Orden blob/commit en `delete_document` (db-3) y dispatch de proyectos
  soft-borrados (db-5) → **prod-06**; la purga de este plan (task_prod13_14)
  asume que ese fix llega antes o en paralelo.
- Antivirus fail-open (api-1) → **prod-12** (hardening de ingesta).
- Operabilidad de Vault, AppRole y defaults (→ **prod-10**); aquí solo timeout +
  `to_thread` + caché del secreto (perf-7).
- Restore/backup funcional de verdad (→ **prod-04**); aquí solo el no-bloqueo
  del event loop en sus endpoints (api-3).
- Particionado físico de `chunks` por tenant o índices HNSW por tenant — se
  documenta como ADR propuesto, no se implementa (db-6 se mitiga con
  `iterative_scan`/`ef_search`).

## Decisiones clave

1. **Marketplace: Celery vs `to_thread`** (perf-1). Opciones: (a) mitigación
   mínima con `asyncio.to_thread` alrededor de `_run_static_analysis`/`_run_sandbox`;
   (b) mover las puertas a una task Celery en cola dedicada con endpoint 202 +
   estado consultable. **Recomendación: (b)**, con (a) como paso intermedio si se
   necesita hotfix; 4 minutos de análisis no pertenecen a un request HTTP aunque
   no bloquee el loop (timeouts de proxy, reintentos del cliente).
2. **Transacción vs LLM** (perf-2/db-2). Opciones: (a) cerrar la transacción antes
   del turno LLM y reabrir sesión para persistir; (b) pasar un _session-factory_
   a las tools del asistente en vez de la sesión viva. **Recomendación: (b)** —
   cada tool abre/cierra su sesión corta (con `set_config` de tenant), el request
   no retiene conexión durante el I/O externo y el patrón sirve igual para el
   embed de `/knowledge-bases/{id}/search`.
3. **Retención de `audit_log`** (db-4). Cuánto tiempo retener auditoría y si los
   runs antiguos se archivan a MinIO antes de compactar `steps_log` es una
   decisión de producto/cumplimiento: **se redacta ADR propuesto**
   (`docs/05-architecture-decisions/`) con opciones (borrado puro a N meses /
   archivado a MinIO + borrado / retención infinita con particionado) y el humano
   decide. La purga de soft-deleted (ventana 30 días configurable) NO requiere
   ADR: la semántica "recuperable durante la gracia" ya está prometida en docstrings.
4. **Valores de pool por defecto** (db-2). Propuesta para single-host:
   `pool_size=10`, `max_overflow=20`, `pool_timeout=10`, `pool_recycle=1800`,
   expuestos como settings de entorno (api-server y admin engine). Se valida con
   la métrica de pool de SQLAlchemy antes de cerrar la fase.
5. **HNSW multi-tenant** (db-6). Ahora: `SET hnsw.iterative_scan = relaxed_order`
   (pgvector ≥ 0.8) en la sesión de búsqueda + `ef_search` configurable + test de
   recall con tenants desbalanceados. Índices parciales/particionado → ADR
   propuesto, no se implementa en este plan.
6. **Caché con TTL corto, invalidación explícita** (perf-10). Membership y
   platform_settings se cachean en Redis con TTL ≤ 60 s e invalidación al
   escribir. Ante la duda entre frescura y rendimiento, gana la frescura: un rol
   revocado no puede sobrevivir más que el TTL.

## Tareas

### Fase A — Event loop sin bloqueos

#### `task_prod13_01` — Marketplace: puertas de análisis y sandbox fuera del event loop

- [ ] **Título**: Mover `_run_static_analysis` (subprocess bandit/semgrep,
      `static_analysis.py:480-486`) y `_run_sandbox` (SDK Docker síncrono,
      `sandbox.py:419,433-435`) fuera del loop: task Celery en cola dedicada,
      endpoint de instalación/actualización devuelve 202 + recurso de estado
      consultable; `asyncio.to_thread` como mitigación intermedia en
      `marketplace/install.py:514-517,559`.
  - ⏳ **Pendiente (2026-07-31):** solo está la mitigación intermedia — las dos
    puertas ya corren fuera del bucle (verificado en
    `tests/unit/test_no_blocking_calls_in_event_loop.py`); falta la task Celery en
    cola dedicada y el endpoint que devuelve 202 + recurso de estado consultable.
- **Tiempo**: 2 días · **Complejidad**: l
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_01_a
    runtime: python-pytest
    command: "pytest tests/integration/test_marketplace_async_gates.py -v"
  - id: auto_prod13_01_b
    runtime: python-pytest
    command: "pytest tests/unit/test_marketplace_no_sync_subprocess_in_async.py -v"
  ```

#### `task_prod13_02` — Backup: boto3/paramiko/rclone con `to_thread` y timeouts

- [ ] **Título**: Envolver `destination.test_connectivity()` (`routers/backup.py:230`)
      y `destination.list_remote()` (`backup.py:361`, bucle de `_list_remote_backups`)
      en `asyncio.to_thread`, con timeouts de conexión explícitos y cortos en los
      adaptadores de `workers/backup_destinations.py` cuando se invocan desde el
      api-server. **Coordinación**: prod-04 reescribe el backup y api-9 (frontera
      apps) puede mover esto a Celery — este task garantiza solo el no-bloqueo.
  - ⏳ **Pendiente (2026-07-31):** el `to_thread` de las dos llamadas está y se
    verifica (`tests/unit/test_no_blocking_calls_in_event_loop.py`); faltan los
    timeouts de conexión explícitos y cortos en los adaptadores de
    `workers/backup_destinations.py` (solo hay `timeout_s=3600` en rclone).
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_02_a
    runtime: python-pytest
    command: "pytest tests/integration/test_backup_endpoints_nonblocking.py -v"
  ```

#### `task_prod13_03` — Vault: timeout explícito, `to_thread` y caché del secreto

- [x] **Título**: Pasar `timeout` corto al `hvac.Client` (`routers/llm_providers.py:116`),
      envolver `vault.read_secret` en `asyncio.to_thread` dentro de
      `build_llm_provider` (`llm_providers/factory.py:175-183`) y cachear la
      credencial por `provider_id` con TTL corto (30-60 s) para no ir a Vault en
      cada mensaje del chat. **Coordinación**: prod-05 (rotación) debe invalidar
      esta caché al rotar; prod-10 (Vault operable) hereda el timeout.
  - ✅ **Cerrada (2026-07-31):** la mitad del panel ya estaba (el `timeout=5 s` del
    `hvac.Client` y el `to_thread` de las cuatro llamadas al store); ahora también
    la del CHAT — `build_llm_provider` lee el secreto por `asyncio.to_thread` y lo
    cachea EN PROCESO por `provider_id` con TTL de 30 s (el extremo bajo del rango,
    porque lo rancio aquí es una credencial). Un fallo de Vault NO se cachea. El
    gancho que prod-05 debe llamar al rotar es
    `factory.invalidate_provider_secret_cache(provider_id)`; hasta que lo llame, el
    TTL acota la ventana. La credencial no va a Redis a propósito (ADR 0028: no se
    crea una segunda copia del secreto fuera del proceso que ya lo tiene).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_03_a
    runtime: python-pytest
    command: "pytest tests/unit/test_vault_timeout_and_cache.py -v"
  ```

#### `task_prod13_04` — Upload de documentos en streaming con rechazo temprano

- [ ] **Título**: En `POST /knowledge-bases/{kb_id}/documents`
      (`routers/knowledge_bases.py:579-598`): rechazar por header `Content-Length`
      antes de leer, leer en chunks acumulando hasta `MAX_UPLOAD_BYTES+1` (nunca
      `file.read()` completo), y validar content-type/extensión contra la lista
      de formatos soportados por Docling. Reutilizar el patrón ya existente en
      `incoming_webhooks.py:160-171`.
  - ⏳ **Pendiente (2026-07-31):** sin empezar — `knowledge_bases.py:604` sigue
    haciendo `payload = await file.read()` y comprobando el tamaño DESPUÉS, sin
    rechazo por `Content-Length` ni lectura por chunks.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_04_a
    runtime: python-pytest
    command: "pytest tests/integration/test_document_upload_limits.py -v"
  ```

#### `task_prod13_05` — Cliente httpx/embedder compartido en los hot paths internos

- [ ] **Título**: Sustituir el `OllamaEmbedder()` nuevo por request
      (`docs_viewer.py:125-138`, `internal_agent.py:198,295`,
      `ingestion/embeddings.py:86-89`) por un `httpx.AsyncClient` singleton de
      proceso (mismo patrón `lru_cache` que `get_redis`), con keep-alive hacia Ollama.
  - ⏳ **Pendiente (2026-07-31):** solo `docs_viewer.py` usa el cliente
    compartido (`get_shared_embed_client`, verificado); siguen construyendo un
    `OllamaEmbedder()` por llamada `chat/responder.py:976`,
    `docs_structure/kb_sync.py:222,337` y `seeds/catalog_ingestion.py:185`.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_05_a
    runtime: python-pytest
    command: "pytest tests/unit/test_shared_embedder_client.py -v"
  ```

### Fase B — Pool y transacciones

#### `task_prod13_06` — Tuning explícito del pool async como settings

- [ ] **Título**: Exponer `pool_size`/`max_overflow`/`pool_timeout`/`pool_recycle`
      como settings de entorno y aplicarlos en `get_engine` y `get_admin_engine`
      (`db/session.py:21-58`), con los defaults de la decisión clave 4 y métrica
      de saturación del pool expuesta (coordinación con prod-08 para la alerta).
  - ⏳ **Pendiente (2026-07-31):** los cuatro settings existen con los defaults
    de la decisión clave 4 y llegan a los DOS engines (verificado en
    `tests/unit/test_engine_pool_settings.py`); falta exponer la métrica de
    saturación del pool — el api-server no publica métricas todavía.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_06_a
    runtime: python-pytest
    command: "pytest tests/unit/test_engine_pool_settings.py -v"
  ```

#### `task_prod13_07` — La transacción por request no abarca llamadas LLM ni embeds

- [ ] **Título**: Implementar la decisión clave 2: `POST /assistant/chat`
      (`routers/assistant.py:171-199`) resuelve datos, cierra la transacción de
      `open_tenant_session` (`auth/deps.py:242-252`) y ejecuta `run_assistant_turn`
      sin conexión retenida — las tools reciben un session-factory tenant-aware en
      `tool_ctx` y abren sesiones cortas; `GET /knowledge-bases/{id}/search`
      (`knowledge_bases.py:294-301`) embebe contra Ollama fuera de la sesión.
      Persistencia del resultado en sesión nueva.
  - ⏳ **Pendiente (2026-07-31):** sin empezar — la sesión de
    `open_tenant_session` sigue viva durante `run_assistant_turn` y las tools no
    reciben ningún session-factory tenant-aware en `tool_ctx`.
- **Tiempo**: 2,5 días · **Complejidad**: l
- **Depende de**: `task_prod13_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_07_a
    runtime: python-pytest
    command: "pytest tests/integration/test_assistant_no_tx_during_llm.py -v"
  - id: auto_prod13_07_b
    runtime: python-pytest
    command: "pytest tests/integration/test_assistant_tools_session_factory.py -v"
  ```

#### `task_prod13_08` — `NullPool` en los engines por tarea Celery

- [x] **Título**: Pasar `poolclass=NullPool` a los `create_async_engine` de las
      tareas Celery (`workers/ingestion.py:127,192`, `workers/maintenance.py:287`)
      para que cada tarea cueste exactamente 1 conexión sin pool ocioso (perf-11).
- **Tiempo**: 0,25 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_08_a
    runtime: python-pytest
    command: "pytest tests/unit/test_worker_engines_nullpool.py -v"
  ```

#### `task_prod13_09` — Seed runner: una transacción por seed

- [ ] **Título**: Trocear `python -m api_server.seeds` (`seeds/__main__.py:46-112`)
      en una transacción por seed, separando `seed_catalog_ingestion`
      (`catalog_ingestion.py:55`, embeds por red) a su propia transacción/lote por
      documento. La idempotencia existente (uuid5, hash de corpus) hace el cambio seguro.
  - ⏳ **Pendiente (2026-07-31):** sin empezar — `seeds/__main__.py:52` sigue
    envolviendo TODOS los seeds en un único `session.begin()`.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_09_a
    runtime: python-pytest
    command: "pytest tests/integration/test_seeds_transaction_per_seed.py -v"
  ```

### Fase C — Índices y búsqueda

#### `task_prod13_10` — Índice FTS de chunks coherente con la query `es_unaccent`

- [x] **Título**: Migración Alembic que reconstruya `ix_chunks_content_fts` con
      `to_tsvector('public.es_unaccent', content)` (réplica de lo que 0079 hizo
      con memory_entries) y unificar `bm25_chunks` (`rag/search.py:103-113`) con
      la misma configuración que usa el preview (`search.py:349-361`), para que
      agente y operador vean los mismos resultados. Downgrade real.
  - ✅ **Ya estaba hecha antes de este plan (verificado 2026-07-31):** la
    migración `0107_chunks_fts_es_unaccent` reconstruyó el índice con
    `public.es_unaccent` y su downgrade restaura el `'simple'` de la 0022;
    `rag/search.py` unifica ambas rutas sobre `_TS_CONFIG`.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_10_a
    runtime: python-pytest
    command: "pytest tests/integration/test_chunks_fts_index_es_unaccent.py -v"
  ```

#### `task_prod13_11` — Índice `(tenant_id, created_at)` en executions + predicado sargable

- [ ] **Título**: Migración con índice compuesto `(tenant_id, created_at)` sobre
      executions y reescritura de `_spend_usd_in_window`
      (`budgets/consumption.py:216-224`) como rango sargable sobre TIMESTAMPTZ
      (sin `func.date()`), definiendo la zona horaria del corte. **Coordinación**:
      prod-06 cablea el sweep de presupuestos (db-1) que depende de este índice.
  - ⏳ **Pendiente (2026-07-31):** el índice `ix_executions_tenant_created_at`
    está (migración 0126, verificado con su orden de columnas en
    `tests/integration/test_perf_indexes_and_uniqueness.py`); falta la mitad del
    código — `budgets/consumption.py:216` sigue con `func.date(...)`, o sea no
    sargable, así que el índice todavía no lo usa nadie.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_11_a
    runtime: python-pytest
    command: "pytest tests/integration/test_budget_window_sargable.py -v"
  ```

#### `task_prod13_12` — Recall vectorial multi-tenant: iterative scan + test de recall

- [ ] **Título**: Mitigar el post-filtrado HNSW (db-6): activar
      `SET hnsw.iterative_scan = relaxed_order` y `hnsw.ef_search` configurable en
      la sesión de `vector_chunks` (`rag/search.py:142-149`), y añadir test de
      recall con dos tenants desbalanceados (corpus 95/5) que falle si el tenant
      pequeño recibe 0 resultados. Redactar ADR propuesto para índices parciales/
      particionado por tenant (decisión futura, no se implementa).
  - ⏳ **Pendiente (2026-07-31):** sin empezar — `rag/search.py` no contiene ni
    `hnsw.iterative_scan` ni `ef_search`, no hay test de recall con tenants
    desbalanceados y el ADR de índices parciales/particionado no está escrito.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_12_a
    runtime: python-pytest
    command: "pytest tests/integration/test_vector_recall_multitenant.py -v"
  ```

#### `task_prod13_13` — Unicidad por tenant en teams/skills/agents + tipos coherentes

- [x] **Título**: Replicar el patrón 0077 (`uq_tools_tenant_name`): índice único
      parcial `WHERE deleted_at IS NULL` sobre `(tenant_id, name)` para teams
      (`domain.py:634-640`), skills y agents, con dedup "latest wins" previo en la
      migración. En la misma pasada: `source_size_bytes` a `BigInteger` y
      `Plan.created_by` a `UUID | None` (db-9, hallazgos fusionados).
  - ℹ️ **Desviación aceptada (2026-07-31):** `agents` NO lleva un
    `(tenant_id, name)` sino DOS índices parciales partidos por `project_id`
    (`uq_agents_tenant_project_name_live` y `uq_agents_tenant_name_global_live`):
    un fork `project_local` conserva a propósito el nombre de su plantilla
    `global_tenant_template` y el índice ingenuo lo habría prohibido. Migración
    0126, con round-trip de downgrade probado.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_13_a
    runtime: python-pytest
    command: "pytest tests/integration/test_tenant_name_uniqueness.py -v"
  ```

### Fase D — Retención y backfill

#### `task_prod13_14` — Job beat de purga de filas soft-deleted

- [ ] **Título**: Nueva task en `workers/maintenance.py` + entrada en
      `beat_schedule.py`: purga física de filas con `deleted_at` anterior a la
      ventana de gracia (platform setting, default 30 días), cascada vía las FKs
      `ON DELETE` existentes (KBs→documents→chunks con embeddings, proyectos→
      plans/tasks/executions), con modo dry-run y log de recuento por tabla.
      **Coordinación**: prod-06 corrige antes el orden blob/commit (db-3) — la
      purga es quien borra los blobs de MinIO de documentos soft-deleted.
  - ⏳ **Pendiente (2026-07-31):** sin empezar — no existe ninguna task de purga
    en `workers/maintenance/` ni entrada en `beat_schedule.py`.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_14_a
    runtime: python-pytest
    command: "pytest tests/integration/test_purge_soft_deleted.py -v"
  ```

#### `task_prod13_15` — Retención de tablas append-only (steps_log, audit_log, guardrail_events)

- [ ] **Título**: Redactar el ADR de retención (decisión clave 3) y, una vez
      decidido por humano, implementar la task beat de retención: compactar/
      archivar `executions.steps_log` (`db/domain.py:1060`) de runs antiguos y
      aplicar la retención decidida a `audit_log`, `guardrail_events` y
      `notifications`. La task entra detrás del flag/setting que el ADR defina.
  - ⏳ **Pendiente (2026-07-31):** bloqueada por decisión de producto — el ADR de
    retención (decisión clave 3) no está escrito y borrar auditoría lo decide el
    operador; no hay task ni entrada de beat.
- **Tiempo**: 1,5 días · **Complejidad**: m
- **Depende de**: `task_prod13_14` (comparte infraestructura de purga)
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_15_a
    runtime: python-pytest
    command: "pytest tests/integration/test_append_only_retention.py -v"
  ```

#### `task_prod13_16` — Embeds por lotes + backfill de chunks sin vector

- [x] **Título**: Trocear el embed de ingesta en lotes (p. ej. 64 chunks/request,
      `ingestion/pipeline.py:123-147`) y añadir task beat `backfill_chunk_embeddings`
      gemela de `backfill_memory_embeddings` (`workers/maintenance.py:249-368`)
      sobre `chunks WHERE embedding IS NULL`, reutilizando el patrón
      `FOR UPDATE SKIP LOCKED` + throttle + platform settings ya existente. Cierra
      el hueco "documento verde en la UI pero invisible para el RAG vectorial".
  - ✅ **Cerrada (2026-07-31):** el backfill YA EXISTÍA (P1-11b) pero sin un solo
    test propio; ahora lo tiene, y muerde: quitarle el `documents.deleted_at IS NULL`
    pone 4 de sus 6 tests en rojo. La mitad que faltaba de verdad —el troceo de la
    ingesta— está hecha con `EMBED_BATCH_SIZE = 64`. Dos decisiones que el plan no
    fijaba y conviene tener escritas: un lote que falla se pierde **solo a sí mismo**
    (antes un `EmbeddingError` dejaba el documento entero sin vector), y un embedder
    que devuelve menos vectores de los pedidos descarta ese lote en vez de emparejar
    por posición — eso último además arregla un `ValueError` que escapaba a Celery
    desde el `zip(strict=True)`, pese a que el pipeline promete no levantar nunca.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_16_a
    runtime: python-pytest
    command: "pytest tests/integration/test_backfill_chunk_embeddings.py -v"
  - id: auto_prod13_16_b
    runtime: python-pytest
    command: "pytest tests/unit/test_ingestion_embed_batching.py -v"
  ```

### Fase E — Endpoints: paginación, caché y concurrencia

#### `task_prod13_17` — Paginar conversaciones, documentos de KB y citas (sin vector)

- [x] **Título**: Aplicar `limit_query()/offset_query()` (`routers/_pagination.py`)
      a `GET /projects/{id}/conversations` (`conversations.py:171`),
      `GET /knowledge-bases/{kb_id}/documents` (`knowledge_bases.py:637`) y
      `GET /documents/{id}/citations` (`knowledge_bases.py:733-746`, paginado por
      `ordinal`). En citations, seleccionar columnas explícitas o declarar
      `Chunk.embedding` como `deferred()` para no arrastrar el vector(768) por
      fila (perf-8).
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_17_a
    runtime: python-pytest
    command: "pytest tests/integration/test_pagination_conversations_docs_citations.py -v"
  ```

#### `task_prod13_18` — No materializar `steps_log` en listados/exports de runs

- [ ] **Título**: En `tenant_stats.py` (`_fetch_runs:557-592`, export `:461`,
      `_last_model_expr`, `_token_split:774-801`): seleccionar solo columnas
      escalares (o `deferred()` en `Execution.steps_log`, `domain.py:1058-1062`)
      y materializar `last_model`/`tokens_in`/`tokens_out` como columnas
      denormalizadas al cerrar el run (patrón ya existente con
      `total_tokens`/`total_cost_usd`), con migración + backfill.
  - ⏳ **Pendiente (2026-07-31):** sin empezar — `routers/tenant_stats.py` sigue
    expandiendo `steps_log` con `jsonb_array_elements` en el listado y el export,
    y no hay columnas denormalizadas de `last_model`/`tokens_in`/`tokens_out`.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_18_a
    runtime: python-pytest
    command: "pytest tests/integration/test_runs_listing_no_steps_log.py -v"
  ```

#### `task_prod13_19` — `/ws/kanban` por canal de proyecto, sin replay global

- [ ] **Título**: Publicar los eventos de tareas también en un stream por proyecto
      (`events:tasks:{project_id}`, dual-write transitorio desde `events.py:28-35`)
      y que `/ws/kanban` (`routers/ws.py:153,211-234`) consuma ese stream
      arrancando en `$` (solo eventos nuevos; el backlog lo da la carga REST
      inicial del tablero), eliminando el filtrado por tenant/proyecto en Python
      y el replay de 10k entradas por socket.
  - ⏳ **Pendiente (2026-07-31):** sin empezar — `events.py` solo publica en el
    stream global `EVENTS_STREAM = "events:tasks"` y `/ws/kanban` lo sigue
    consumiendo con filtrado por tenant/proyecto en Python.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_19_a
    runtime: python-pytest
    command: "pytest tests/integration/test_ws_kanban_per_project_stream.py -v"
  ```

#### `task_prod13_20` — Rate limit en `POST /assistant/chat`

- [x] **Título**: Añadir dependencia de rate limit por `user_id` (y cap por tenant)
      al endpoint (`routers/assistant.py:167`), reutilizando
      `RateLimiter.check_with_headers` (`auth/rate_limit.py:51`) con budget
      configurable en platform_settings. **Coordinación**: prod-07 añade los
      budgets/contabilidad LLM — este límite es la válvula de QPS, no de coste.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_20_a
    runtime: python-pytest
    command: "pytest tests/integration/test_assistant_chat_rate_limit.py -v"
  ```

#### `task_prod13_21` — Caché Redis para membership y platform_settings

- [x] **Título**: Cachear en Redis (TTL ≤ 60 s + invalidación al escribir) el
      lookup de membership por request (`auth/deps.py:308-352`) y
      `get_platform_setting` (`db/platform_settings.py:32`), con invalidación en
      los endpoints de escritura correspondientes. Medir antes/después con la
      métrica de QPS por query (decisión clave 6: la frescura gana).
  - ✅ **Cerrada (2026-07-31):** la de `platform_settings` ya estaba; la de
    membership vive ahora en `api_server/cache/membership.py` (TTL 30 s, la mitad
    del máximo, porque lo que se cachea es un permiso). **La invalidación NO se
    sembró por los endpoints de escritura**, y a propósito: las membresías se
    escriben desde cuatro sitios (panel admin, SCIM, mapeo de grupos SSO, siembra
    de tenant) y el quinto llegará sin que nadie se acuerde de la caché — el patrón
    "mecanismo entregado, cero llamantes" del apartado 5 de
    `verificar-antes-de-implementar.md`. Cuelga de eventos de mapper del ORM y se
    ejecuta DESPUÉS del commit. Precio dicho en voz alta: un UPDATE en SQL crudo
    fuera del ORM no invalida (ninguna vía de aplicación escribe así; el TTL acota
    el resto). Los tres tests que mandan son los de revocación —retirar acceso,
    degradar rol y CONCEDER acceso—: desactivando la invalidación, los tres pasan a
    rojo.
- **Tiempo**: 1 día · **Complejidad**: m
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_21_a
    runtime: python-pytest
    command: "pytest tests/integration/test_redis_cache_membership_settings.py -v"
  ```

#### `task_prod13_22` — `FOR UPDATE` en transiciones de estado críticas

- [ ] **Título**: Añadir `for_update=True` a `get_writable_or_404`
      (`routers/_helpers.py:77`) y usarlo en `approve_plan`/`apply_human_action`
      (`plans.py:471,495`) y `task_lifecycle.py:140`, cerrando la carrera de doble
      firma simultánea (api-10). Test de concurrencia con dos firmas en paralelo.
  - ⏳ **Pendiente (2026-07-31):** el `for_update=True` está implementado y
    verificado (`tests/unit/test_row_lock_and_pagination.py` compila el SELECT y
    exige `FOR UPDATE` + filtro de tenant, y las tres transiciones que firman lo
    piden); falta el test de concurrencia con dos firmas en paralelo, que es lo
    único que demuestra que el lock serializa DENTRO de la transacción del
    request y no se suelta al instante.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_22_a
    runtime: python-pytest
    command: "pytest tests/integration/test_state_transitions_row_lock.py -v"
  ```

#### `task_prod13_23` — No exponer `str(exc.orig)` en respuestas 409

- [x] **Título**: Sustituir el patrón `detail=str(exc.orig)` de los seis routers
      (`conversations.py:159,311`, `plans.py:158`, `projects.py:212`,
      `tasks.py:108`, `kb_categories.py:140`) por un exception handler global de
      `IntegrityError` que mapee constraint→mensaje de dominio estable (el patrón
      correcto ya existe en `api_v1/router.py:137`), y sanear el `{exc}` del
      proveedor LLM en `assistant.py:209-220`.
  - ℹ️ **Desviación aceptada (2026-07-31):** el mapeo constraint→mensaje de
    dominio vive en el helper compartido `routers/_integrity.py`
    (`integrity_conflict`) en vez de en un exception handler global; el efecto es
    el mismo y una guarda recorre los routers para que ninguno vuelva a
    `detail=str(exc.orig)`. El saneado del error del proveedor LLM
    (`_provider_error_detail`) está hecho pero NO tiene test propio.
- **Tiempo**: 0,5 días · **Complejidad**: s
- **Tests automáticos**:
  ```yaml
  - id: auto_prod13_23_a
    runtime: python-pytest
    command: "pytest tests/integration/test_integrity_error_sanitized.py -v"
  ```

## Hallazgos de auditoría cubiertos

| fid     | Severidad | Tarea(s) que lo cierran                        |
| ------- | --------- | ---------------------------------------------- |
| perf-1  | high      | task_prod13_01                                 |
| perf-2  | high      | task_prod13_06, task_prod13_07                 |
| perf-3  | medium    | task_prod13_10                                 |
| perf-4  | medium    | task_prod13_16                                 |
| perf-5  | medium    | task_prod13_19                                 |
| perf-6  | medium    | task_prod13_18                                 |
| perf-7  | medium    | task_prod13_03                                 |
| perf-8  | low       | task_prod13_17                                 |
| perf-9  | low       | task_prod13_05                                 |
| perf-10 | low       | task_prod13_21                                 |
| perf-11 | low       | task_prod13_08                                 |
| db-2    | high      | task_prod13_06, task_prod13_07, task_prod13_08 |
| db-4    | medium    | task_prod13_14, task_prod13_15                 |
| db-6    | medium    | task_prod13_12                                 |
| db-7    | low       | task_prod13_11                                 |
| db-8    | low       | task_prod13_09                                 |
| db-9    | low       | task_prod13_13                                 |
| api-2   | medium    | task_prod13_04                                 |
| api-3   | medium    | task_prod13_02                                 |
| api-4   | medium    | task_prod13_20                                 |
| api-5   | low       | task_prod13_23                                 |
| api-6   | low       | task_prod13_17                                 |
| api-10  | low       | task_prod13_22                                 |

## Riesgos

1. **Regresión funcional en el asistente** (task_prod13_07): sacar la sesión viva
   del turno LLM cambia el contrato de las tools (`tool_ctx.session`). Mitigación:
   suite de integración del asistente completa antes/después + el session-factory
   replica `set_config` de tenant en cada sesión corta (riesgo RLS si se omite).
2. **Reconstrucción del índice GIN/FTS bloquea escrituras** (task_prod13_10): con
   corpus grande la migración puede tardar. Mitigación: `CREATE INDEX CONCURRENTLY`
   fuera de la transacción de Alembic (autocommit) o ventana de mantenimiento
   documentada en el runbook.
3. **La purga borra datos que un tenant quería recuperar** (task_prod13_14):
   pérdida irreversible si la ventana de gracia o la cascada están mal. Mitigación:
   dry-run por defecto la primera semana, log de recuentos, test que verifica que
   filas dentro de la gracia NUNCA se tocan.
4. **Dedup "latest wins" rompe referencias por nombre** (task_prod13_13): seeds o
   plantillas que resuelven por nombre pueden apuntar al duplicado eliminado.
   Mitigación: la migración loguea los renombrados/fusionados y se revisa en staging.
5. **Caché de membership sirve un rol revocado** (task_prod13_21): un admin
   degradado conserva privilegios hasta el TTL. Mitigación: TTL ≤ 60 s +
   invalidación explícita en los writes de membership; test de revocación.
6. **Dual-write de eventos del kanban** (task_prod13_19): consumidores del stream
   global existentes deben seguir funcionando durante la transición. Mitigación:
   mantener el stream global hasta verificar los consumidores y retirar después.

## Tests humanos del Plan

```yaml
- id: human_prod13_01
  description: "El api-server no se congela bajo operaciones pesadas"
  hint: "Dos pestañas: una operando, otra disparando la operación pesada"
  checklist:
    - "Lanzar una actualización de paquete del marketplace → el endpoint devuelve 202 y el kanban en otra pestaña sigue fluido (WS vivo, sin spinner)"
    - "Probar un destino de backup SFTP inalcanzable → la UI espera, pero /healthz y el resto de la API responden < 1 s"
    - "Subir un fichero de 2 GB a una KB → rechazo rápido con 413, sin OOM ni reinicio del contenedor"
    - "Parar Vault y mandar un mensaje al asistente → error acotado en segundos, la API no se cuelga"

- id: human_prod13_02
  description: "Concurrencia de chats sin agotar el pool"
  hint: "Script con 20 chats de asistente en paralelo contra un modelo lento"
  checklist:
    - "Con 20 chats concurrentes, GET /plans y el kanban responden < 2 s"
    - "Sin TimeoutError de pool en los logs del api-server"
    - "La métrica de pool no llega a saturación sostenida"

- id: human_prod13_03
  description: "Retención y backfill operativos"
  hint: "Usar un tenant de pruebas con datos sintéticos"
  checklist:
    - "Soft-borrar una KB, adelantar el reloj/ventana → la purga elimina documents+chunks y libera disco; dentro de la gracia NO se toca nada"
    - "Tirar Ollama durante una ingesta → documento con chunks sin vector; al volver Ollama, el backfill los rellena solo (< 10 min) y el RAG los encuentra"
    - "El ADR de retención de audit_log está decidido y firmado por un humano antes de activar la task de retención"

- id: human_prod13_04
  description: "Endpoints con válvulas"
  checklist:
    - "Bucle de POST /assistant/chat → a partir del límite devuelve 429 con headers X-RateLimit-*"
    - "Doble click simultáneo en 'Aprobar plan' por dos admins → solo una primera firma; la otra recibe 409"
    - "Provocar un nombre duplicado → el 409 muestra un mensaje de dominio, nunca 'duplicate key value violates...'"
    - "Abrir el visor de citas de un PDF grande → respuesta paginada, carga fluida"
```

## Criterios de cierre

1. Todas las tareas con `[x]` y sus tests automáticos en verde.
2. Tabla «Hallazgos de auditoría cubiertos» verificada: los 23 fids tienen su
   tarea cerrada (re-chequeo contra la evidencia original de la auditoría).
3. Los 4 tests humanos pass, validados por un humano.
4. ADR de retención append-only (decisión clave 3) y ADR de particionado HNSW
   (decisión clave 5) redactados; el primero decidido antes de activar la task
   de retención.
5. Migraciones nuevas con downgrade real y roundtrip head→base→head verde.
6. Entrada de changelog en `docs/07-changelog/prod-13-rendimiento-y-datos.md` y
   runbooks afectados (`docs/06-runbooks/`) actualizados (purga, retención, pool).
7. PR del plan mergeado a `master`.

## Próximo Plan

Con los P1 de rendimiento y datos cerrados, la serie continúa con los P2:

- **prod-14-tenancy-defensa-profundidad** [P2] — multi-tenancy: defensa en
  profundidad (junctions, service_user, meta-test). Se beneficia directamente de
  este plan: el test de recall multi-tenant (task_prod13_12) y la caché de
  membership con invalidación (task_prod13_21) son insumos de su meta-test.
- Después: **prod-15-gobernanza-roadmap-docs** y **prod-16-frontend-i18n-calidad**.
