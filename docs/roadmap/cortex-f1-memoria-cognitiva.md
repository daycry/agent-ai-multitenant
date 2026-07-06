---
title: "Córtex F1 — Córtex conversacional con memoria persistente"
status: pending_human_validation
blocking_plan:
  [
    "F0 (rol system_owner, IMPLEMENTADO)",
    "ADR 0074 (F1 proposed/gated)",
    "ADR 0076 (proposed)",
    "ADR 0021",
    "ADR 0070",
    "ADR 0054",
    "ADR 0059",
  ]
started_at: 2026-06-23
completed_at: null
date: 2026-06-23
related_adrs: ["0074", "0076", "0021", "0070", "0054", "0059", "0064"]
docs_language: es
---

# Córtex F1 — Córtex conversacional con memoria persistente (mente útil mínima)

> **✅ IMPLEMENTADO Y DESPLEGADO** (verificado 2026-07-06 — auditoría de estado del roadmap). Este
> plan quedó congelado en su estado de diseño (`pending_approval`/GATED) tras el commit `cf8f7cd`,
> pero el código real se implementó en la rama `plan/runs-visor-trabajo` entre 2026-06-24 y
> 2026-07-06: `cortex/graph.py`, `threads.py`, `tools.py`, `model_config.py`, migración
> `0092_cortex_threads`, router `cortex.py` (`/turns`, `/conversations`), página
> `app/admin/cortex/page.tsx`, y la suite `test_cortex_threads*`/`test_cortex_turns_endpoint.py`/
> `test_cortex_cross_owner.py` en verde. **Desviación real vs. plan**: la búsqueda web NO usa el
> camino "WebSearch/WebFetch nativas de claude_sdk" (ADR 0076) que este plan exigía — se implementó
> una tool web provider-agnóstica (`cortex/web.py`, SearXNG/Brave + egress-proxy) bajo el ADR 0067,
> ya `accepted`. Ver [cortex-identidad-real.md](cortex-identidad-real.md) para la capa añadida
> encima (self-model unificado) y el resto de correcciones de la auditoría 2026-07-06. Checkboxes
> de tareas de este documento NO se han re-verificado línea a línea; el status refleja el
> veredicto agregado, no un cierre formal con changelog propio.

## Objetivo

Dar al `system_owner` un córtex conversacional con **hilo persistente entre turnos** (que el asistente de tenant no tiene), **recall asociativo híbrido real** (BM25 + vector + entidad, fusión RRF) sobre su memoria privada, y **deliberación con razonamiento profundo** (`claude_sdk run_agent` con `effort` modulado, degradación limpia sin SDK), expuesto en endpoints `require_system_owner` y una página `app/admin/cortex`.

## Arquitectura

El córtex **reutiliza** el sustrato del asistente sin duplicarlo: clona el turn-loop `decide→run_tools→decide→answer` de `assistant/graph.py` en un `CortexState`/`run_cortex_turn` con los mismos topes (`MAX_TOOL_ROUNDS`, cap 1/turno para la escritura de memoria), reutiliza `memorizer.recall()` (ADR 0059) y `persist_memory_candidates()` directo (NO `workers/memorizer.py`), y resuelve el modelo clonando `resolve_assistant_model` sobre una clave `cortex.default_model` en `platform_settings`. Las tablas `cortex_conversations`/`cortex_turns` son **tenant-less** (singleton del owner): se acceden por `get_admin_sessionmaker` (BYPASSRLS) con **filtro `owner_user_id` explícito en todo SQL** como defensa en profundidad (test cross-owner obligatorio). La deliberación sale **exclusivamente** de `claude_sdk` (ADR 0021/0076); sin el Claude Agent SDK degrada al loop clásico con `reasoning_effort` o un 503 honesto. La memoria sigue viviendo en `memory_entries` con `scope='private'` + `user_id=owner` + `metadata_.cortex=true`, así que conserva un `tenant_id` real (Decisión D1).

## Decisiones de diseño (cerrar con el operador antes de implementar)

