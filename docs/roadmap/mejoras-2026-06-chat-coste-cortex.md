---
title: "Mejoras 2026-06: coste por modelo, historial de chat, limpieza de streams y córtex"
status: in_progress
started_at: 2026-06-23
blocking_plan: null
owner: operator
summary: >
  Cuatro encargos del operador agrupados en un plan táctico (no es una fase del
  roadmap maestro): (4) el coste del plan se calcula por el modelo del agente
  asignado, no gpt-4o; (2) historial de conversaciones con creación/listado/
  borrado; (3) borrar un chat limpia DB + Redis sin huérfanos; (1) córtex /
  memoria cognitiva — F0 (rol system_owner) ahora, F1-F5 GATED.
---

# Mejoras 2026-06 — coste, chat e inicio del córtex

Plan táctico nacido de cuatro peticiones del operador (2026-06-23). Mapa de
código levantado con un workflow de investigación (4 agentes `Explore`). Cada
feature lleva su tamaño, riesgo y estado real.

> Orden de ejecución: **#4 → #2/#3 → #1 (solo F0)**. El córtex completo es XL +
> security-critical (tablas BYPASSRLS, bucles autónomos de coste/egress) y queda
> a la espera de luz verde explícita salvo su cimiento F0.

---

## Feature 4 — Coste del plan por modelo del agente (no gpt-4o) · L · ✅ HECHO

**Problema.** `GET /plans/{id}/cost-breakdown` (y el cálculo del umbral de doble
firma) pricing-eaba TODAS las tareas como `gpt-4o` hardcodeado: el modelo real
del agente asignado (override o heredado, ADR 0065) no se usaba.

**Diseño.** La capa pura `compute_ai_cost` ya admitía un `model` por tarea; el
hueco era resolver ese modelo desde el agente. Cambio aditivo y de bajo riesgo:
las tareas cuyo `role` mapea a un agente del equipo se pricing-ean con el modelo
efectivo de ese agente; el resto mantiene el fallback `?model=` → `metadata` →
`gpt-4o`. Catálogo de precios desde la tabla `model_prices` (Plan 11), con
fallback al catálogo en código.

- [x] **`compute_ai_cost(task_models=...)`** — nuevo parámetro `dict[task_id, model_id]` con máxima precedencia. Pura, testeada. `apps/api-server/src/api_server/chat/cost.py`
- [x] **`resolve_plan_task_models(session, plan)`** — resuelve por tarea `role → agente del equipo → cadena agente→equipo→proyecto→plataforma` (reusa `resolve_model_config_chain`). `apps/api-server/src/api_server/chat/cost_resolution.py`
- [x] **`load_price_catalog(session)`** — `PriceCatalog` desde las filas abiertas de `model_prices`, sobre el catálogo placeholder. `cost_resolution.py`
- [x] **Cableado del endpoint + umbral de doble firma** — helper compartido `_compute_plan_ai_cost`. `apps/api-server/src/api_server/routers/plans.py`
- [x] **Frontend** — sin cambios: `CostBreakdownSection` ya renderiza `t.model_id` por tarea.
- [x] **Tests** — unit `tests/unit/test_ai_cost_calc.py` (precedencia de `task_models`); integración `tests/integration/test_plan_cost_breakdown_endpoint.py` (tarea con agente usa su modelo, no gpt-4o). **13 unit + 4 integración en verde.**

---

## Feature 2 — Historial de conversaciones · M · ⏳ PENDIENTE

**Estado.** El backend ya soporta N conversaciones por proyecto + soft-delete
(`deleted_at`) + `DELETE /conversations/{id}` (limpia Redis) + `GET
/projects/{id}/conversations`. **Falta UI** y un endpoint de borrado de mensaje
individual.

- [ ] **Backend** — `DELETE /conversations/{id}/messages/{message_id}` (hard-delete, RLS, 404 si no existe). `apps/api-server/src/api_server/routers/conversations.py`
- [ ] **UI: selector/histórico** — panel/dropdown que lista las conversaciones del proyecto (created_at, title, modo) y permite cambiar de activa. `apps/admin-panel/app/admin/projects/[id]/chat/page.tsx`
- [ ] **UI: "Nueva conversación"** — botón visible SIEMPRE (hoy solo si la lista está vacía).
- [ ] **UI: "Eliminar conversación"** — botón destructivo con `ConfirmDialog` → `DELETE /conversations/{id}` → recargar siguiente.
- [ ] **Tests** — integración del DELETE de mensaje; e2e `conversation-navigation.spec.ts` (crear A, crear B, listar, cambiar, borrar A, B sigue).

---

## Feature 3 — Borrar chat limpia DB + Redis · S · ◑ PARCIAL

**Estado.** Para conversaciones YA está hecho (commit `9df29b4`:
`delete_conversation_stream` se llama en `delete_conversation` y `clear_messages`).
El hueco son los otros streams (`doc:{id}` de documentos) que pueden dejar
huérfanos en Redis.

- [ ] **`delete_document_stream(redis, document_id)`** en `events.py` (mismo patrón que el de conversación).
- [ ] **Llamarlo** en `knowledge_bases.py::delete_document` tras el soft-delete.
- [ ] **Verificar** que el DELETE de conversación además hard-borra sus mensajes (hoy quedan bajo una conversación soft-deleted).
- [ ] **Tests** — `test_delete_document_also_deletes_redis_stream`.

---

## Feature 1 — Córtex cerebral / memoria cognitiva · XL · 🔒 GATED (solo F0)

**Estado.** Diseño completo (`docs/roadmap/cortex-system-owner.md`, ADRs
0074-0078 en `proposed`), **cero código**. Fases F0-F5.

**Decisión.** Implementar **solo F0** (el cimiento de rol, SIN tablas BYPASSRLS):

- [ ] **ADR 0074 → `accepted`** acotado a F0 (rol `system_owner`).
- [ ] **Migración** `users.is_system_owner` (Boolean NOT NULL default false, UNIQUE parcial = singleton).
- [ ] **JWT** claim `own` (encode/decode) + `AuthPrincipal.is_system_owner` + `require_system_owner` + `require_admin_or_owner`.
- [ ] **Bootstrap** del primer usuario como owner + `/me` expone `is_system_owner`.
- [ ] **Guardrail SSO** — `is_system_owner` no concedible por grupo IdP.
- [ ] **Tests** — `tests/integration/test_cortex_f0_ownership.py` (singleton, 403 cross-user, claim).

**F1-F5 (memoria cognitiva asociativa, motor afectivo PAD, identidad, bucles de
reflexión/curiosidad, voz/avatar) quedan PENDIENTES de luz verde**: introducen
tablas BYPASSRLS (excepción consciente al Principio 1 RLS), egress y Celery beats
autónomos con coste, y requieren copy honesto sobre la simulación afectiva.
