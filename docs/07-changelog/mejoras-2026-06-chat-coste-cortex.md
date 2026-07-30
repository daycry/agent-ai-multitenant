---
plan_id: mejoras-2026-06-chat-coste-cortex
title: "Mejoras 2026-06: coste por modelo, historial de chat, limpieza de streams, ciclo de vida del plan, rol aprobador y F0 del córtex"
completed_at: null
status: pending_human_validation
docs_language: es
---

# Mejoras 2026-06 — coste, chat, streams, ciclo de vida y cimiento del córtex

## Resumen

Plan táctico (no una fase del roadmap maestro) con seis encargos del operador
agrupados. Todas las casillas están `[x]` salvo una marcada explícitamente
`[~]` como no implementada por YAGNI. Esta entrada verifica en el código lo que
cada una entregó.

## Cambios

### Feature 4 — el coste del plan usa el modelo del agente, no `gpt-4o`

`GET /plans/{id}/cost-breakdown` (y el umbral de doble firma) pricing-eaba
**todas** las tareas como `gpt-4o` hardcodeado.

- `chat/cost.py::compute_ai_cost(task_models=…)` — parámetro nuevo
  `dict[task_id, model_id]` con máxima precedencia. Puro.
- [`chat/cost_resolution.py`](../../apps/api-server/src/api_server/chat/cost_resolution.py):
  `resolve_plan_task_models` (resuelve por tarea `role → agente del equipo →
cadena agente→equipo→proyecto→plataforma`, reutilizando
  `resolve_model_config_chain`) y `load_price_catalog` (catálogo desde las filas
  abiertas de `model_prices`, con fallback al catálogo en código).
- Cableado en `routers/plans.py` vía el helper compartido
  `_compute_plan_ai_cost`, de modo que el endpoint y el umbral no puedan
  divergir.
- Frontend: sin cambios — `CostBreakdownSection` ya pintaba `t.model_id`.

### Feature 2 — historial de conversaciones

El backend ya soportaba N conversaciones + soft-delete; el operador quedaba
atrapado en la última. Se añadió la UI: selector/histórico, "Nueva
conversación" **siempre visible** (antes solo aparecía con la lista vacía) y
"Eliminar conversación" con confirmación, más el helper puro
`lib/conversation-history.ts` (`nextActiveAfterDelete`, `conversationLabel`) con
sus tests.

- **No implementado, declarado**: borrado de un mensaje individual (`[~]` en el
  plan). El borrado opera por conversación o por "vaciar chat".

### Feature 3 — borrar un chat limpia DB + Redis sin huérfanos

- `events.py::delete_document_stream(redis, document_id)`, llamado en
  `knowledge_bases.py::delete_document` tras el soft-delete.
- **El contrato que más importa es el negativo, y está testeado**: la limpieza es
  best-effort y **un Redis caído no puede tumbar el borrado del usuario**
  (`test_a_dead_redis_does_not_break_the_document_delete` y su gemelo de
  conversación). Perder un stream huérfano es un incordio; perder el borrado es
  perder una orden explícita.
- **Hallazgo del inventario**: el test que enumera las familias de stream —puesto
  para que una familia nueva no pase inadvertida— encontró dos que el plan no
  listaba. `cortex:telemetry:{owner}` está acotado (una por owner); `exec:{id}`
  **no tenía ni limpieza ni TTL**: una clave en Redis por cada run y para
  siempre (`maxlen` acota lo que pesa cada stream, no cuántos hay). Como no
  existe una operación "borrar ejecución" de la que colgar la limpieza, se le
  puso un **TTL deslizante de 7 días** renovado en el mismo pipeline del `xadd`
  (`_EXECUTION_STREAM_TTL_S = 7 * 24 * 3600`, `events.py:44` y `:132`). Es
  seguro porque el stream es solo el canal EN VIVO: el histórico que pinta el
  visor sale de `executions.steps_log`, en PostgreSQL.

### Feature 5 — ciclo de vida del plan + gating del sync-to-kanban

- **Guard en `sync-to-kanban`**: 409 `plan_not_approved` salvo estado
  `approved`/`in_progress`. Un borrador ya no materializa tareas.