- **D1 — `tenant_id` de la memoria del córtex.** `memory_entries` exige `tenant_id` NOT NULL y `recall()`/`_scope_filter_sql()` filtran por él. El córtex es tenant-less, pero su memoria NO puede serlo sin tocar el esquema de `memory_entries` (fuera de alcance F1). **Recomendación:** resolver el `cortex_tenant_id` UNA vez como el tenant de la membresía activa más antigua del owner (vía `get_admin_sessionmaker`, leyendo `user_org_memberships`), **persistirlo en `cortex_conversations.tenant_id`** y reusarlo en cada escritura/recall. Aislamiento real = filtro `user_id=owner` + `metadata_.cortex=true` (el `tenant_id` es solo el discriminante físico que `memory_entries` necesita). Si el owner no tiene ninguna membership → 409 honesto ("el córtex necesita al menos un tenant para su memoria").
- **D2 — `cortex.default_model`.** Clave nueva en `platform_settings` (`cortex.default_model`), misma forma `{provider_id, model_id, reasoning_effort?}` que `assistant.default_model`. `reasoning_effort` alto por defecto (recomendación del diseño). Sin él configurado → 503 honesto (igual que el asistente).
- **D3 — Búsqueda web (Tarea 6) DOBLEMENTE GATED por el ADR 0076.** El resto de F1 (memoria + deliberación con effort, sin web) entrega valor sin ella; NO implementar la web hasta aprobar el ADR 0076.

## Tablas nuevas (migración 0092, reversible)

> Tenant-less, sobre BYPASSRLS, aislamiento por `owner_user_id` explícito. Migración `0092_cortex_threads`, `down_revision = "0091_system_owner_f0"`.

### `cortex_conversations`

- `id` UUID PK (uuid7).
- `owner_user_id` UUID NOT NULL → `users.id` ON DELETE CASCADE (el dueño del hilo; **filtro de aislamiento**).
- `tenant_id` UUID NOT NULL → `organizations.id` (Decisión D1; discriminante físico de la memoria, NO de autorización).
- `title` Text NULL (autoetiquetado del primer mensaje; editable después).
- `model_id` String(128) NULL (el catálogo-id efectivo del turno de creación, p. ej. `claude-sonnet-4-5`; auditoría/UI).
- `created_at` / `updated_at` timestamptz NOT NULL (TimestampMixin).
- `deleted_at` timestamptz NULL (SoftDeleteMixin; soft-delete del hilo).
- Índices: `ix_cortex_conversations_owner` ON (`owner_user_id`, `updated_at` DESC) `WHERE deleted_at IS NULL` (listado "hilos del owner, más recientes primero").

### `cortex_turns`

- `id` UUID PK (uuid7).
- `conversation_id` UUID NOT NULL → `cortex_conversations.id` ON DELETE CASCADE.
- `owner_user_id` UUID NOT NULL → `users.id` ON DELETE CASCADE (**redundante a propósito**: permite el filtro de aislamiento sin join, defensa en profundidad).
- `role` String(16) NOT NULL — CHECK `role IN ('user','cortex')`.
- `content` Text NOT NULL (el mensaje del owner o la respuesta del córtex).
- `model_id` String(128) NULL (modelo que produjo la respuesta; NULL en turnos `user`).
- `tools_called` JSONB NOT NULL server_default `'[]'` (nombres de tools del turno).
- `rounds` Integer NOT NULL server_default `'0'`.
- `reasoning_effort` String(16) NULL (effort efectivo del turno; auditoría).
- `metadata_` ("metadata") JSONB NOT NULL server_default `'{}'` (degraded/sdk, recall_hits, etc.).
- `created_at` timestamptz NOT NULL.
- Índices: `ix_cortex_turns_conversation` ON (`conversation_id`, `created_at`) `WHERE` (sin soft-delete: los turnos son inmutables); `ix_cortex_turns_owner` ON (`owner_user_id`).

## Endpoints / WS

