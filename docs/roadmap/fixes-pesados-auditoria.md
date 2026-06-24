---
title: "Fixes pesados de la auditoría — mini-diseños TDD"
type: plan
status: pending_approval
date: 2026-06-22
author: claude-opus (workflow diseño + revisión adversarial)
related: auditoria-zonas-2026-06.md
docs_language: es
---

# Fixes pesados de la auditoría — mini-diseños TDD

> Los 3 hallazgos de la auditoría que **no son quick-wins** (migraciones + workers + Celery + tests de integración). Cada uno fue diseñado por un agente y **revisado adversarialmente** por otro que leyó los tests/código y encontró **bloqueantes reales**. Severidad/origen en `auditoria-zonas-2026-06.md`. Implementar con TDD, respetando los **must-address** de la revisión.

---

## 1 · Endpoint de cancelación de ejecución (`POST /executions/{id}/cancel`) — esfuerzo L

**Problema:** cancelar una tarea no revoca la task Celery ni mata el contenedor; el run sigue gastando LLM hasta el timeout.

**Enfoque (cancelación cooperativa, 3 líneas de defensa):**

1. Migración 0090 (reversible): `executions.cancel_requested_at` (timestamptz null) + `executions.celery_task_id` (str null). `ExecutionStatus.CANCELLED`. El **worker** estampa `celery_task_id = self.request.id` al crear el running row (no tocar el path revert-on-failure del orchestrator).
2. Endpoint `POST /executions/{id}/cancel` (`require_tenant_member` + sesión tenant-scoped → RLS da 404 cross-tenant): marca `cancel_requested_at` (idempotente), revoca la task Celery, publica `execution.cancel_requested`. El **kill del contenedor lo hace el worker**, no el api-server (aislamiento).
3. `conduct_execution`: poll del flag + kill por label `com.agentic-platform.execution-id`; `finalize_execution` trata `cancelled` como terminal y no lo sobrescribe.

**⚠️ MUST-ADDRESS (de la revisión adversarial — bloqueantes):**

- **`SoftTimeLimitExceeded` se captura en `run_execution()` (hilo principal de Celery), NO alrededor de `run_streamed`** (que corre en `asyncio.to_thread`, otro hilo; el handler nunca lo vería). Allí: leer `cancel_requested_at` para clasificar — con flag → `cancelled` SIN DLQ; sin flag → timeout/failed CON DLQ (comportamiento actual). Test: soft-timeout SIN flag sigue yendo al DLQ (no regresión).
- **Sacar el poll del flag del drain loop** (acoplado a stdout): un contenedor silencioso bloquea `queue.get()` y nunca consulta el flag. Ponerlo en una tarea asyncio periódica o en el lado de `_await_exit`.
- **`supersede_running_executions` debe respetar `cancel_requested_at`** (cerrar como `cancelled`, no `failed`/`superseded`): `revoke(terminate=True)` + `acks_late` provoca reentrega. Test del orden cancel→reentrega.
- **`signal='SIGUSR1'` colisiona con el soft-timeout de Celery** → el discriminador autoritativo es el flag en BD (o usar SIGTERM); el handler re-lee `cancel_requested_at`.
- `celery_task_id` como kwarg **opcional** (default None) en `create_running_execution`/`conduct_execution` (no romper `test_worker_runs_execution.py` ni `test_celery_idempotency.py`).
- **Mantener fuera del PUT de tasks** (no enforcement de máquina de estados allí — rompería `test_tasks_endpoints.py::test_task_crud_with_status_moves`).

**Conflictos confirmados:** ninguno con tests existentes si se respeta lo anterior. El `ExecutionStatus` espejo del SDK es **infundado** (no existe; se regenera por OpenAPI).

---

## 2 · Respuesta del chat → Celery durable — esfuerzo M

**Problema:** la respuesta del equipo corre como `asyncio.create_task` en proceso; se pierde al reiniciar y nada la reintenta.

**Enfoque (patrón "memorize-style"):** `workers/chat_reply.py` con `@app.task` que hace `asyncio.run(respond_to_conversation(...))`; `trigger_respond_to_conversation` encola con `apply_async` (best-effort). `responder.schedule_reply` deja de hacer `create_task` y encola Celery. Idempotencia/serialización: columna `conversations.reply_pending_at` (migración 0090) + lock Redis `SET NX EX` por conversación + barrido `reap_orphan_chat_replies` (beat) para huérfanas.

**⚠️ MUST-ADDRESS (de la revisión adversarial — 2 BLOQUEANTES de infra):**

