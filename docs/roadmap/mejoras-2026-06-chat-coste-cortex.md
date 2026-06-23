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

## Feature 2 — Historial de conversaciones · M · ✅ HECHO

**Estado.** El backend ya soportaba N conversaciones por proyecto + soft-delete
(`deleted_at`) + `DELETE /conversations/{id}` + `GET /projects/{id}/conversations`.
El trabajo era de UI: el operador quedaba atrapado en la última conversación, sin
forma de ver el histórico, cambiar de conversación, ni borrar una.

- [x] **UI: selector/histórico** — dropdown que lista las conversaciones del proyecto (etiqueta por título o fecha + modo) y permite cambiar de activa. `apps/admin-panel/app/admin/projects/[id]/chat/page.tsx`
- [x] **UI: "Nueva conversación"** — botón visible SIEMPRE en la barra de historial (antes solo aparecía con la lista vacía).
- [x] **UI: "Eliminar conversación"** — botón destructivo con `ConfirmDialog` → `DELETE /conversations/{id}` → salta a la más reciente restante (o estado vacío).
- [x] **Helper puro testeado** — `lib/conversation-history.ts` (`nextActiveAfterDelete`, `conversationLabel`) con 6 tests vitest.
- [~] **Borrado de mensaje individual** — NO implementado (no lo pediste; YAGNI). El borrado opera a nivel de conversación (eliminar) o "Vaciar chat" (todos los mensajes).

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

## Feature 5 — Ciclo de vida del plan + gating de sync-to-kanban · M · ✅ HECHO

**Problema.** Un plan en **borrador** podía materializar tareas al Kanban
(`sync_plan_kanban` no comprobaba el estado), y no había acción explícita para
pasar de **aprobado** a **en curso**. La máquina de estados (`plan_state_machine.py`)
ya existía (draft→pending_approval→approved→in_progress→…) pero no se cumplía en el
endpoint de sync.

- [x] **Guard en `sync-to-kanban`** — 409 `plan_not_approved` salvo que el estado sea `approved`/`in_progress`. Un borrador ya no puede materializar tareas. `apps/api-server/src/api_server/routers/plans.py`
- [x] **Endpoint `POST /plans/{id}/start-execution`** — transición `approved → in_progress` (vía state machine; 409 si no está aprobado) + materializa las tareas que falten (idempotente). Cumple "revisar si están en Kanban y si no, crearlas". `plans.py`
- [x] **UI: barra de ciclo de vida** — `PlanLifecycleSection`: "Enviar a aprobación" (draft), "Aprobar plan" (pending_approval), "Empezar ejecución" (approved). `apps/admin-panel/app/admin/projects/[id]/plans/[planId]/page.tsx`
- [x] **UI: gating de sync** — el botón "Sincronizar al Kanban" se deshabilita y avisa salvo que el plan esté aprobado/en curso.
- [x] **Tests** — 3 integración nuevas (draft no sincroniza → 409; aprobado sincroniza + start-execution crea tareas; start-execution en draft → 409) + se actualizaron los 3 tests de sync/dag existentes para aprobar antes de sincronizar. **19 en verde.**

---

## Feature 6 — Rol `project_approval` + modelo de roles del tenant · 🔒 GATED (ADR)

**Estado.** Análisis hecho (workflow). Los roles del tenant son un **enum** cerrado
(`tenant_admin`/`tenant_user`/`system_operator`), **sin Casbin**; aprobar plan está
cableado a `tenant_admin`. Añadir `project_approval` es viable pero tiene decisiones de
diseño abiertas (granularidad, relación con la doble firma, enum vs Casbin, SSO).

- [x] **ADR 0079** — documenta el modelo actual, 4 opciones (enum / booleano / tabla por-proyecto / Casbin), recomienda la **Opción A** (rol `plan_approver` tenant-wide + `require_can_approve_plan`) y lista las **preguntas abiertas para el operador**. `docs/05-architecture-decisions/0079-rol-aprobacion-de-planes-project-approval.md`
- [ ] **Implementación** — PENDIENTE de que el operador responda las 6 preguntas abiertas del ADR. Como pediste, no se toca código de roles sin ese análisis cerrado.

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