Todos gated por `Depends(require_system_owner)` (DB-authoritative, re-lee `users.is_system_owner`). Prefijo `/owner/cortex` (router nuevo `routers/cortex.py`).

- `POST /owner/cortex/turns` — body `{message: str, conversation_id?: UUID}`. Si `conversation_id` ausente → crea hilo. Persiste el turno `user`, corre el grafo del córtex (recall + augment + deliberación), persiste el turno `cortex`, devuelve `{conversation_id, answer, tools_called, rounds, reasoning_effort, degraded: bool}`.
- `GET /owner/cortex/turns?conversation_id=…&limit=…` — turnos de un hilo del owner, en orden cronológico (filtro `owner_user_id` explícito).
- `GET /owner/cortex/conversations` — hilos del owner (no borrados), más recientes primero, con `last_turn_preview`.
- (F1 no añade WS; el WS de telemetría/voz llega en F2/F5.)

## FASES → TAREAS

> TDD ESTRICTO en cada tarea: escribe el test → corre y FALLA (rojo) → implementa el mínimo → corre y PASA (verde) → commit. Migraciones reversibles (`down()`). Tests en `tests/unit/` y `tests/integration/` (raíz del repo); patrón de fixtures en `tests/integration/conftest.py` (`alembic_config`, `migrations_pg_dsn`, `app_database_url`, `admin_database_url`, `test_redis_url`) y el patrón del test F0 `tests/integration/test_cortex_f0_ownership.py`. Marcadores: `@pytest.mark.integration`, `@pytest.mark.cross_tenant` para el test de aislamiento.

### Bloque A — Persistencia del hilo (memoria episódica de conversación)

- [ ] **Tarea 1 — Migración 0092: `cortex_conversations` + `cortex_turns`**
  - Crear: `apps/api-server/migrations/versions/20260623_0092_cortex_threads.py` (`revision = "0092_cortex_threads"`, `down_revision = "0091_system_owner_f0"`).
  - Modelos ORM: `apps/api-server/src/api_server/db/cortex.py` (clase `CortexConversation`, `CortexTurn`; usar `UUIDPrimaryKeyMixin`, `TimestampMixin`; `CortexConversation` con `SoftDeleteMixin`; **NO** `TenantScopedMixin` con RLS — declarar `tenant_id`/`owner_user_id` como columnas explícitas; ver `db/conversation.py` y `db/memory.py` como referencia de estilo). Registrar el módulo donde `Base.metadata` los recoge (importarlo en `db/__init__.py` o el módulo que agrega modelos para alembic autogenerate).
  - TDD:
    1. Test (rojo): `tests/integration/test_cortex_threads_migration.py` — aplica `alembic upgrade head` sobre la BD throwaway, comprueba que existen las tablas + el índice parcial `ix_cortex_conversations_owner` + el CHECK `role IN ('user','cortex')`; luego `alembic downgrade -1` y comprueba que se eliminan (reversible). Patrón: ver el `configured_app`/`migrations_pg_dsn` de `test_cortex_f0_ownership.py`.
    2. Implementa `upgrade()`/`downgrade()` + modelos ORM.
    3. Verde. Commit: `feat(cortex): migración 0092 cortex_conversations + cortex_turns (F1, tenant-less BYPASSRLS)`.
  - Aceptación: `alembic upgrade head` y `alembic downgrade 0091_system_owner_f0` corren sin error; el CHECK rechaza `role='agent'`.

