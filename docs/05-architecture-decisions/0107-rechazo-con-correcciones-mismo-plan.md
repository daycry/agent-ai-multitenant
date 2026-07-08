---
id: 0107
title: Rechazo humano con correcciones en el mismo plan
status: accepted
date: 2026-07-08
deciders: [operador (diseño conversado y aprobado 2026-07-08), claude]
related: [hallazgo #11 (hallazgos-pendientes-2026-07-07), ADR 0062, ADR 0072, ADR 0085, ADR 0013]
---

# ADR 0107 — Rechazo humano con correcciones en el mismo plan

## Contexto

El veredicto humano `rejected` persiste `rejection_reason` en la sesión de review y
transiciona el plan a `rejected`… y ahí muere (hallazgo #11): nadie consume el motivo,
las tareas siguen `done` y el trabajo correctivo exige crear a mano un plan NUEVO en el
chat de planning — perdiendo la relación rechazo↔corrección, la rama git del plan
(ADR 0085) y la trazabilidad del PR final. Visto en vivo validando el plan CI4
(2026-07-08): el operador rechazó con un motivo accionable («acotar el filtro
Content-Type a api/v1») y no había forma de convertirlo en trabajo.

## Decisión

**Rework en el MISMO plan**, en tres piezas que reutilizan maquinaria existente:

1. **El veredicto no cambia.** `rejected` sigue siendo el destino del rechazo y pasa a
   ser el estado-aparcamiento del ciclo de correcciones. Es seguro por construcción:
   el promotor DAG y el reconciler de convergencia filtran `in_progress`, y nada
   reacciona a planes `rejected`.
2. **`POST /plans/{id}/generate-corrections`** (autenticado, miembro): un paso LLM
   (patrón de `generate-acceptance-criteria`; helper nuevo `chat/corrections_llm.py`
   que calca el contrato JSON de tareas de `pm_plan_draft`) convierte el
   `rejection_reason` de la sesión rechazada más reciente en 1-N **tareas correctivas**
   con criterios de aceptación. Se añaden a `specification.tasks` con
   `origin: "correction"` (así el sync scope=`selection` las acepta sin cambios) y se
   registra el ciclo en `specification.corrections[]`
   (`{session_id, reason, task_ids, created_at, status: proposed|accepted}`).
3. **`POST /plans/{id}/accept-corrections`** (autenticado, admin): en UNA transacción
   materializa la selección vía `sync_plan_to_kanban(scope="selection")` (las tareas
   nacen en `backlog`) **y** transiciona el plan `rejected → in_progress` (arista
   NUEVA en la state machine, espejo semántico de `blocked → in_progress`); tras el
   commit, promoción DAG + announce (patrón exacto de `start-execution`). El orden
   dentro de la transacción garantiza que el plan nunca es observable `in_progress`
   con todas las tareas `done` — sin esa garantía, el reconciler lo rebotaría a
   `pending_human_validation` y re-lanzaría una sesión de review.

La UI (detalle del plan) muestra en planes `rejected` la tarjeta **«Correcciones del
rechazo»**: el motivo (markdown), «Generar tareas correctivas», la lista de propuestas
con criterios y checkboxes, y «Aceptar correcciones». El ciclo continúa solo: dispatch
→ review IA → `pending_human_validation` → nueva sesión de review (con app-preview si
el proyecto la tiene configurada).

## Alternativas descartadas

- **Transicionar a `in_progress` en el propio veredicto** («request changes»):
  rechazada — entre el veredicto y la creación de tareas correctivas el plan estaría
  `in_progress` con todo `done`, y `_reconcile_complete_plans` (90 s) lo devolvería a
  `pending_human_validation` re-lanzando sesión de review (bucle). Además exigiría
  generación LLM dentro del endpoint firmado del verdict — sin precedente (todos los
  endpoints LLM requieren principal autenticado) y con latencia inaceptable en un POST
  síncrono firmado.
- **Plan correctivo separado** (statu quo manual): pierde la relación, la rama git y
  el PR unificado; duplica ceremonia de aprobación para arreglos de 1-2 tareas.
- **Re-abrir tareas `done` con el motivo como `prior_review_feedback`**: la infra
  existe a nivel de tarea, pero un rechazo de plan no mapea 1:1 a tareas concretas —
  la descomposición del motivo en trabajo nuevo es exactamente lo que hace bien el
  paso LLM.

## Consecuencias

- La arista `rejected → in_progress` amplía el vocabulario de la state machine
  (documentada con este ADR; el guard T4 y los tests de la máquina se actualizan).
- `rejected` deja de ser semánticamente terminal: es «rechazado, correcciones
  posibles». El rechazo terminal = no aceptar correcciones (o archivar).
- El spec del plan gana la sección `corrections` (metadatos del ciclo) — los
  renderers/consumidores del spec la ignoran salvo la tarjeta nueva (retro-compatible).
- La mutación del spec se hace SIEMPRE reemplazando el dict completo (no hay
  `flag_modified` en el código); helper compartido para no repetir el patrón.
