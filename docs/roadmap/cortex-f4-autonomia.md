---
title: "Córtex F4 — Curiosidad y pensamiento de fondo (bucles cognitivos autónomos)"
status: pending_human_validation
blocking_plan:
  - "docs/roadmap/cortex-system-owner.md (F1, F2, F3) — IMPLEMENTADAS"
  - "ADR 0078 (bucles cognitivos de fondo) — proposed → requiere accepted-f4"
  - "ADR 0076 (razonamiento profundo + egress confiable vía WebSearch del SDK)"
  - "ADR 0075 (drives homeostáticos)"
  - "ADR 0021 (catálogo LLM cerrado), ADR 0070 (reasoning_effort)"
started_at: 2026-06-24
completed_at: null
phase: F4
related_adrs: ["0078", "0076", "0075", "0074", "0021", "0070", "0059"]
docs_language: es
---

# Córtex F4 — Curiosidad y pensamiento de fondo

> **Auditoría 2026-07-27 — las casillas de este plan se verificaron una a una
> contra el código.** Las marcadas `[x]` lo están con evidencia `file:line` y una
> segunda pasada adversarial; las que siguen sin marcar tienen su hueco concreto
> descrito en
> [`gaps-cortex-2026-07-27.md`](gaps-cortex-2026-07-27.md) (informe:
> [`auditoria-cortex-2026-07-27.md`](auditoria-cortex-2026-07-27.md)).
> Antes de implementar una casilla sin marcar, **abre el fichero**: la pasada
> adversarial dio al menos un falso positivo comprobado.

> **✅ IMPLEMENTADO Y DESPLEGADO** (verificado 2026-07-06 — auditoría de estado del roadmap). El
> banner GATED quedó congelado desde el diseño (commit `cf8f7cd`); el código real:
> `cortex/autonomy.py`, `curiosity.py`, migraciones `0095_cortex_curiosity_pursuits` +
> `0103_cortex_pursuit_surfaced`, worker `cortex_curiosity.py` (entrada `sched["cortex-curiosity"]`
> en `beat_schedule.py`), endpoints `/curiosity/pursuits` + `GET/PUT /autonomy` (kill-switch), con
> `test_cortex_autonomy*`/`test_cortex_curiosity_loop.py`/`test_cortex_topic_selection.py` en verde.
> **El kill-switch `cortex.autonomy_enabled` sigue en OFF por defecto** (decisión del operador,
> nadie lo ha encendido en dev). Ver [cortex-identidad-real.md](cortex-identidad-real.md) para el
> cierre del "surfacing" (§2 de este plan decía "abre el tema en el próximo encuentro" — quedó sin
> cablear hasta el 2026-07-06). Checkboxes de tareas NO re-verificados línea a línea.

## Objetivo

Cuando el drive `curiosity` baja, el córtex elige un tema entre las **entities que el owner ha mencionado**, lo investiga con WebSearch (egress confiable del SDK), lo destila a una **memoria de aprendizaje** (`semantic/learning`), sacia el drive afectivo y **abre el tema en el próximo encuentro** — todo bajo budget caps en Redis, circuit-breaker, kill-switch global y un owner-approval gate opcional para las primeras búsquedas.

## Arquitectura

Un **Celery beat NUEVO** (`workers.cortex_curiosity_loop`) que extiende el patrón exacto de `workers/maintenance.py` + `workers/beat_schedule.py`: cadencia operator-tunable (`Settings.cortex_curiosity_cron`), enable/disable + kill-switch leídos _en vivo_ al inicio de cada pasada como platform settings. El bucle corre con rol **BYPASSRLS** (igual que el back-fill de embeddings y el Memorizer: `create_async_engine(settings.database_url)` + `async_sessionmaker`, sin `set_config('app.tenant_id')`) y **filtra `owner_user_id` explícito en todo SQL** porque las tablas `cortex_*` son tenant-less (excepción consciente al Principio 1, ADR 0074). La acción autónoma (WebSearch + razonamiento) sale **exclusivamente de `claude_sdk run_agent`** con `effort` modulado y `allowed_tools=["WebSearch","WebFetch"]` (egress directo del api-server/worker confiable, anti-SSRF gestionado por Anthropic, ADR 0076) — **degradación limpia a no-op si no hay SDK** (ADR 0064), nunca un 5º proveedor. Toda la lógica de selección de tema, gating de presupuesto y circuit-breaker es **determinista, pura y testeable** fuera del LLM; la idempotencia se garantiza por `metadata_` de la memoria (`cortex_pursuit_id`) y por una nueva tabla de auditoría `cortex_curiosity_pursuits`.

## Tablas nuevas

### `cortex_curiosity_pursuits` (migración 0092, tenant-less / singleton del owner, BYPASSRLS)

Auditoría + idempotencia + cola de "temas pendientes de abrir". El olvido/decay de memoria es de F5; aquí solo añadimos esta tabla.