- [ ] **Tarea 2 — Capa de persistencia del hilo (owner-scoped, BYPASSRLS)**
  - Crear: `apps/api-server/src/api_server/cortex/threads.py` con funciones puras sobre una `AsyncSession` (admin/BYPASSRLS):
    - `resolve_cortex_tenant_id(session, owner_user_id) -> UUID` (Decisión D1: membership activa más antigua; lanza `CortexNoTenantError` si ninguna).
    - `create_conversation(session, *, owner_user_id, tenant_id, model_id) -> CortexConversation`.
    - `append_turn(session, *, conversation_id, owner_user_id, role, content, model_id=None, tools_called=(), rounds=0, reasoning_effort=None, metadata=None) -> CortexTurn` (verifica `conversation.owner_user_id == owner_user_id` ANTES de escribir).
    - `list_conversations(session, *, owner_user_id, limit=50)` y `list_turns(session, *, conversation_id, owner_user_id, limit=100)` — **TODO `SELECT` con `WHERE owner_user_id = :owner` explícito**.
    - `recent_history_for_prompt(session, *, conversation_id, owner_user_id, max_turns=20) -> list[dict]` (los N últimos turnos como `[{role, content}]` para alimentar el grafo).
  - TDD:
    1. Test (rojo): `tests/integration/test_cortex_threads.py::test_append_and_list_turns_owner_scoped` — crea hilo, añade turnos `user`/`cortex`, lista y comprueba orden + contenido; comprueba que `append_turn` con un `owner_user_id` que NO posee el hilo lanza/`403`-equivalente (no escribe).
    2. Implementa.
    3. Verde. Commit: `feat(cortex): capa de persistencia del hilo owner-scoped`.
  - Aceptación: round-trip create→append→list correcto; un owner_user_id ajeno nunca escribe ni lee turnos de otro hilo.

### Bloque B — Grafo reactivo del córtex

- [ ] **Tarea 3 — `CortexState` + `run_cortex_turn` (clon del loop con persistencia de memoria)**
  - Crear: `apps/api-server/src/api_server/cortex/graph.py`. Reutilizar las primitivas de `assistant/graph.py` (`ModelTurn`, `ToolInvocation`, `AssistantModelClient`, `MAX_TOOL_ROUNDS`, `_admissible_tool_calls`, el cap `_PER_TOOL_CALL_CAP`) **importándolas** (no copiar) y construir un `CortexState` análogo a `AssistantState` con: `system_prompt`, `chat_history` (de `recent_history_for_prompt`), `enabled_tools`, `tool_ctx` (un `CortexToolContext` nuevo). Exponer `run_cortex_turn(model, *, system_prompt, enabled_tools, tool_ctx, chat_history) -> AssistantTurnResult` (puede reutilizar `build_assistant_graph` directamente si `CortexState` es compatible; si no, factorizar `build_turn_graph` genérico). Tope de escritura de memoria 1/turno reutilizando `_PER_TOOL_CALL_CAP` con la tool `cortex_remember`.
  - Crear tools owner-scoped: `apps/api-server/src/api_server/cortex/tools.py` — `CortexToolContext(session, owner_user_id, tenant_id)`; tool `cortex_remember` (escribe memoria del córtex vía `persist_memory_candidates` directo con `metadata_.cortex=true`, cap 1/turno) y tool de lectura mínima `cortex_recall_more` (recall híbrido bajo demanda). Registro/`run_cortex_tool`/`cortex_tool_schemas` espejo de `assistant/tools.py`.
  - TDD:
    1. Test (rojo): `tests/unit/test_cortex_graph.py` — con un `ScriptedAssistantModel` (un round que llama `cortex_remember`, luego una respuesta), comprueba que `run_cortex_turn` devuelve la respuesta y `tools_called` contiene `cortex_remember`, y que un modelo que re-llama `cortex_remember` 3 veces se capa a 1 (reusa el cap). Espejo de `tests/unit/test_assistant_tool_caps.py`.
    2. Implementa.
    3. Verde. Commit: `feat(cortex): grafo reactivo CortexState + run_cortex_turn`.
  - Aceptación: el grafo converge, respeta `MAX_TOOL_ROUNDS` y el cap 1/turno de la escritura.

### Bloque C — Recall asociativo híbrido real

