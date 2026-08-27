---
title: "Mejoras 2026-06: coste por modelo, historial de chat, limpieza de streams y córtex"
status: pending_human_validation
started_at: 2026-06-23
blocking_plan: null
owner: operator
summary: >
  Cuatro encargos del operador agrupados en un plan táctico (no es una fase del
  roadmap maestro): (4) el coste del plan se calcula por el modelo del agente
  asignado, no gpt-4o; (2) historial de conversaciones con creación/listado/
  borrado; (3) borrar un chat limpia DB + Redis sin huérfanos; (1) córtex /
  memoria cognitiva — el alcance de ESTE plan es F0 (rol system_owner). F1-F5
  estaban gated cuando se escribió esto; el operador las autorizó el
  2026-06-23 y se entregaron fuera de este plan (ver cortex-fases.md).
---

# Mejoras 2026-06 — coste, chat e inicio del córtex

Plan táctico nacido de cuatro peticiones del operador (2026-06-23). Mapa de
código levantado con un workflow de investigación (4 agentes `Explore`). Cada
feature lleva su tamaño, riesgo y estado real.

> Orden de ejecución: **#4 → #2/#3 → #1 (solo F0)**. El córtex completo es XL +
> security-critical (tablas BYPASSRLS, bucles autónomos de coste/egress) y, cuando se escribió
> este plan, quedaba a la espera de luz verde explícita salvo su cimiento F0. **El operador dio
> esa luz verde el mismo 2026-06-23** y F1-F5 se implementaron entre el 2026-06-24 y el
> 2026-07-06, ya **fuera del alcance de este plan** — su rastro vive en
> [cortex-fases.md](cortex-fases.md) y en las cinco entradas de changelog por fase.

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

## Feature 3 — Borrar chat limpia DB + Redis · S · ✅ HECHO

**Estado.** Para conversaciones YA está hecho (commit `9df29b4`:
`delete_conversation_stream` se llama en `delete_conversation` y `clear_messages`).
El hueco son los otros streams (`doc:{id}` de documentos) que pueden dejar
huérfanos en Redis.

- [x] **`delete_document_stream(redis, document_id)`** en `events.py` (mismo patrón que el de conversación).
- [x] **Llamarlo** en `knowledge_bases.py::delete_document` tras el soft-delete.
- [x] **Verificar** que el DELETE de conversación además hard-borra sus mensajes — sí lo hace
      (`delete(Message).where(...)`); la conversación queda soft-deleted como marca de auditoría.
- [x] **Tests** — `tests/unit/test_redis_stream_cleanup.py` (13).

**Cierre (2026-07-26).** Las tres primeras **ya estaban escritas** y sin marcar; lo que faltaba
de verdad era la cuarta, y no era un detalle: sin test, la llamada de limpieza podía
desaparecer en un refactor sin que nadie se enterara — el mismo patrón de «mecanismo entregado,
cero red debajo» que persigue la remediación de 2026-07-25.

Se cubren los dos contratos, y el que más importa es el negativo: la limpieza es _best-effort_
y **un Redis caído no puede tumbar el borrado del usuario**. Perder un stream huérfano es un
incordio; perder el borrado es perder una orden explícita.

**Hallazgo del inventario**: el test que enumera las familias de stream —puesto para que una
nueva no pase inadvertida— encontró a la primera dos que no estaban en el plan.
`cortex:telemetry:{owner}` está acotado (una por owner, no crece con el uso), pero
`exec:{id}` **no tenía ni limpieza ni TTL**: una clave en Redis por cada run y para siempre.
`maxlen` acota lo que pesa cada stream, no cuántos hay. No existe una operación «borrar
ejecución» de la que colgar la limpieza —son registros inmutables—, así que se le pone un TTL
deslizante de 7 días, renovado en la misma ida y vuelta que el `xadd`. Es seguro porque el
stream es solo el canal EN VIVO: el histórico que pinta el visor sale de
`executions.steps_log`, en PostgreSQL.

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

## Feature 6 — Rol `plan_approver` (project_approval) · M · ✅ HECHO (Opción A, ADR 0079 accepted)