- **`respond_to_conversation` usa `get_admin_sessionmaker()` → settings de api-server (`API_SERVER_*`), que el worker NO tiene** (solo `WORKERS_*`). **Inyectar el sessionmaker** (firma nueva `sessionmaker: async_sessionmaker`) construido en el worker desde `WORKERS_DATABASE_URL` (patrón memorizer/`_run_execution`), `engine.dispose()` en finally. Sigue BYPASSRLS → la defensa cross-tenant por `conv.tenant_id != tenant_id` es la única barrera, mantenerla. **El diseño afirmaba "no necesita cambios internos" → FALSO.**
- **El WS de chat (`/ws/conversation/{id}`) lee la Redis de api-server (db/0)**; el worker publicaría a `events_redis_url` (`WORKERS_EVENTS_REDIS_URL` = db/3) → **el chat en vivo NO recibiría los mensajes** (solo al refrescar). Publicar al Redis correcto (db/0). **Antes de codificar: confirmar a qué Redis publica HOY el streaming de execution y a cuál lo lee su WS** — puede haber un bug latente preexistente (exec en db/3, WS en db/0) que no se debe heredar. Decisión de wiring explícita + test de integración.
- Import **lazy** de `workers.chat_reply` en el factory after-commit (no acoplar api-server→workers en import-time) o seam `_enqueue` inyectable.
- TTL del lock > `_STEP_TIMEOUT_S`(150s) × pasos de un planning largo; release en finally.
- **Reescribir `test_conversation_responder_after_commit.py`** al contrato de enqueue (monkeypatch del trigger + afirmar "al encolar, el mensaje user ya está committeado") — codifica el mecanismo viejo (espera ejecución in-loop). Es el único fichero de test en conflicto (no "el área").

---

## 3 · Idempotencia/dedup de la auto-destilación — esfuerzo M

**Problema:** `task_acks_late=True` global → un redelivery re-destila (otra llamada LLM) y re-persiste filas duplicadas; `_persist_routed` commitea por-grupo (estado parcial).

**Enfoque (defensa en capas, espejo de `supersede_running_executions`):**

1. **Guard temprano** en `_memorize_execution_async`/`_memorize_human_work_session_async`: antes de la llamada LLM, `count_memories_for_source(source_execution_id)`; si >0 → return `ok:already_memorized` (evita la 2ª llamada LLM no determinista). **Esta es la barrera principal y la parte MÁS SEGURA** (no toca `persist_memory_candidates`).
2. **Transacción única** en `_persist_routed` (un solo `session.begin()` para todos los grupos de scope, no uno por grupo).
3. **Backstop de carrera:** índice UNIQUE parcial `(tenant_id, source_execution_id, md5(content)) WHERE source_execution_id IS NOT NULL AND deleted_at IS NULL` (migración 0090) + `ON CONFLICT DO NOTHING`.

**⚠️ MUST-ADDRESS (de la revisión adversarial — BLOQUEANTE):**

- **NO migrar `persist_memory_candidates` a Core `insert().returning()` sin más:** 3 callers externos dependen de que el retorno sean **instancias ORM adjuntas** y hacen `session.refresh()`: `routers/memories.py` (POST /memories), `routers/internal_agent.py` (memory_store del agent-runtime), `assistant/memory.py`. Romperían con `InvalidRequestError`/`IndexError`. **Opción A:** tras el insert, re-SELECT de los `MemoryEntry` por id en la misma sesión y devolver ORM gestionado. **Opción B:** mantener `session.add()` para el path single-candidate de los routers y aplicar `ON CONFLICT` solo en el path batch del memorizer. **Decidir explícitamente.**
- Blindar `rows[0]` ante `RETURNING` vacío (conflicto).
- Verificar serialización del embedding pgvector `Vector` bajo `insert().values()` (None y list[float]).
- Añadir tests de regresión de los 3 callers externos (el diseño solo listaba tests del memorizer).
- **Recomendación de alcance:** entregar primero **solo el guard (1)** — es seguro, sin migración, sin tocar el contrato de retorno, y resuelve el caso común (redelivery secuencial tras crash/timeout). El backstop de índice (3) + transacción única (2) como segunda PR con los must-address resueltos.

---

## Orden sugerido de implementación

1. **Dedup — solo el guard** (más seguro, alto valor, sin migración).
2. **Cancel-execution** (alto valor operativo; respetar los 4 must-address de hilos/señales).
3. **Chat→Celery** (resolver primero los 2 bloqueantes de infra: sessionmaker inyectado + Redis db correcta).
4. Dedup — backstop de índice + transacción única (con los callers arreglados).