- [ ] **Tarea 4 — Recall híbrido del córtex (BM25 + vector + entidad, RRF), filtrado por owner**
  - Crear: `apps/api-server/src/api_server/cortex/memory.py`:
    - `cortex_recall(session, *, owner_user_id, tenant_id, query, query_embedding=None, limit=8) -> list[str]` — llama a `memorizer.recall.recall(session, query=query, tenant_id=tenant_id, scopes=("private",), user_id=owner_user_id, query_embedding=query_embedding, limit=limit)`. El filtro `scope='private' AND user_id=:user_id` de `_scope_filter_sql()` garantiza el aislamiento por owner; además se filtra `metadata_.cortex=true` post-fetch (o se añade el predicado al recall si se decide extender). **Sustituye el MVP "N recientes"** de `recall_user_memories`.
    - `cortex_remember(session, *, owner_user_id, tenant_id, content, type='semantic', tags=()) -> dict` — `persist_memory_candidates(... scope='private', user_id=owner_user_id, extra_metadata={'source':'cortex','cortex':True})` directo (NO `workers/memorizer.py`); dedup como `remember_user_fact`.
    - `augment_cortex_prompt(base, *, known_facts, remember_enabled)` — reutiliza/extiende `assistant.memory.augment_system_prompt` (mismo blindaje anti-inyección de los marcadores `<<<DATOS>>>`).
  - Embedding de la query (path vectorial): best-effort con el `OllamaEmbedder` (`ingestion/embeddings.py`); si falla → `query_embedding=None` y el recall cae a BM25+entidad (igual que `persistence._embed_contents` nunca bloquea).
  - TDD:
    1. Test (rojo): `tests/integration/test_cortex_recall.py::test_recall_hybrid_owner_only` — siembra memorias del córtex del owner (varias) + memorias `private` de OTRO usuario en el mismo tenant; comprueba que `cortex_recall` con una query asociativa devuelve las del owner ordenadas por RRF y **NUNCA** las del otro usuario (sin pasar query_embedding → BM25+entidad). Espejo de `test_assistant_memory.py::test_recall_is_isolated_per_user` pero con recall híbrido.
    2. Implementa.
    3. Verde. Commit: `feat(cortex): recall asociativo híbrido (RRF) sustituyendo MVP N-recientes`.
  - Aceptación: el recall devuelve resultados rankeados por RRF y jamás cruza de usuario.

### Bloque D — Deliberación con effort modulado (claude_sdk) + degradación

- [ ] **Tarea 5 — Resolución `cortex.default_model` + builder del modelo del córtex (degradación limpia)**
  - Crear: `apps/api-server/src/api_server/cortex/model_config.py` — `CORTEX_DEFAULT_MODEL_KEY = "cortex.default_model"`; `get/set/clear_cortex_default_model` y `resolve_cortex_model(admin_session) -> ResolvedAssistantModel | None` clonando `assistant/model_config.py::resolve_assistant_model` pero **solo platform-default** (el córtex es singleton, sin override por tenant). Reutiliza `is_valid_selection`, `to_provider_model_name`, `_selection_from_value` (importadas).
  - Builder en el router (Tarea 7): `resolve_cortex_model` → si `provider_kind == "claude_sdk"` y `not _claude_sdk_available()` → **degradación**: si hay otro modelo configurable se usa el loop clásico con `reasoning_call_kwargs`; si no, 503 honesto (copia el `_claude_sdk_available()` y el patrón 503 de `routers/assistant.py:121-191`). El `effort` se propaga vía `LLMAssistantModel(extra_call_kwargs=reasoning_call_kwargs(kind, reasoning_effort))` — y, como `run_agent` YA propaga `effort` (claude_agent.py:428-434, fix F0), el razonamiento profundo no se ignora.
  - TDD:
    1. Test (rojo): `tests/unit/test_cortex_model_factory.py` — `reasoning_call_kwargs("claude_sdk", "high") == {"effort":"high"}` se cablea al `extra_call_kwargs`; y un kind no-claude con `reasoning_effort` produce `{"reasoning_effort": …}`. Espejo de `tests/unit/test_assistant_llm_reasoning.py`.
    2. Test (rojo) integración: `tests/integration/test_cortex_degradation.py::test_503_when_claude_sdk_missing` — modelo `claude_sdk` configurado pero SDK ausente (monkeypatch `_claude_sdk_available` → False y sin alternativa) → `POST /owner/cortex/turns` responde 503 honesto, NO 500.
    3. Implementa.
    4. Verde. Commit: `feat(cortex): resolución cortex.default_model + effort modulado + degradación 503`.
  - Aceptación: con SDK presente el `effort` llega al provider; sin SDK ni alternativa → 503 claro (nunca 500).