**Estado.** ADR 0079 `accepted` — el operador eligió la **Opción A** (rol `plan_approver`
a nivel de tenant). Desacopla "aprobar planes" de "administrar el tenant".

- [x] **ADR 0079** — análisis del modelo de roles + 4 opciones + recomendación + defaults MVP. `docs/05-architecture-decisions/0079-...md`
- [x] **Enum** — `UserRole.PLAN_APPROVER = "plan_approver"` (no necesita migración: `role` es `String(32)` sin CHECK). `apps/api-server/src/api_server/db/models.py`
- [x] **Dependencia** — `require_can_approve_plan` (acepta `tenant_admin` ∪ `plan_approver`; system_admin pasa), separada de `require_tenant_admin`. `apps/api-server/src/api_server/auth/deps.py`
- [x] **Wire** — `POST /plans/{id}/approve` usa `require_can_approve_plan`. La doble firma admite firmantes mixtos. `apps/api-server/src/api_server/routers/plans.py`
- [x] **UI** — `plan_approver` ("Aprobador de planes") en el selector de rol de membresías. `apps/admin-panel/app/admin/users/page.tsx`
- [x] **Tests** — plan_approver aprueba (200), tenant_user no (403), firma única sigue OK.

---

## Feature 1 — Córtex cerebral / memoria cognitiva · XL · ✅ F0 HECHO (y F1-F5 después, fuera de este plan)

> **Corregido el 2026-07-30.** Esta sección decía que el córtex tenía **«cero código»**, que los
> ADR 0074-0078 estaban `proposed` y que **«F1-F5 … NO implementadas — gated por fase»**. Las tres
> cosas eran ciertas el 2026-06-23, cuando se escribió el plan, y **hoy son falsas**: el operador
> dio luz verde a F1→F5 ese mismo día, las cinco fases se implementaron entre el 2026-06-24 y el
> 2026-07-06 y están desplegadas, y los ADR 0075-0078 (más 0073 y 0080) están `accepted` — el 0074
> también, tras normalizarse el 2026-08-27 el `accepted-f0` que registraba su aprobación en dos
> tiempos. Es el modo de fallo de
> [`verificar-antes-de-implementar.md`](../03-guides/verificar-antes-de-implementar.md) §1: un plan
> «pendiente» que miente porque nadie lo actualizó cuando el trabajo siguió por otro sitio.

**Estado.** El **alcance de ESTE plan táctico es sólo F0** (el cimiento de rol, sin tablas
BYPASSRLS), y está entregado — las casillas de abajo son las de F0 y ya estaban en `[x]`. Lo que
vino después **no pertenece a este plan**: F1-F5 tienen su propio plan y su propio changelog, con
sus divergencias y sus casillas abiertas declaradas.

- Índice de las seis fases: [cortex-fases.md](cortex-fases.md).
- Diseño maestro: [cortex-system-owner.md](cortex-system-owner.md).
- Entregado por fase:
  [F1](../07-changelog/cortex-f1-memoria-cognitiva.md) ·
  [F2](../07-changelog/cortex-f2-afectivo.md) ·
  [F3](../07-changelog/cortex-f3-identidad.md) ·
  [F4](../07-changelog/cortex-f4-autonomia.md) ·
  [F5](../07-changelog/cortex-f5-voz-avatar.md).
- Huecos que siguen abiertos en F2-F5, casilla a casilla:
  [gaps-cortex-2026-07-27.md](gaps-cortex-2026-07-27.md).

**Decisión.** F0 (cimiento de rol, SIN tablas BYPASSRLS) **APROBADO + HECHO** por el operador.

