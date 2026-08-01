---
plan_id: prod-07-fiabilidad-llm-costes
title: Capa LLM fiable y contabilidad de costes exacta
started_at: null
completed_at: null
status: pending_approval
tasks_done: 14
tasks_total: 16
tasks_pending_local:
  - task_prod07_10
docs_language: es
---

> **Estado: el plan NO está cerrado.** Esta entrada se escribe por adelantado
> (`task_prod07_16` la pide «para el cierre») y se mantiene al día conforme
> caen las tareas. **14 de 16** están entregadas con sus tests en verde; queda
> abierta `task_prod07_10` (sacar la credencial del env del contenedor a un
> mount tmpfs), que toca `apps/workers/execution.py`, `container.py` y la
> imagen `agent-runtime`. Los **6 tests humanos** del plan siguen sin validar y
> el `status: completed` no se fija hasta ellos.

# Changelog — Plan prod-07 · Capa LLM fiable y contabilidad de costes exacta

Cierra 13 de los 14 hallazgos de la auditoría de producción (2026-06) sobre
`shared-llm` y su contabilidad: `llm-1`…`llm-10`, `llm-12`, `llm-13` y
`workers-8`. Queda `llm-11` (la credencial en el env del contenedor).

## Resultado por fases

- **Fase A — Fiabilidad (tasks 01-06):** `with_retries()` en `shared-llm`
  aplicado en los adaptadores (runtime + asistente) respetando `Retry-After`;
  `asyncio.Runner` persistente por ejecución en el agent-runtime, que mata el
  `RuntimeError: Event loop is closed` de la iteración 2; `await resp.aread()`
  antes de `check_status` en streaming, para que un no-2xx sea un `AuthError`
  tipado y no un `ResponseNotRead` opaco; `stream_options.include_usage` +
  parseo de `tool_calls` en SSE, con test de paridad `complete()` ↔ `stream()`;
  la dependencia del asistente pasa a async generator que cierra su provider en
  el `finally` (incluido el camino de ERROR, que era el que más fugaba); y
  `ClaudeAgentProvider` deja de mutar `os.environ`.

- **Fase B — Credenciales y capacidades (tasks 07-09, 11):** Vault caído es
  ahora `ModelResolutionError` con `abort_code=vault_unavailable` **antes** de
  lanzar el contenedor, en vez de un 401 dentro del sandbox que culpaba al
  proveedor; el mapeo kind→campos vive una sola vez en
  `shared_llm/credential_fields.py` (y de paso el worker aprendió el
  `bearer_token` de Azure, que solo conocía el factory); el probe de conexión
  mintea el JWT de Copilot de verdad —un token revocado ya no luce verde— y
  acepta configs de Azure bearer-only.

  **`task_prod07_09` se cerró al revés de como se planificó**: pedía bloquear
  la combinación `tools + claude_sdk`, y esa premisa había caducado. El
  **ADR 0150** (`accepted`, opción A) lo registra: `complete()` sí honra las
  tools —las anuncia como servidor MCP in-process y captura la llamada con
  `can_use_tool`— y `ClaudeSDKModelClient` hereda `decide()`, así que alcanza
  ACT como los OpenAI-compat. Implementar el bloqueo habría roto el proveedor
  principal de la plataforma. Del original solo sobrevivió (c), el timeout del
  SDK, que sí hacía falta.

- **Fase C — Contabilidad de costes (tasks 12-14):** el hallazgo `llm-1` tenía
  tres tramos y ahora los tres tienen prueba. El paso `model_call` lleva el
  `provider`, y su clave nativa casa con la del catálogo (tecleada a la
  LiteLLM); `_billable_cost_usd` impone la precedencia **runtime > catálogo >
  0** en las dos vías de escritura de `executions`; y el e2e recorre la cadena
  entera, desde un endpoint OpenAI-compat real que no manda `cost` hasta un
  proyecto que **cruza el umbral del 80 %** de su presupuesto. Ahí es donde
  dolía: no en la columna, sino en que un proyecto que gastaba de verdad
  marcaba 0 % usado para siempre.

  **Decisión tomada aquí** (el plan la delegaba a `task_prod07_13`): se
  sobrescribe `total_cost_usd` en vez de añadir una columna
  `cost_estimated_usd`. La trazabilidad que la columna iba a dar ya existe por
  llamada en `steps_log` —el `cost_usd` crudo del runtime junto a un
  `price_snapshot` con su `source` y su `price_id`—, y una columna obligaría a
  cada lector de la cifra facturable a aprender a hacer coalesce o a seguir
  contando de menos. **No hubo migración Alembic.**

- **Fase D — Memorizer y documentación (tasks 15-16):** el destilador tiene
  tres escalones (provider del agente → fila activa del catálogo → env), con lo
  que la precedencia «fila de BD > env» pierde su única excepción; y su muerte
  deja de ser muda: `agentic_memorizer_consecutive_distill_failures` +
  la regla `MemorizerDistillationFailing`. La referencia
  `docs/04-reference/llm-providers.md` documenta las cinco áreas que pedía el
  plan (reintentos, streaming, capacidades por kind, costes, Vault) y lleva una
  §8 con lo que **no** está.

## Lo que NO entra, y por qué

- **`task_prod07_10`** (`llm-11`, severidad `low`) — la credencial sigue
  viajando dentro de `AGENT_TASK_SPEC`. Exige tocar el lanzador del contenedor
  y **reconstruir la imagen `agent-runtime`** con un formato de spec versionado
  que acepte los dos durante una versión; hacerlo a medias rompe runs en vuelo.
- **Enforcement de budgets** sobre el coste ya exacto — es **prod-06**. Este
  plan garantiza que la cifra que consume es correcta.
- **Operabilidad de Vault** (HA, unseal, AppRole) — es **prod-10**. Aquí solo
  el comportamiento fail-fast del consumidor.

## Trampas que costaron tiempo (para el siguiente)

- **El cap de tokens del runtime corta el run sin decirlo.** Un test e2e con
  llamadas de 100 000 tokens tropieza con `safeguards.RunBudgets.max_tokens` y
  se aborta en la PRIMERA llamada: el test seguía verde midiendo un sexto del
  gasto. La calibración del e2e (62 000 tokens) va escrita con su porqué.
- **Un porcentaje de presupuesto redondeado a 0,0 no distingue nada.** Con un
  run de juguete (0,0015 USD) el test no podía separar «budgets consume la
  cifra corregida» de «budgets sigue en cero».