- [ ] **Tarea 6 — 🔒 GATED (ADR 0076): WebSearch/WebFetch nativas del Claude Agent SDK**
  - **NO implementar hasta aprobar el ADR 0076.** Cuando se apruebe: en el builder del modelo del córtex (solo para `provider_kind == "claude_sdk"`), pasar `allowed_tools=["WebSearch","WebFetch"]` a la vía agéntica (`ClaudeAgentProvider.run_agent`/`_build_options`), de modo que Anthropic gestione el fetch (anti-SSRF gratis, sin abrir egress en runtimes, sin ADR 0067). Sin claude_sdk → SIN web (camino degradado de web propia queda fuera de F1: requiere su propio ADR con anti-SSRF). Verificar antes el prerequisito de seguridad del ADR 0076 ("credencial en os.environ global" de `ClaudeAgentProvider`).
  - TDD (cuando se desbloquee): test con `query_fn` inyectado que comprueba que `allowed_tools` incluye `WebSearch`/`WebFetch` solo en el camino claude_sdk.
  - Aceptación: la web solo se activa con claude_sdk y ADR 0076 aprobado; sin SDK el córtex sigue respondiendo (sin web) o 503.

### Bloque E — Endpoints + página

- [ ] **Tarea 7 — Router `/owner/cortex/*` (gate require_system_owner)**
  - Crear: `apps/api-server/src/api_server/routers/cortex.py` (router `cortex_router`, prefix `/owner/cortex`). Endpoints `POST /turns`, `GET /turns`, `GET /conversations` (ver sección Endpoints). Todos `Depends(require_system_owner)`. Inyección del modelo vía dependencia `get_cortex_model` overrideable en tests (espejo exacto de `get_assistant_model`). Sesión: abrir `get_admin_sessionmaker()` internamente (BYPASSRLS) y filtrar `owner_user_id` explícito; manejar `AuthError/RateLimitError/LLMError` → 502/429/502 (copia el bloque de `routers/assistant.py:230-251`).
  - Crear schemas: `apps/api-server/src/api_server/schemas/cortex.py` (`CortexTurnRequest`, `CortexTurnResponse`, `CortexConversationResponse`, `CortexTurnItem`) — espejo de `schemas/assistant.py`.
  - Registrar el router: añadir `cortex_router` a la lista de `apps/api-server/src/api_server/routers/__init__.py` y al bloque `include_router` de `apps/api-server/src/api_server/main.py:200-238`.
  - TDD:
    1. Test (rojo): `tests/integration/test_cortex_turns_endpoint.py::test_post_turn_persists_and_returns_answer` — owner mintea token (patrón `_mint` de `test_assistant_memory.py`), override `get_cortex_model` con `ScriptedAssistantModel`, `POST /owner/cortex/turns {message}` → 200, devuelve `conversation_id` + `answer`; un segundo POST con ese `conversation_id` añade turno y `GET /owner/cortex/turns` devuelve los 4 turnos en orden.
    2. Implementa.
    3. Verde. Commit: `feat(cortex): endpoints /owner/cortex/{turns,conversations} (gate require_system_owner)`.
  - Aceptación: hilo persistente verificable por API; el segundo turno ve el primero como historia.