- [x] **ADR 0074 → `accepted`** (F0 aprobado. Se marcó `accepted-f0` para registrar la aprobación en dos tiempos y se normalizó a `accepted` el 2026-08-27, quedando esa traza en el banner del ADR. La coletilla original decía «F1-F5 siguen `proposed`/gated»; el operador levantó ese gate el 2026-06-23 y el banner del ADR se corrigió el 2026-07-30).
- [x] **Migración 0091** `users.is_system_owner` (Boolean NOT NULL default false, UNIQUE parcial = singleton). Reversible.
- [x] **JWT** claim `own` (encode/decode) + `AuthPrincipal.is_system_owner` + `require_system_owner` (**DB-authoritative**, no solo el claim). También se entregó entonces la compuesta `require_admin_or_owner`, **retirada el 2026-07-30** por vivir con cero llamantes en una superficie de autorización; no reponerla sin endpoint (ver punto 3 del [ADR 0074](../05-architecture-decisions/0074-rol-system-owner-y-cortex-singleton.md), y la guarda `tests/unit/test_no_dead_authorization_gates.py`).
- [x] **Bootstrap** del primer usuario como owner + `/me` (ambos: `/auth/me` y `/me` rico) expone `is_system_owner` + hook `use-current-user` (`isSystemOwner`).
- [x] **Guardrail SSO** — estructural: las vías SSO/MFA de minteo no fijan `is_system_owner` (default false) y el gate consulta la BD; SSO nunca concede ownership.
- [x] **Tests** — `tests/integration/test_cortex_f0_ownership.py` (bootstrap, singleton, gate DB-authoritative rechaza hint forjado, /me). 3 en verde.

### Qué pasó con F1-F5 (lo que este plan dejaba «pendiente de luz verde»)

Este plan cerraba diciendo que F1-F5 «quedan PENDIENTES de luz verde» porque introducen tablas
BYPASSRLS (excepción consciente al Principio 1), egress y Celery beats autónomos con coste, y
exigen copy honesto sobre la simulación afectiva. **Todo eso sigue siendo verdad de la naturaleza
del trabajo; lo que ya no es verdad es que esté pendiente.** El operador dio la luz verde el
2026-06-23 y las cinco fases se entregaron entre el 2026-06-24 y el 2026-07-06.

Cómo se resolvieron las tres preocupaciones que este plan levantaba, verificado el 2026-07-30:

- **Tablas BYPASSRLS**: `cortex_conversations`/`cortex_turns` (0092), `cortex_affect_snapshots`
  (0093), `cortex_identity`/`cortex_identity_history` (0094) y `cortex_curiosity_pursuits` (0095)
  son tenant-less, con `owner_user_id` explícito en todo SQL y test cross-owner por tabla, tal
  como el ADR 0074 exige.
- **Egress**: salió por el camino degradado del ADR 0076 (tool web propia con `ssrf_guard` y
  kill-switch `cortex.web_enabled`, ADR 0067), no por las WebSearch nativas de `claude_sdk`.
  Divergencia deliberada y registrada.
- **Beats autónomos con coste**: existen los tres (`cortex_reflection`, `cortex_curiosity`,
  `cortex_maintenance`) detrás del kill-switch `cortex.autonomy_enabled`, que **sigue OFF**.
  **Corregido el 2026-08-19 contra el código:** esta viñeta añadía «no debería encenderse antes de
  cerrar los huecos de gobierno de F4 (sin owner-approval gate ni tope de USD cableado al bucle)»,
  y esos dos huecos están cerrados desde julio — `workers/cortex_curiosity.py` reserva y liquida
  presupuesto en dólares (`check_and_reserve(usd_cap=…)`:281, `record_spend(cost_usd=…)`:372) y
  retiene el pursuit en `selected` con `approved IS NULL` hasta que el owner lo aprueba (:303,
  :324). El enlace a [gaps-cortex-2026-07-27.md](gaps-cortex-2026-07-27.md) se conserva porque
  es la auditoría que los detectó, pero es un documento **fechado**: describe el 27 de julio.
  El kill-switch sigue OFF por **decisión del operador**, no por gobierno pendiente; encenderlo es
  una decisión suya, no un desbloqueo técnico.
- **Copy honesto**: presente en el Panel de Mente y en la videollamada, pero **sólo en castellano**
  en varias superficies, aunque la API ya devuelve `note_es`/`note_en`. Es una de las casillas que
  siguen abiertas.