| Columna                     | Tipo                               | Notas                                                                                                                                           |
| --------------------------- | ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`                        | `UUID` PK                          | `gen_random_uuid()`                                                                                                                             |
| `owner_user_id`             | `UUID NOT NULL`                    | FK lógica a `users.id`; **filtro de aislamiento en TODO SQL** (no FK física dura para no acoplar al ciclo de vida del user; índice obligatorio) |
| `topic`                     | `Text NOT NULL`                    | la entity/tema elegido (normalizado igual que `query_entity_terms`)                                                                             |
| `source_entities`           | `JSONB NOT NULL default '[]'`      | entities del owner que motivaron el tema (trazabilidad)                                                                                         |
| `status`                    | `Text NOT NULL`                    | CHECK `IN ('selected','searching','digested','surfaced','skipped','failed')`                                                                    |
| `drive_snapshot`            | `JSONB`                            | valor de `curiosity` (y demás drives) al disparar — auditoría del "por qué ahora"                                                               |
| `learning_memory_id`        | `UUID`                             | FK lógica a `memory_entries.id`; la memoria `semantic/learning` generada                                                                        |
| `cost_usd`                  | `Numeric(12,6) NOT NULL default 0` | coste real de la pasada (de `Usage.cost_usd` del SDK)                                                                                           |
| `search_count`              | `Integer NOT NULL default 0`       | nº de WebSearch consumidas                                                                                                                      |
| `approved`                  | `Boolean`                          | NULL = no requiere gate; true/false = decisión del owner-approval gate                                                                          |
| `metadata_`                 | `JSONB NOT NULL default '{}'`      | extensible (razón de skip, circuit-breaker trip, etc.)                                                                                          |
| `created_at` / `updated_at` | `TimestamptzMixin`                 |                                                                                                                                                 |
| `surfaced_at`               | `Timestamptz`                      | cuándo se abrió el tema en un turno (NULL hasta entonces)                                                                                       |

**Índices:**

- `ix_cortex_pursuits_owner_status` sobre `(owner_user_id, status)` — la cola "pendiente de abrir" (`status='digested' AND surfaced_at IS NULL`) y el conteo diario.
- `ix_cortex_pursuits_owner_topic_created` sobre `(owner_user_id, topic, created_at DESC)` — dedup por tema reciente (no re-investigar lo mismo en N días).
- Migración **reversible**: `downgrade()` hace `drop_index` ×2 + `drop_table`.

**Redis (reutiliza el namespace de F2, NO nueva infra):**

- `cortex:budget:{owner_user_id}` (hash) — campos `curiosity_cost_usd_today`, `curiosity_searches_today`, con TTL hasta medianoche UTC (ventana diaria). El gate lee+incrementa atómicamente (`HINCRBYFLOAT`/`HINCRBY`).
- `cortex:curiosity:cb:{owner_user_id}` (string + TTL) — estado del circuit-breaker (`open` durante `cb_cooldown_s` tras N fallos consecutivos).

## Endpoints / WS

Todos **gated `require_system_owner`** (DB-authoritative, F0). Routers nuevos en `apps/api-server/src/api_server/routers/cortex_curiosity.py`, montados bajo el prefijo del córtex `/owner/cortex`:

- `GET /owner/cortex/curiosity/pursuits?status=&limit=` — historial de persecuciones (para el panel "lo que está aprendiendo"). Filtra `owner_user_id == principal.user_id` en SQL.
- `GET /owner/cortex/curiosity/budget` — snapshot de budget consumido hoy vs caps (lee Redis + platform settings). Copy honesto.
- `POST /owner/cortex/curiosity/pursuits/{id}/approve` (GATED gate) — aprueba/rechaza una persecución pendiente (`approved` true/false) cuando el owner-approval gate está activo; mueve `selected→searching` o `selected→skipped`.
- `POST /owner/cortex/curiosity/kill-switch` — flip del kill-switch global de bucles autónomos (platform setting `cortex.autonomy_enabled`).

> **Sin WS nuevo en F4.** La telemetría afectiva (`/ws/owner/cortex/telemetry`, F2) ya emite los drives; la UI de F4 hace polling de `/curiosity/pursuits` (cadencia de fondo, no tiempo real).

---

## FASES → TAREAS

> Convención TDD estricta (CLAUDE.md): por cada tarea **escribe el test → corre → falla → implementa → corre → pasa → commit**. Migraciones reversibles con `down()`. Tests cross-owner **obligatorios** en todo acceso a `cortex_*`. Catálogo LLM cerrado. Copy ES+EN honesto sobre simulación afectiva.

### Sub-fase 4.0 — Gates de gobierno (PRIMERO: sin esto no hay bucle)

- [x] **Platform settings de autonomía + budget + circuit-breaker**
  - Modificar: `apps/api-server/src/api_server/db/platform_settings.py` — añadir claves y helpers (patrón exacto de `get_rag_reranker_enabled` / `get_memory_backfill_enabled`):
    - `CORTEX_AUTONOMY_ENABLED_KEY = "cortex.autonomy_enabled"` (default `False` → **kill-switch global apagado por defecto**), `get_cortex_autonomy_enabled(session) -> bool`.
    - `CORTEX_CURIOSITY_ENABLED_KEY = "cortex.curiosity_enabled"` (default `False`), `get_cortex_curiosity_enabled(session) -> bool`.
    - `CORTEX_CURIOSITY_APPROVAL_GATE_KEY = "cortex.curiosity_approval_gate"` (default `True` → **las primeras búsquedas requieren aprobación**), `get_cortex_curiosity_approval_gate(session) -> bool`.
    - `CORTEX_CURIOSITY_DAILY_USD_CAP_KEY = "cortex.curiosity_daily_usd_cap"` (default `0.50`), `CORTEX_CURIOSITY_DAILY_SEARCHES_CAP_KEY` (default `5`), `CORTEX_CURIOSITY_DRIVE_THRESHOLD_KEY` (default `0.35`: solo se dispara si `curiosity < threshold`), `CORTEX_CURIOSITY_CB_FAILS_KEY` (default `3`), getters tipados (`int`/`float`).
  - TDD: en `apps/api-server/tests/unit/test_platform_settings.py` (o `test_cortex_curiosity_settings.py` nuevo) — test: defaults cuando no escrito; `set_platform_setting` rechaza no-System-Admin (`PlatformSettingForbiddenError`); valores tipados correctos.
  - **Aceptación:** los 6 getters devuelven los defaults seguros (autonomía OFF, gate ON) sin filas en `platform_settings`; un Tenant-Admin no puede escribirlos.

- [x] **Config de cadencia del beat (worker)**
  - Modificar: `apps/workers/src/workers/config.py` — añadir `cortex_curiosity_cron: str = Field(default="*/30 * * * *", ...)` y `cortex_curiosity_cb_cooldown_s: int = Field(default=3600, ...)` (mismo estilo que `human_escalation_cron`).
  - TDD: `apps/workers/tests/test_config.py` — test: el default parsea como cron válido vía `_parse_cron`; override por env (`WORKERS_CORTEX_CURIOSITY_CRON`) se respeta.
  - **Aceptación:** `get_settings().cortex_curiosity_cron == "*/30 * * * *"` y `_parse_cron(...)` no cae al fallback.

- [x] **Budget gate determinista en Redis (puro, testeable)**
  - Crear: `apps/api-server/src/api_server/cortex/curiosity/budget.py` — funciones puras + acceso Redis:
    - `daily_budget_key(owner_user_id) -> str` → `cortex:budget:{owner}`.
    - `async def check_and_reserve(redis, *, owner_user_id, usd_cap, searches_cap) -> BudgetDecision` — lee `curiosity_cost_usd_today`/`curiosity_searches_today`; devuelve `allowed: bool` + `reason`; setea TTL hasta medianoche UTC si la clave es nueva. **No** incrementa coste real aquí (eso se hace tras la búsqueda con `record_spend`).
    - `async def record_spend(redis, *, owner_user_id, cost_usd, searches) -> None` — `HINCRBYFLOAT`/`HINCRBY`.
    - `seconds_until_utc_midnight(now) -> int` (pura).
  - TDD: `apps/api-server/tests/unit/test_cortex_curiosity_budget.py` (fakeredis) — test: bajo cap → `allowed`; al alcanzar el cap de USD o de búsquedas → `not allowed` con reason; TTL fijado; `record_spend` acumula; `seconds_until_utc_midnight` correcto en bordes.
  - **Aceptación:** un cap de 5 búsquedas bloquea la 6ª en la misma ventana; la ventana se resetea a medianoche UTC.

- [x] **Circuit-breaker determinista**
  - Crear: `apps/api-server/src/api_server/cortex/curiosity/circuit_breaker.py` — `async def is_open(redis, owner_user_id) -> bool`, `async def record_failure(redis, *, owner_user_id, threshold, cooldown_s) -> bool` (abre el breaker al N-ésimo fallo consecutivo; devuelve si quedó abierto), `async def record_success(redis, owner_user_id) -> None` (resetea el contador).
  - TDD: `apps/api-server/tests/unit/test_cortex_curiosity_circuit_breaker.py` (fakeredis) — test: N fallos consecutivos abren el breaker con TTL=cooldown; un éxito resetea; mientras `open` el bucle no debe correr.
  - **Aceptación:** tras 3 fallos consecutivos `is_open()==True` durante `cooldown_s`; un éxito intermedio reinicia el contador.

### Sub-fase 4.1 — Selección de tema + persistencia (lógica pura)

- [x] **Migración 0092 `cortex_curiosity_pursuits`**
  - Crear: `apps/api-server/migrations/versions/20260623_0092_cortex_curiosity_pursuits.py` — `revision="0092_cortex_curiosity_pursuits"`, `down_revision="0091_system_owner_f0"`. `upgrade()` crea la tabla + los 2 índices + el CHECK de `status`; `downgrade()` los retira (reversible). Server-defaults para `status`/`source_entities`/`metadata_`/`cost_usd`/`search_count`. **Sin RLS** (tenant-less, BYPASSRLS) — comentario explícito citando ADR 0074.
  - Modelo ORM: crear `apps/api-server/src/api_server/db/cortex.py` (o extender el de F1) con la clase `CortexCuriosityPursuit` (TimestamptzMixin, **sin** TenantScopedMixin).
  - TDD: `apps/api-server/tests/integration/test_migration_0092_cortex_pursuits.py` — test: `alembic upgrade head` crea la tabla con los índices y el CHECK; `downgrade -1` la elimina; insertar un `status` fuera del CHECK falla.
  - **Aceptación:** upgrade/downgrade limpios sobre una DB de test; el CHECK rechaza `status='bogus'`.

- [x] **Selector de tema determinista (puro)**
  - ✅ **Cerrada el 2026-08-19.** La mitad `cortex_turns` que faltaba ya está: los turnos recientes del owner entran en el ranking (`cortex/curiosity.py:36-146`, constante `_TURN_SCAN_LIMIT` en la línea 33) con su **test cross-owner** en verde. Rojo verificado antes de dar nada por bueno: quitando los filtros `owner_user_id`/`role` de la consulta de turnos caen `test_los_turnos_de_otro_owner_no_refuerzan_mi_ranking` y `test_los_turnos_del_propio_cortex_no_se_votan_a_si_mismos`; contando todos los tokens del turno cae `test_una_palabra_cualquiera_del_turno_no_se_convierte_en_tema`.
  - **Enunciado corregido — los turnos VOTAN, no proponen.** La lectura literal («extrae las entities del texto del turno con `query_entity_terms`») no se implementó, y no por pereza: ese helper es un **matcher de recall**, no un ranker — devuelve todo token de ≥3 caracteres fuera de 26 stopwords. Medido con la implementación literal puesta a propósito, el ranking de un owner real salía `despliegue 3, manana 3, necesito 3`: el bucle autónomo habría sacado a Internet, con dinero real, la palabra «necesito». Lo implementado: la **memoria destilada fija el vocabulario** (solo puede ser tema lo que alguna vez se destiló como entity) y **cada turno `role='user'` suma un voto** a las entities conocidas que menciona. Así el helper hace justo aquello para lo que se escribió —emparejar texto con entities guardadas— y un tema del que se habla AHORA adelanta a otro destilado hace meses, que era el objetivo. Se descartó `role='cortex'` para no cerrar un bucle de autorrefuerzo (el córtex saca un tema → sus turnos lo mencionan → lo vuelve a investigar). Limitación consciente y anotada en el docstring: un tema que nunca llegó a la memoria destilada no es candidato; levantarlo pide un extractor de entidades para turnos, que es trabajo aparte.
  - **Enunciado corregido — dónde vive.** No hay paquete `cortex/curiosity/`: `gather_owner_entities`, `pick_topic` y `persist_learning_memory` viven en el módulo `apps/api-server/src/api_server/cortex/curiosity.py`. La firma real es `gather_owner_entities(session, *, owner_user_id, limit=50, turn_scan_limit=100)` — la ventana se acota por NÚMERO de filas (memorias y turnos), no por fecha, para que el coste de la pasada sea predecible aunque el owner pase un mes sin hablar.
  - Ubicación real (el enunciado original decía `cortex/curiosity/topic_selection.py`): `apps/api-server/src/api_server/cortex/curiosity.py`:
    - `async def gather_owner_entities(session, *, owner_user_id, limit, turn_scan_limit) -> list[tuple[str,int]]` — SQL **BYPASSRLS con filtro `owner_user_id` explícito** en las DOS consultas (y, desde la migración `0140`, con la RLS de eje owner detrás): agrega `entities` de `memory_entries` (scope='private', user_id=owner, `metadata->>'cortex'='true'`, `deleted_at IS NULL`) y el voto de los `cortex_turns` recientes del owner (F1), ordenadas por frecuencia.
    - `def pick_topic(entity_freqs, *, recently_pursued: set[str], identity_learning_goals: list[str]) -> str | None` — **pura**: elige la entity más frecuente NO investigada recientemente; sesga hacia `learning_goals` de la identidad (F3) si solapan; `None` si no hay candidato.
  - TDD (rutas reales): `tests/unit/test_cortex_topic_selection.py` — `pick_topic` favorece frecuencia, **excluye** temas en `recently_pursued`, prioriza solape con `learning_goals`, devuelve `None` con set vacío. `tests/integration/test_cortex_curiosity_entities.py` (12 tests) para `gather_owner_entities`, con **test cross-owner** por partida doble: entities de OTRO owner NO aparecen (`:115`) y turnos de OTRO owner no votan (`:480`).
  - **Aceptación:** el selector nunca repite un tema investigado en la ventana de dedup; **test cross-owner en verde** (aislamiento por `owner_user_id`).

### Sub-fase 4.2 — Investigación autónoma (deep reasoning + WebSearch, GATED egress)

- [x] **Investigador con `claude_sdk` (egress confiable, degradación limpia)** — **GATED por ADR 0076**
  - Crear: `apps/api-server/src/api_server/cortex/curiosity/researcher.py`:
    - `async def research_topic(provider, *, topic, model, effort) -> ResearchResult` — usa `provider.run_agent(prompt, model=model, system_prompt=<digest prompt>, allowed_tools=["WebSearch","WebFetch"], effort=effort)` (ADR 0076: WebSearch/WebFetch **nativas del SDK** vía `allowed_tools`, anti-SSRF gratis; el `effort` SÍ llega a `_build_options`, fix de F0 ya aplicado). Acumula `text` events → digest; cuenta `tool_use` de WebSearch; suma `Usage.cost_usd` del `result` event.
    - **Degradación:** si el provider efectivo NO es `claude_sdk` (sin `run_agent` / sin `WITH_CLAUDE`, ADR 0064) → devolver `ResearchResult(skipped=True, reason="no_sdk")`. **MVP NO usa tool web propia** (Decisión #5 del plan maestro: camino degradado solo tras ADR con anti-SSRF obligatorio).
  - Resolución de modelo: reusar la clave `cortex.default_model` (F1, clon de `resolve_assistant_model`); `effort` alto por defecto (ADR 0070).
  - TDD: `apps/api-server/tests/unit/test_cortex_researcher.py` — con un **doble de provider** (mismo patrón que `ScriptedAssistantModel`): emite text+tool_use+result → `research_topic` produce digest, cuenta búsquedas, suma coste. Provider sin `run_agent` → `skipped, reason="no_sdk"` (sin egress, sin excepción).
  - **Aceptación:** con el doble, `ResearchResult.digest` no vacío, `search_count>=1`, `cost_usd>0`; sin SDK, `skipped=True` y **cero llamadas de red**.

- [x] **Escritura de la memoria de aprendizaje (directa, idempotente)**
  - Crear: `apps/api-server/src/api_server/cortex/curiosity/digest_memory.py` — `async def persist_learning(session, *, owner_user_id, topic, digest, pursuit_id, entities) -> MemoryEntry`:
    - Usa `persist_memory_candidates` **directo** (NO `workers/memorizer.py`, que enruta episodic→project_shared y rompería el scope private — ver plan maestro): `scope="private"`, `user_id=owner_user_id`, `type="semantic"`, `extra_metadata={"cortex": True, "kind": "learning", "cortex_pursuit_id": str(pursuit_id), "source": "cortex_curiosity"}`, `tags=("cortex","learning")`, `entities=...`.
    - **Idempotencia:** antes de escribir, comprobar que no exista ya una memoria con `metadata_->>'cortex_pursuit_id' == pursuit_id` (dedup por `metadata_`, ADR 0078) → si existe, no-op.
  - TDD: `apps/api-server/tests/integration/test_cortex_digest_memory.py` — test: persiste una `semantic/learning` con el `kind` y `cortex_pursuit_id` correctos; **re-ejecutar con el mismo `pursuit_id` es no-op** (idempotente); **test cross-owner**: la memoria nace con `user_id=owner` y NO es visible para otro user bajo RLS.
  - **Aceptación:** memoria `kind='learning'` recuperable por el recall del owner; doble ejecución no duplica filas; aislamiento verificado.

### Sub-fase 4.3 — El bucle de fondo (Celery beat) + satisfacción del drive

- [x] **Tarea Celery `workers.cortex_curiosity_loop`** — **GATED por ADR 0078**
  - Crear: `apps/workers/src/workers/cortex_curiosity.py` (patrón EXACTO de `workers/maintenance.py`): `@app.task(name="workers.cortex_curiosity_loop")` síncrona que hace `asyncio.run(_run_curiosity_loop(settings))`; el core async **posee el `engine` lifecycle** (`create_async_engine` + `dispose()`), corre **BYPASSRLS** (sin `set_config app.tenant_id`), captura sus propias excepciones (best-effort, nunca tumba beat).
  - Orquestación del core async (todo con imports perezosos de `api_server.cortex.*`, como `maintenance.py` importa `api_server.ingestion.embeddings`):
    1. **Kill-switch + enable**: si `not get_cortex_autonomy_enabled()` o `not get_cortex_curiosity_enabled()` → return `{"skipped":"disabled"}`.
    2. Resolver el owner (singleton): `SELECT id FROM users WHERE is_system_owner` (si no hay owner → no-op).
    3. **Circuit-breaker**: si `is_open()` → return `{"skipped":"circuit_open"}`.
    4. **Drive gate**: leer `curiosity` de `cortex:affect:{owner}` (F2); si `>= drive_threshold` → no-op (no hay hambre de curiosidad).
    5. **Budget gate**: `check_and_reserve(...)`; si no `allowed` → registrar pursuit `status='skipped'` con reason y return.
    6. **Selección de tema** (4.1); si `None` → no-op. Insertar fila `cortex_curiosity_pursuits` `status='selected'`.
    7. **Approval gate** (GATED): si `get_cortex_curiosity_approval_gate()` y `approved IS NULL` → dejar en `status='selected'` (espera al endpoint `/approve`) y return. Si gate OFF o ya aprobado → seguir.
    8. **Investigar** (4.2) → `status='searching'`; `record_spend(...)`; en fallo `record_failure(...)` y `status='failed'`.
    9. **Destilar memoria** (4.2) → `status='digested'`, set `learning_memory_id`.
    10. **Saciar el drive** (F2): aplicar el delta determinista que sube `curiosity` hacia baseline en `cortex:affect:{owner}` (motor PAD de F2, función `satisfy_drive`); `record_success(...)`.
  - Registrar el módulo en `imports=(...)` de `apps/workers/src/workers/celery_app.py`.
  - TDD: `apps/workers/tests/test_cortex_curiosity_loop.py` — con fakeredis + DB de test + provider-doble: (a) kill-switch OFF → no-op; (b) `curiosity` alto → no-op; (c) budget agotado → pursuit `skipped`; (d) approval gate ON → queda `selected`, sin búsqueda; (e) camino feliz (gate OFF) → `digested`, memoria escrita, drive saciado, `record_spend` llamado; (f) provider sin SDK → `skipped no_sdk`, sin egress; (g) **una excepción interna NO propaga** (best-effort). **Test cross-owner**: con dos users `is_system_owner` simulados, el bucle solo toca al owner real (en la práctica el singleton lo garantiza, pero el SQL filtra `owner_user_id`).
  - **Aceptación:** cada rama del gate observable en el dict de retorno; el camino feliz deja una fila `digested` + una `memory_entries` `learning` + el drive saciado en Redis; un fallo del LLM incrementa el circuit-breaker sin tumbar beat.

- [x] **Wire del beat schedule (cadencia + enable en vivo)**
  - Modificar: `apps/workers/src/workers/beat_schedule.py` — añadir `CORTEX_CURIOSITY_BEAT_ENTRY = "cortex-curiosity-loop"` y en `build_beat_schedule` insertar la entrada `{"task":"workers.cortex_curiosity_loop","schedule":_parse_cron(cfg.cortex_curiosity_cron),"options":{"queue":"default"}}`. (El enable/disable real es el platform setting leído _dentro_ de la tarea — la entrada del beat siempre existe, como el price-sync.)
  - TDD: `apps/workers/tests/test_beat_schedule.py` — test: la entrada existe con el nombre constante, apunta a `workers.cortex_curiosity_loop`, queue `default`, y su `schedule` deriva de `cortex_curiosity_cron`.
  - **Aceptación:** `build_beat_schedule()` contiene la entrada `cortex-curiosity-loop` con la cadencia configurada.

### Sub-fase 4.4 — "Inicia el tema en el próximo encuentro"

- [x] **Inyección del tema pendiente en el system_prompt del turno** — **depende de F1 (grafo del córtex) + F2 (augment del mood)**
  - ✅ **Cerrada el 2026-08-19.** La mitad «copy ES+EN» de la aceptación ya está protegida: `tests/unit/test_cortex_self_context.py:153` y `:166` fijan que el label del learning sale en el idioma de la identidad **y solo en ese** (`_LEARNING_LABEL` en `cortex/self_context.py:256-259`). Los tests que había asertaban el tema y el digest, que son DATOS del pursuit, así que pasaban en verde aunque el label desapareciera. Rojo verificado con dos mutaciones: cableando `_LEARNING_LABEL["es"]` en vez de `[language]` cae el test EN, y borrando el label del f-string caen los dos.
  - **Enunciado corregido — dónde vive.** No hay `cortex/curiosity/surfacing.py`: el surfacing se integró dentro del self-model unificado de F1/F2/F3, en `apps/api-server/src/api_server/cortex/self_context.py` (`_load_pending_learnings` lee el pursuit `digested` sin `surfaced_at` con filtro `owner_user_id`; `_extra_fact_lines` inyecta la línea dentro de `<<<DATOS>>>`; `mark_pursuits_surfaced` la marca una sola vez). Enunciado original, para el historial:
  - ~~Crear: `apps/api-server/src/api_server/cortex/curiosity/surfacing.py`~~ — `async def pending_topic_to_open(session, *, owner_user_id) -> CortexCuriosityPursuit | None` (la más reciente `status='digested' AND surfaced_at IS NULL`, filtro `owner_user_id`); `def augment_prompt_with_curiosity(base_prompt, pursuit, learning_digest, *, language) -> str` (**pura**, estilo `augment_system_prompt` de `assistant/memory.py`): añade una sección "Algo que aprendí desde la última vez (compártelo con naturalidad si encaja)" — copy ES+EN.
  - Modificar (F1): el constructor del system_prompt del turno del córtex (en el grafo/cerebro de F1) para llamar a este augment; tras usarse, marcar `surfaced_at=now()` y `status='surfaced'` (una sola vez, idempotente por `surfaced_at IS NULL`).
  - TDD: `apps/api-server/tests/unit/test_cortex_surfacing.py` — `augment_prompt_with_curiosity` inserta la sección en ES y EN; sin pursuit pendiente devuelve el prompt intacto. Integración: tras un turno, el pursuit pasa a `surfaced` y un segundo turno NO lo re-abre.
  - **Aceptación:** el córtex menciona el tema aprendido **una vez** en el siguiente encuentro y no lo repite; copy honesto y bilingüe.

### Sub-fase 4.5 — Endpoints + UI "lo que está aprendiendo"

- [x] **Router `cortex_curiosity` (gated `require_system_owner`)**
  - ✅ **Cerrada el 2026-08-19.** El hueco que quedaba —el **budget** solo exponía búsquedas— está cerrado: `GET /owner/cortex/autonomy` devuelve ya las DOS dimensiones (`cost_usd_today` / `cost_usd_cap` en `schemas/cortex_autonomy.py:45-46`, leídas en `routers/cortex_mind.py:785-817` con el MISMO lector que usa el gate, `read_budget_usage`, para que panel y gate no diverjan). Tests: `tests/integration/test_cortex_autonomy_endpoint.py:149` (el gasto lo escribe el productor real, `record_spend`, no un `SET` a mano) y `:204` (cross-owner: el gasto de otro owner no se ve). Rojo verificado: descartando el segundo valor de `read_budget_usage` cae el primero; quitando el `owner_user_id` de `daily_budget_key` cae el segundo.
  - **Enunciado corregido — dónde vive.** No existe `routers/cortex_curiosity.py`: los cuatro endpoints se plegaron en el router del Panel de Mente, `apps/api-server/src/api_server/routers/cortex_mind.py` (`GET /curiosity/pursuits`, `POST /curiosity/pursuits/{id}/approve`, `GET /autonomy` —que ES el budget— y `PUT /autonomy` —que ES el kill-switch—), con schemas en `schemas/cortex_curiosity.py` y `schemas/cortex_autonomy.py`. Enunciado original, para el historial:
  - ~~Crear: `apps/api-server/src/api_server/routers/cortex_curiosity.py` + schemas en `apps/api-server/src/api_server/schemas/cortex_curiosity.py`.~~ Los 4 endpoints de la sección "Endpoints/WS". Acceso a `cortex_*` vía `get_admin_session`/admin sessionmaker con **filtro `owner_user_id == principal.user_id` explícito** en todo SQL (defensa en profundidad sobre BYPASSRLS). El `/kill-switch` y `/approve` escriben platform setting / fila con el owner como actor.
  - Montar el router en `apps/api-server/src/api_server/main.py` (o en el agregador de routers del córtex de F1).
  - TDD: `apps/api-server/tests/integration/test_cortex_curiosity_endpoints.py` — test: owner ve sus pursuits y su budget (200); **un user NO-owner → 403** (gate DB-authoritative); **un owner NO ve pursuits de otro owner_user_id** (cross-owner, aislamiento); `/approve` mueve el estado; `/kill-switch` flipa el platform setting y la siguiente pasada del bucle queda `disabled`.
  - **Aceptación:** 403 para no-owner; aislamiento cross-owner en verde; el kill-switch detiene el bucle en la siguiente pasada.

- [x] **UI "Lo que está aprendiendo" (Panel de Mente, F2/F3)**
  - ✅ **Cerrada el 2026-08-19.** Las dos piezas que quedaban sin test —la **tarjeta de budget** (`cortex-budget-usage`) y el **toggle del kill-switch** (`cortex-autonomy-toggle`)— ya lo tienen, y el del toggle comprueba el `PUT` al endpoint, no que se pinta el botón. Rojo verificado borrando cada pieza antes de restaurarla (detalle abajo).
  - **Enunciado corregido — dónde vive el panel de verdad.** El plan pedía crear `apps/admin-panel/app/admin/cortex/_components/curiosity-panel.tsx`; esa carpeta **nunca existió**. La funcionalidad se repartió en dos componentes del Panel de Mente, y ahí es donde está:
    - `apps/admin-panel/app/admin/cortex/mind/autonomy-panel.tsx` — kill-switch, gates de web/navegador, **tarjeta de budget** y el aviso honesto;
    - `apps/admin-panel/components/cortex/learning-panel.tsx` — lista de pursuits (tema, estado, fecha) y el gate Aprobar/Rechazar.
  - Helper puro: `apps/admin-panel/lib/cortex-curiosity.ts` (`budgetUsageLabel`, `budgetUsageRatio`, `pursuitStatusLabel`, `pursuitAwaitsApproval`, `honestNote`, `autonomyHonestNote`), con `lib/cortex-curiosity.test.ts` — 19 vitest.
  - Tests de pantalla: `apps/admin-panel/app/admin/cortex/mind/autonomy-panel.test.tsx` (NUEVO, 10 casos) y `app/admin/cortex/mind/page.test.tsx` (la tarjeta «Lo que está aprendiendo»).
  - **Un hueco de copy honesto que se cerró de paso**: `honestNote` devuelve `""` cuando el backend no manda ninguna de las dos notas, y el panel pintaba un `<p>` en blanco — o sea, el kill-switch y el gasto del día **sin** el aviso que el ADR 0075 §6 declara no removible. Se añadió `autonomyHonestNote`, con respaldo ES+EN por el diccionario (`cortexCuriosity.autonomyHonestyFallback`), no como cadena fija en el JSX.
  - **Aceptación (verificada):** el panel lista las persecuciones y refleja el budget con los dos números del endpoint; el toggle hace `PUT /owner/cortex/autonomy` con `{autonomy_enabled}` **y sin tocar los otros dos gates** (los tres botones pegan contra la misma ruta: un cable cruzado apagaría la web creyendo apagar la autonomía); copy honesto presente en ES y EN, también cuando el backend no lo manda.

### Sub-fase 4.6 — Observabilidad (OTEL) + cierre

- [x] **Métricas OTEL del bucle (ADR 0078)**
  - Crear/Modificar: emitir métricas de coste/latencia/resultado por pasada del bucle. Patrón del repo: el textfile-collector de `apps/workers/src/workers/backup_metrics.py` (gauges `*.prom` atómicas) o el meter de `apps/api-server/src/api_server/telemetry/setup.py`. Métricas: `agentic_cortex_curiosity_runs_total{outcome}`, `agentic_cortex_curiosity_cost_usd_total`, `agentic_cortex_curiosity_searches_total`, `agentic_cortex_curiosity_circuit_open` (gauge). Best-effort: un fallo al emitir nunca rompe el bucle.
  - TDD: `apps/workers/tests/test_cortex_curiosity_metrics.py` — render determinista de las métricas dado un resultado de pasada; emisión es no-op seguro si el dir del collector no existe.
  - **Aceptación:** tras una pasada feliz, las métricas reflejan `outcome="digested"`, coste>0 y nº de búsquedas; el circuit-breaker abierto pone el gauge a 1.

- [ ] **Doc + ADR flip**
  - ✅ **La mitad documental, hecha (2026-08-19).** Reescritos contra el código los pasajes que
    afirmaban que el owner-approval gate y el tope en USD «no están cableados al bucle»:
    - `docs/roadmap/cortex-system-owner.md` — el banner de cabecera («F4 salió sin owner-approval
      gate ni tope de gasto en USD cableados al bucle, y por eso `cortex.autonomy_enabled` sigue
      OFF») y la sección **Fase 4**, que ahora lleva la tabla punto-del-ADR → dónde vive, con
      `fichero:línea`.
    - `docs/roadmap/mejoras-2026-06-chat-coste-cortex.md` — la viñeta «Beats autónomos con coste».
      Su «Feature 1 → F4» ya estaba: la cabecera dice «✅ F0 HECHO (y F1-F5 después, fuera de este
      plan)».
    - `docs/05-architecture-decisions/0078-...md` — **ya estaba corregido** por el carril de F3:
      el matiz del 2026-07-30 lleva banner de **VENCIDO** y la sección «Estado de implementación
      (2026-08-19)» acredita el gate (`:303`), el `check_and_reserve` (`:281`/`:279`) y el
      `record_spend` (`:372`). No se ha tocado.
  - **La evidencia, para que no haya que volver a rastrearla:** `workers/cortex_curiosity.py:256`
    lee `cortex.curiosity_daily_usd_cap`; `:281` lo reserva junto al cap de búsquedas; `:372`
    liquida con el coste real; `:259` lee `cortex.curiosity_approval_gate` (ON por defecto); `:324`
    deja el pursuit recién elegido en `selected`/`approved IS NULL` **sin salir a Internet** y
    `:303` lo retiene en pasadas posteriores; decide `POST /owner/cortex/curiosity/pursuits/{id}/approve`
    (columna `approved`, migración 0123). Las cuatro métricas las publica
    `workers/cortex_curiosity_metrics.py`, llamado desde `:198`.
  - ⏳ **Lo que queda, y por qué la casilla NO se marca:** el flip de
    `docs/05-architecture-decisions/0078-bucles-cognitivos-fondo-cortex.md` a `accepted-f4` exige
    el **visto bueno del operador** y no se ha hecho (sigue en `accepted` a secas). Hay además un
    argumento escrito **en contra** del propio ADR, en su sección del 2026-08-19: el corpus no usa
    estados por fase —`accepted-f0` del ADR 0074 es el único, por una razón histórica anotada en
    él— así que inventar `accepted-f4` reintroduce la ambigüedad que aquel banner conserva a
    propósito. **Decisión del operador**: (a) flip a `accepted-f4`, o (b) dejarlo en `accepted` y
    cerrar esta casilla apoyándose en el plan y el changelog, que es lo que el propio ADR
    recomienda.
  - **Aceptación:** el roadmap refleja el estado real de F4 ✅; ADR 0078 documenta la aceptación
    parcial ⏳ (pendiente de la decisión de arriba).

---

## Notas de seguridad y restricciones (recordatorio)

- **Aislamiento (Principio 1, excepción consciente ADR 0074):** TODA query a `cortex_curiosity_pursuits` y a `memory_entries` del owner filtra `owner_user_id`/`user_id` **explícito**; cada tarea de acceso lleva su **test cross-owner**.
- **Egress (Principio 2, ADR 0076):** búsqueda web SOLO vía `claude_sdk` (`allowed_tools=["WebSearch","WebFetch"]`); **sin** tool web propia en F4 (camino degradado = no-op). Egress directo del worker/api-server confiable, no abre egress en runtimes de agentes.
- **Catálogo cerrado (ADR 0021):** razonamiento profundo desde `claude_sdk` + `reasoning_effort`; sin 5º proveedor. Degradación a no-op si no hay SDK (ADR 0064), nunca un fallback de proveedor.
- **Gobierno (ADR 0078):** budget caps en Redis + circuit-breaker + kill-switch global + owner-approval gate (ON por defecto) son **parte del MVP**, implementados en la Sub-fase 4.0 ANTES del bucle.
- **Honestidad:** copy ES+EN en el panel deja claro que la curiosidad es un comportamiento programado con límites de coste auditables, no curiosidad consciente.
- **Precondición de auditoría:** arreglar "credencial en `os.environ` global" de `ClaudeAgentProvider` antes de que el worker use `claude_sdk` intensivamente.