- [ ] **Tarea 8 — Gate 403 + listado de hilos**
  - TDD:
    1. Test (rojo): `tests/integration/test_cortex_turns_endpoint.py::test_non_owner_gets_403` — un usuario con membership tenant_admin pero `is_system_owner=false` (incluso con claim `own` forjado) recibe **403** en `POST /owner/cortex/turns` y `GET /owner/cortex/conversations` (gate DB-authoritative). Espejo del `test_require_system_owner_gate_checks_the_db` de F0 pero a nivel HTTP.
    2. Test (rojo): `test_list_conversations_owner_scoped` — owner crea 2 hilos; `GET /owner/cortex/conversations` los lista (más reciente primero) con preview.
    3. Implementa lo que falte.
    4. Verde. Commit: `test(cortex): gate 403 no-owner + listado de hilos`.
  - Aceptación: no-owner → 403 en todos los endpoints; listado correcto y ordenado.

- [ ] **Tarea 9 — 🔒 Test cross-owner OBLIGATORIO (excepción al Principio 1)**
  - TDD:
    1. Test (rojo): `tests/integration/test_cortex_cross_owner.py` (`@pytest.mark.cross_tenant`) — como el owner es singleton, simular a nivel de DATOS dos `owner_user_id` distintos en `cortex_conversations`/`cortex_turns` (insert directo con dos user ids) y comprobar que `list_turns`/`list_conversations`/`append_turn` con `owner_user_id=A` **NUNCA** ven ni tocan filas de `owner_user_id=B`, aun corriendo sobre BYPASSRLS (no hay RLS que proteja: solo el filtro explícito). Verificar que cada `SELECT`/`UPDATE` de `cortex/threads.py` lleva el `WHERE owner_user_id`.
    2. Implementa el filtro donde falte.
    3. Verde. Commit: `test(cortex): aislamiento cross-owner sobre BYPASSRLS (excepción consciente al Principio 1)`.
  - Aceptación: ningún acceso cruza de owner; este test es la condición de mérito de seguridad de F1.

- [ ] **Tarea 10 — Cableado fin-a-fin: turno con recall + augment + deliberación**
  - En `POST /owner/cortex/turns`: resolver tenant (D1) → persistir turno `user` → `cortex_recall` (Tarea 4) → `augment_cortex_prompt` → `run_cortex_turn` con el modelo resuelto (Tarea 5/7) y `chat_history=recent_history_for_prompt` → persistir turno `cortex` (con `tools_called`, `rounds`, `reasoning_effort`, `metadata.degraded`).
  - TDD:
    1. Test (rojo): `tests/integration/test_cortex_recall_in_chat.py` — siembra una memoria del córtex ("Al owner le interesa la arquitectura hexagonal"); con `ScriptedAssistantModel` que NO llama tools, comprueba que el `system_prompt` pasado al modelo (capturado por un fake que registra el state) contiene esa memoria recallada (recall híbrido funcionando en el hot-path). Reutiliza el patrón de captura de `decide` del test del asistente.
    2. Implementa el cableado.
    3. Verde. Commit: `feat(cortex): cableado turno = recall híbrido + augment + deliberación + persistencia`.
  - Aceptación: una memoria relevante del owner aparece en el prompt del siguiente turno sin tool call.

### Bloque F — Frontend (página del córtex)

- [ ] **Tarea 11 — NAV: grupo "Córtex" `systemOwnerOnly`**
  - Modificar: `apps/admin-panel/components/layout/admin-shell.tsx` — añadir `systemOwnerOnly?: boolean` a `NavItem` y `NavGroup`; en `SidebarContent` leer `isSystemOwner` de `useCurrentUser()` (ya existe) y extender `itemVisible`/`groupVisible` (`if (item.systemOwnerOnly) return isSystemOwner;` / idem grupo). Añadir un `NavGroup` `{ id: "cortex", label: "Córtex", Icon: Brain, systemOwnerOnly: true, items: [{ href: "/admin/cortex", label: "Córtex", Icon: Brain }] }` (separado, como pide el diseño).
  - TDD:
    1. Test (rojo): e2e/unit del shell que comprueba que el grupo "Córtex" se muestra con `isSystemOwner=true` y se oculta para un tenant_admin no-owner (mock de `useCurrentUser`). Si no hay test unit del shell, añadir uno mínimo (`apps/admin-panel/__tests__/admin-shell-cortex.test.tsx`) o un Playwright `nav-cortex` visible/oculto.
    2. Implementa.
    3. Verde. Commit: `feat(admin): grupo NAV "Córtex" systemOwnerOnly`.
  - Aceptación: el grupo solo lo ve el system_owner; el backend sigue siendo la barrera real.