- **`POST /plans/{id}/start-execution`** (`routers/plans.py:1555`): transición
  `approved → in_progress` vía la máquina de estados (409 si no está aprobado) +
  materialización idempotente de las tareas que falten.
- UI: `PlanLifecycleSection` ("Enviar a aprobación" / "Aprobar plan" / "Empezar
  ejecución") y el botón de sync deshabilitado con aviso mientras no proceda.

### Feature 6 — rol `plan_approver`

[ADR 0079](../05-architecture-decisions/0079-rol-aprobacion-de-planes-project-approval.md)
`accepted`, opción A: aprobar planes se desacopla de administrar el tenant.

- `UserRole.PLAN_APPROVER = "plan_approver"` (sin migración: `role` es
  `String(32)` sin CHECK).
- `auth/deps.py::require_can_approve_plan` (:472) acepta
  `tenant_admin ∪ plan_approver`, `system_admin` pasa; **separada** de
  `require_tenant_admin`, no una redefinición.
- `POST /plans/{id}/approve` la usa; la doble firma admite firmantes mixtos.
- UI: el rol aparece en el selector de membresías como "Aprobador de planes".

### Feature 1 — córtex: solo F0

- Migración `0091` (`users.is_system_owner` + UNIQUE parcial = singleton), claim
  JWT `own`, `require_system_owner` / `require_admin_or_owner`
  **DB-authoritative**, bootstrap del primer usuario, `/me` con
  `is_system_owner` y el hook `use-current-user`.
- **Guardrail SSO estructural**: las vías de minteo SSO/MFA no fijan
  `is_system_owner` y el gate consulta la BD — SSO no concede ownership.
- Tests: `tests/integration/test_cortex_f0_ownership.py` (bootstrap, singleton,
  rechazo de un hint forjado, `/me`).

## Divergencia documental que este plan arrastraba — corregida el 2026-07-30

El cuerpo del plan afirmaba, sobre la Feature 1, que el córtex tenía **"cero
código"**, que los ADR 0074-0078 estaban `proposed` y que **"F1-F5 NO
implementadas — gated por fase"**. Eso era cierto el 2026-06-23 y dejó de serlo
ese mismo día: el operador dio luz verde a F1→F5, que se implementaron entre
2026-06-24 y 2026-07-06 y están desplegadas (ver
[cortex-fases](cortex-fases.md) y las cinco entradas por fase).

Corregido en `docs/roadmap/mejoras-2026-06-chat-coste-cortex.md`: la sección de
la Feature 1 declara ahora que **el alcance de este plan es sólo F0** y enlaza
las cinco fases con sus changelogs; el `summary` del frontmatter y la nota de
orden de ejecución dicen que el gate se levantó; y el cierre «F1-F5 quedan
PENDIENTES de luz verde» pasa a explicar **cómo se resolvieron** las tres
preocupaciones que lo motivaban (tablas BYPASSRLS con test cross-owner, egress
por el camino degradado del ADR 0076, beats detrás de un kill-switch que sigue
OFF) y qué queda abierto de ellas.

**Ninguna casilla se marcó ni se desmarcó** al hacerlo: lo que estaba en `[x]`
sigue en `[x]`, y la única coletilla que se tocó dentro de una casilla es la del
ADR 0074, que afirmaba un gate ya levantado.

## Tests

`tests/unit/test_ai_cost_calc.py` (precedencia de `task_models`),
`tests/integration/test_plan_cost_breakdown_endpoint.py` (una tarea con agente
se pricing-ea con SU modelo, no con `gpt-4o`),
`tests/unit/test_redis_stream_cleanup.py` (13, incluidos los dos de Redis
caído y el inventario de familias), los 3 de integración del ciclo de vida del
plan, los del rol `plan_approver` y `test_cortex_f0_ownership.py`. Más 6 tests
vitest del helper de historial de conversaciones.

## Estado de cierre

Pendiente lo humano: el QA en navegador del historial de conversaciones y de la
barra de ciclo de vida del plan, y comprobar con un usuario `plan_approver` real
que aprueba y que un `tenant_user` no.

## PR

- _pendiente_