- [ ] **Tarea 12 — Página `app/admin/cortex/page.tsx` (chat persistente + preview Markdown)**
  - Crear: `apps/admin-panel/app/admin/cortex/page.tsx` — réplica del patrón de `app/admin/assistant/page.tsx` pero (a) gated por `isSystemOwner` (no `isTenantAdmin`); (b) **hilo persistente**: al montar, `GET /owner/cortex/conversations` y un selector de hilo (reutilizar el patrón de historial de `projects/[id]/chat`); `POST /owner/cortex/turns` con `conversation_id`; cargar turnos con `GET /owner/cortex/turns`; (c) respuestas del córtex con preview Markdown vía `renderPlanDraft` (igual que el asistente); (d) indicador "pensando profundo" cuando `reasoning_effort` alto; (e) **copy honesto** (sin afirmar emociones/consciencia — F1 no tiene afecto aún). `data-testid`: `cortex-input`, `cortex-send`, `cortex-chat`, `cortex-answer`, `cortex-no-access`.
  - Crear helpers/tipos: `apps/admin-panel/lib/cortex.ts` (tipos `CortexTurnResponse`, `CortexConversation`, `cortexFetch`).
  - TDD:
    1. Test (rojo): Playwright `apps/admin-panel/e2e/cortex.spec.ts` — un system_owner ve `cortex-input`; un tenant_admin no-owner ve `cortex-no-access` y `cortex-input` count 0; enviar mensaje renderiza una respuesta Markdown. Espejo de la e2e del asistente.
    2. Implementa.
    3. Verde. Commit: `feat(admin): página /admin/cortex con chat de hilo persistente + preview Markdown`.
  - Aceptación: el owner conversa con hilo persistente; un no-owner no ve el input.

## Riesgos / notas de cumplimiento

- **Principio 1 (RLS) — excepción consciente:** Tarea 9 (cross-owner) es la prueba de mérito. Todo `SELECT`/`UPDATE`/`DELETE` de `cortex/threads.py` y `cortex/memory.py` lleva `WHERE owner_user_id = :owner` explícito; nunca confiar en RLS (no la hay).
- **ADR 0021 (catálogo cerrado):** el córtex NO añade 5º proveedor; deliberación = `claude_sdk` (effort) o degradación a otro kind del catálogo / 503.
- **Honestidad de producto:** F1 NO simula afecto — el copy de la UI no debe insinuar emociones/consciencia (eso llega en F2 con copy honesto explícito).
- **Egress:** la web (Tarea 6) queda gated por ADR 0076; sin él, el córtex razona pero no busca en Internet.
- **Prerequisito de seguridad:** antes de uso intensivo de claude_sdk en el api-server, atender el hallazgo "credencial en os.environ global" de `ClaudeAgentProvider` (ADR 0076).

## Ficheros críticos para la implementación

- apps/api-server/src/api_server/assistant/graph.py (loop a clonar/reutilizar: `MAX_TOOL_ROUNDS`, `_admissible_tool_calls`, cap 1/turno)
- apps/api-server/src/api_server/memorizer/recall.py (recall híbrido BM25+vector+entidad RRF a cablear con `scopes=('private',)`, `user_id=owner`)
- apps/api-server/src/api_server/memorizer/persistence.py (`persist_memory_candidates` directo, NO el worker)
- apps/api-server/src/api_server/auth/deps.py (`require_system_owner` DB-authoritative, `get_admin_sessionmaker` BYPASSRLS)
- apps/api-server/src/api_server/routers/assistant.py + assistant/model_config.py (patrón a clonar para endpoints, resolución de modelo, degradación 503 y effort)
