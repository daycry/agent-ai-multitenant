---
plan_id: ciclo-vida-planes-fixes
title: Ciclo de vida de planes y tareas — máquina de estados autoritativa, tenancy del orquestador y durabilidad del planning
status: completed
blocking_plan: []
started_at: 2026-07-04
completed_at: 2026-07-08
estimated_duration_calendar: 4-5 días
estimated_effort_person_days: 4
estimated_cost_human_eur: 1.600 € – 2.400 €
estimated_cost_ai_eur: 25 € – 45 €
created_by: auditoría-plataforma-2026-07-03
spec_sections_referenced: []
docs_language: es
---

# Plan ciclo-vida-planes-fixes — que el estado de planes y tareas solo cambie por la puerta correcta

> **Origen:** auditoría de plataforma 2026-07-03, causas raíz **B (mutación de estado fuera de la máquina de
> estados)** y **C (tenancy/durabilidad del orquestador)**. Once hallazgos c1-c11 verificados
> adversarialmente en Opus 4.8. La verificación **reencuadró varios**: c2, c5, c7, c8 y c10 salieron
> `matizado` — el hecho literal es cierto pero la severidad/marco del claim cambia. Este plan incorpora esos
> matices: **no** «arregla» comportamientos que resultaron ser diseño aceptado.

## Cabecera

| Campo           | Valor                                                       |
| --------------- | ----------------------------------------------------------- |
| **ID del Plan** | `ciclo-vida-planes-fixes`                                   |
| **Rama git**    | `plan/runs-visor-trabajo` (rama en curso)                   |
| **Causa raíz**  | B (mutación fuera de la state machine) + C (tenancy/durab.) |

## Problema (con evidencia verificada)

| Id      | Veredicto  | Defecto                                                                                                                                                                                                                                                                                                                  | Reencuadre tras verificación                                                                                                                                                                                                                                                                   |
| ------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **c1**  | confirmado | `PUT /tasks` no consulta la máquina de estados: solo valida DAG y setea `status` crudo (`tasks.py:345-372`). Un drag&drop `backlog→done` (o `done→in_progress`) pasa sin validar la transición.                                                                                                                          | Bug real de integridad de workflow intra-tenant. El trigger `trg_compute_task_ready` **amplifica** (un falso `done` promueve dependientes). Ya flagueado en `auditoria-zonas-2026-06.md:85`.                                                                                                   |
| **c2**  | matizado   | `submit_verdict` asigna `plan.status` en crudo (`review.py:470`) sin `transition_plan_status`.                                                                                                                                                                                                                           | **La tesis del claim cae**: completar el plan **antes** de encolar el PR es el diseño **aceptado** de ADR 0072 fase 2 (gate humano = cierre; auto-PR downstream best-effort); no hay `approved_at`/`completed_at` que sellar. Residuo real: **solo higiene** (encaminar por la state machine). |
| **c3**  | confirmado | Una tarea `blocked` estanca el plan en `in_progress` sin ruta automática de salida: `_OPEN_TASK_STATUSES` cuenta `blocked` como abierta (`plan_progress.py:55`), `apply_reviewer_verdict` deja `blocked` al agotar reintentos (`reviewer_bridge.py:180`) y no existe ruta automática plan→blocked ni de auto-resolución. | Verificado: sin intervención humana el plan no cierra ni avanza (queda `in_progress` indefinidamente).                                                                                                                                                                                         |
| **c5**  | matizado   | `_revert_to_ready` (`dispatch.py:762-766`) consulta `select(Task).where(Task.id==task_id)` **sin `tenant_id`** en sesión BYPASSRLS, violando la regla dura #1 de CLAUDE.md.                                                                                                                                              | **NO es P0**: `Task.id` es PRIMARY KEY → el lookup solo puede filtrar, nunca redirigir a otro tenant; el `task_id` viene del mismo evento interno. Es **hardening/consistencia**, no fuga explotable. (Además `_dispatch:823` también carece del predicado.)                                   |
| **c6**  | confirmado | Los planes de chat nacen **sin `phases[]`** (`responder.py:598`, `planning_llm.py:478`) → el scope `phase` del sync es inservible y `PhasesSection` no renderiza.                                                                                                                                                        | Degradación **benigna**: la UI deshabilita el radio `phase`; `total`/`selection` materializan igualmente las tareas.                                                                                                                                                                           |
| **c7**  | matizado   | Un rol desconocido/sin agente deja `assigned=None` **sin log ni warning** (`sync_to_kanban.py:237-246`); ni create ni approve validan roles.                                                                                                                                                                             | El fallback a NULL→política de dispatch es **decisión aceptada** de ADR 0091 D1. Residuo real: **solo observabilidad** (falta warning ante un typo de rol) + validación temprana. Bajo.                                                                                                        |
| **c8**  | matizado   | El board gerencial `/admin/board` pinta **proyectos** como planes (`board/page.tsx:137`).                                                                                                                                                                                                                                | ADR 0008 **autorizó explícitamente** ese placeholder («cada tarjeta es un proyecto/plan») y difirió la tabla real a «Plan 02». Hoy la tabla `plans` ya existe con datos → el interino quedó **obsoleto** (spec-drift), no viola el ADR.                                                        |
| **c9**  | confirmado | El turno de respuesta del equipo corre como `asyncio.create_task` **detached** en el api-server (`responder.py:854-865`), sin Celery, cola durable, reintento ni recuperación al arranque.                                                                                                                               | Un reinicio a mitad de turno **pierde la respuesta** (el mensaje del usuario sí es durable). Aplica a los 3 modos de chat, no solo planning.                                                                                                                                                   |
| **c10** | matizado   | `PlanStatus` tiene dos definiciones divergentes: StrEnum de dominio (`domain.py`, con `draft`/`pending_second_approval`) vs `Literal` de `plan_progress.py:28-38` que los omite.                                                                                                                                         | **Sin bug runtime**: los `Literal` no se validan en runtime, la columna es `String(32)` libre, y la función trata cualquier status ≠`in_progress` como no-op seguro. Es **higiene de tipos**.                                                                                                  |
| **c11** | confirmado | `estimated_complexity` de tareas de planes de chat siempre queda en `'m'`: `pm_plan_draft` no emite `complexity` (`planning_llm.py:393`); `sync_to_kanban` y `compute_ai_cost` caen al default.                                                                                                                          | El desglose de coste pondera todo por igual. `cost.py:461` lee `complexity` del spec, no del `Task` materializado — misma raíz, dos consumidores.                                                                                                                                              |

## Alcance

**Entra:** encaminar toda mutación de estado por la máquina de estados (c1, c2-higiene, c10 fundación),
propagar `blocked` al plan (c3, sujeto a veredicto), cerrar el hueco de tenancy del orquestador con guard-test
(c5), durabilidad del turno de planning (c9), y las mejoras de fidelidad del planner (c6/c7/c11) + migración
del board (c8). Todo con test automático.

**Queda fuera:** nada gated a ADR en este plan; c8 no necesita ADR (ADR 0008 `accepted` ya pide el kanban de
planes). El re-diseño del gate de guardrails va en `tools-y-cierre-plan-fixes.md` (causa D), no aquí.

## Decisiones clave

- **`transition_task_status` / `transition_plan_status` son la ÚNICA puerta** de cambio de estado. Todo camino
  REST/worker las usa; un guard-test estático falla el CI si aparece `setattr`/asignación cruda de `.status`
  fuera de ellas.
- **c2 es higiene, no cambio de comportamiento**: el orden completar→encolar-PR se **preserva** (ADR 0072).
- **c5 se corrige por consistencia con la regla #1**, no como fix de seguridad P0 (se documenta el matiz).
- **c10 primero** (fundación): unificar `PlanStatus` antes de tocar los caminos que lo consumen.

## Tareas

> **Estado (2026-07-05, rama `plan/runs-visor-trabajo`):** HECHAS y verificadas
> **T3** (c2 submit_verdict por state machine), **T5** (c5
> tenant_id dispatch), **T8** (c6 phases), **T9** (c7 warning de rol), **T10** (c11
> complexity), **T6** (c9 durabilidad del turno — idempotencia + sweep de arranque)
> — en la remediación fase 1/2 (ver changelogs). PENDIENTES: **T1** (c10 PlanStatus —
> `plan_progress.py` sigue con `Literal` divergente, ver nota inline; esta línea la
> daba por hecha erróneamente hasta la corrección de 2026-07-06), **T2** (c1 PUT→state
> machine — transversal + decisión de producto sobre estrictez del Kanban), **T4**
> (guard-test estático — requiere T2 primero), **T7** (c3 — el escalado plan→blocked
> YA está en dispatch; falta notificación + acción humana de desbloqueo), **T11** (c8
> board gerencial por plan_id — frontend).
>
> **Corrección (2026-07-06, auditoría de roadmap)**: el `status` del frontmatter estaba en
> `pending_approval` pese a 6 de 11 tareas hechas — corregido a `in_progress`. Los checkboxes de
> T5/T8/T9/T10 no reflejaban esta nota (seguían en `[ ]`); ya se marcaron `[x]` con evidencia. T1
> se re-verificó y **NO** está hecha (ver nota inline) — se corrige también esta línea de estado.
>
> **Reconciliación (2026-07-08, tests corridos)**: **T1, T2 y T7 se marcan `[x]`** con evidencia
> y tests verdes hoy (ver notas inline de cada una: consistency-pin de PlanStatus, PUT→409 con
> force de admin, human-action retry + plan unblock validados además en vivo en el QA e2e).
> **Quedan genuinamente pendientes: T4** (guard-test estático de mutación de estado — no existe;
> tiene sentido hacerla ahora que T2 cerró la última puerta lateral) **y T11** (board por planes:
> implementada y en uso, falta SOLO su test vitest — ver nota inline). 9 de 11 tareas cerradas.
>
> **CERRADO (2026-07-08, misma tanda)**: T4 entregada (`f123489` — guard AST con allowlist de
> igualdad + el sweep de expiry encaminado por `transition_plan_status`) y T11 con su test
> (`e1ff76c`). **11/11 tareas `[x]`** y los 5 criterios de cierre cumplidos (guard-tests T4+T5
> activos, plan no puede quedar atascado — T7 + `transition_from_blocked` H2 —, turno de
> planning durable T6, board por planes T11). El merge de la rama a master queda como decisión
> del operador (mismo estado que el resto de la rama `plan/runs-visor-trabajo`).

### Fase A — Fundación de tipos y máquina de estados

- [x] **T1 — Unificar `PlanStatus` (c10)**: una sola definición canónica (StrEnum de dominio) importada por
      `plan_progress.py` en vez del `Literal` divergente; o, si el `Literal` debe restringir, documentar y
      validar explícitamente en frontera. **Test:** no existen dos vocabularios de `PlanStatus`; mypy verde;
      pasar `draft` a `transition_to_pending_human_validation` sigue siendo no-op seguro. > **Nota (2026-07-06, auditoría de roadmap)**: la línea "Estado (2026-07-05)" de arriba lista T1 > como hecha, pero **no lo está**: `apps/api-server/src/api_server/plan_progress.py:31` sigue > definiendo su propio `Literal[...]` (comentado "mirrors it EXACTLY" en vez de importar > `api_server.db.domain.PlanStatus`). No se marca `[x]`.
  > **Reconciliado (2026-07-08)**: cumplida por la **segunda rama del propio item** ("documentar
  > y validar explícitamente en frontera"): el `Literal` de `plan_progress.py` ya NO diverge
  > (espejo exacto del StrEnum, incluye `draft`/`pending_second_approval`), está documentado
  > (el módulo es puro y no puede importar el dominio cargado de SQLAlchemy) y
  > `tests/unit/test_plan_status_consistency.py` **pinea los dos conjuntos iguales** (verde
  > hoy) — un solo vocabulario, imposible de driftear en silencio. mypy verde (gate mypy-total
  > `db1c5d0`).
- [x] **T2 — `PUT /tasks` vía máquina de estados (c1)**: `update_task` encamina el cambio de `status` por
      `transition_task_status` (mantiene la validación DAG existente); transición ilegal → 409/422 con mensaje.
      **Test:** `backlog→done` y `done→in_progress` por PUT devuelven error; `backlog→ready` pasa; el
      drag&drop del Kanban respeta las columnas legales.
  > **Reconciliado (2026-07-08)**: implementada — `routers/tasks.py:375` valida
  > `allowed_transitions(old_status)` → 409 (distinto del 422 de DAG) con override explícito
  > `?force=` solo para `tenant_admin`. Test dedicado
  > `test_dag_enforcement.py::test_illegal_transition_is_409_and_tenant_admin_can_force`
  > (módulo completo verde hoy, 7 passed).
- [x] **T3 — `submit_verdict` vía `transition_plan_status` (c2, higiene)**: `submit_verdict` (`review.py:469`)
      encamina el cierre `pending_human_validation→completed|rejected` por `transition_plan_status` (la única
      puerta) en vez de asignar `.status` en crudo. La transición es legal (línea 17 del state machine) → mismo
      comportamiento; se preserva el orden completar→encolar-PR (ADR 0072). **Test:** regresión verde
      (`test_plan_completion` + `test_review_execution_applies_verdict`, 15 casos) — misma transición y mismo
      encolado del auto-PR. _(El guard-test estático anti-asignación-cruda es T4, aparte.)_
- [x] **T4 — Guard-test estático de mutación de estado**: CI falla si aparece asignación directa de
      `.status`/`setattr(..., 'status', ...)` sobre `Task`/`Plan` fuera de las funciones de transición
      (incluye `maintenance.py:126`, señalado en la verificación como el mismo patrón). **Test:** el linter/test
      detecta un caso sembrado.
  > **HECHA (2026-07-08, `f123489`)**: `tests/unit/test_state_mutation_guard.py` — escáner AST
  > sobre los 3 árboles con allowlist de igualdad justificada + auto-test de caso sembrado. El
  > sitio del sweep de expiry (heredero del `maintenance.py:126` original, hoy
  > `maintenance/review_runtimes.py`) quedó encaminado por `transition_plan_status` con el edge
  > `pending_human_validation→blocked` declarado en la tabla canónica. Verde.

### Fase B — Tenancy y durabilidad del orquestador

- [x] **T5 — `tenant_id` en `_revert_to_ready` (c5, hardening)**: añadir el predicado de tenant a las consultas
      por id del orquestador en sesión BYPASSRLS (`_revert_to_ready:762`, `_dispatch:823`), por consistencia con
      la regla dura #1; documentar en el código que es defense-in-depth (no fuga explotable, lookup por PK).
      **Test:** guard-test estático que exige `tenant_id` en todo `select(Task/Plan).where(...id...)` del
      orquestador; regresión de que el revert sigue funcionando. > **Verificado (2026-07-06, auditoría de roadmap)**: `dispatch.py` filtra `Task.tenant_id` en ambos > sitios; existe `tests/unit/test_orchestrator_tenant_scoping.py`.
- [x] **T6 — Turno de planning durable (c9)**: en vez de migrar el responder a Celery (cambio arquitectónico
      grande: el responder vive en api-server con los proveedores LLM), se resuelve la durabilidad con
      **idempotencia + sweep de recuperación al arranque** en api-server: (1) `respond_to_conversation` tiene una
      guarda que hace SKIP si el último mensaje ya es respuesta (agent/system) → seguro llamarlo repetido;
      (2) `resume_pending_replies` (nuevo) reanuda al arranque las conversaciones cuyo último mensaje es de
      usuario y está estancado (> 30s, ni frescas ni respondidas), con lock redis single-flight entre workers;
      (3) hook `@app.on_event("startup")` en `create_app`, best-effort. Aplica a los 3 modos de chat. **Test**
      (`tests/integration/test_chat_resume_pending.py`, 2 casos): solo la conversación estancada-sin-responder se
      reanuda (no las frescas ni las respondidas); el lock concede una sola vez. 19 tests de chat sin regresión.

### Fase C — Propagación de `blocked` (c3)

- [x] **T7 — Ruta de salida de tarea `blocked` + propagación al plan (c3, confirmado)** — **3 de 3 partes
      hechas**: (a) el escalado plan `in_progress→blocked` cuando las únicas tareas abiertas son `blocked` ya está
      (fase 1, `dispatch.py:_on_task_done`); (b) **notificación al operador HECHA** — evento `plan_blocked` en el
      registro del notification-dispatcher + plantillas es/en + el orquestador lo encola tras el escalado
      (`_send_plan_blocked_notification`, fuera de la txn, best-effort; restructure `if/else` behavior-preserving).
      **Test** (`tests/unit/test_plan_blocked_notification.py`, 2 casos + 28 de registro/plantilla + 5 de dispatch
      sin regresión). (c) la **acción humana de desbloqueo/reintento HECHA**:
      `POST /tasks/{id}/human-action` con `action=retry` (`routers/task_lifecycle.py:175` — tarea →
      `ready`/`backlog` + reset del presupuesto de reintentos + **reactivación del plan**
      `blocked→in_progress`) y `POST /plans/{id}/unblock` (`routers/plans.py:303`, reactiva y re-encola);
      UI en `/admin/plans/{id}/escalated` (ambas acciones, usadas en vivo por el operador en el QA e2e
      2026-07-07/08). **Test** (`tests/integration/test_task_retry_human_action.py`, 2 casos, verde hoy).
  > **Fricción residual de UX** (no de lógica): visibilidad del botón de desbloqueo fuera de la
  > página escalated → hallazgos #2/#3 de `hallazgos-pendientes-2026-07-07.md`.

### Fase D — Fidelidad del planner y board (c6, c7, c8, c11)

- [x] **T8 — `phases[]` en planes de chat (c6)**: `pm_plan_draft` emite `phases` (o se documenta y bloquea con
      gracia el scope `phase` para planes de chat, que ya degrada bien). Recomendación: emitir `phases` para
      habilitar el sync por fases. **Test:** un plan de chat trae `phases` no vacío y el scope `phase` del sync
      funciona; si se opta por no soportarlo, el radio queda deshabilitado con tooltip explicativo. > **Verificado (2026-07-06, auditoría de roadmap)**: `chat/planning_llm.py:394-496` emite `phases`.
- [x] **T9 — Warning de rol desconocido (c7)**: `_resolve_assignment` loguea un warning cuando un rol no
      resuelve a agente (typo o rol sin agente en el equipo); validación temprana opcional en create/approve.
      **Test:** un rol inexistente en el spec produce un warning con el nombre del rol; la tarea se materializa
      igual (comportamiento ADR 0091 D1 preservado). > **Verificado (2026-07-06, auditoría de roadmap)**: `chat/sync_to_kanban.py:243` (comentario "(c7)" > explícito) loguea el warning de rol no resuelto.
- [x] **T10 — `complexity` en planes de chat (c11)**: `pm_plan_draft` emite `complexity` por tarea; el desglose
      de coste deja de ponderar todo como `'m'`. **Test:** un plan de chat con tareas de complejidad mixta
      produce un desglose de coste diferenciado. > **Verificado (2026-07-06, auditoría de roadmap)**: `chat/planning_llm.py:475-485` emite `complexity`.
- [x] **T11 — Board gerencial por `plan_id` (c8)**: `/admin/board` agrupa por planes reales
      (`/projects/{id}/plans` / tabla `plans`) en vez de proyectos; actualizar el comentario obsoleto. **Test:**
      el board muestra planes (no proyectos) como tarjetas de la fila superior; ADR 0008 satisfecho.
  > **HECHA (2026-07-08, `e1ff76c`)**: feature ya implementada (`board/page.tsx:61`, en uso en el
  > QA e2e) y ahora con su test — `app/admin/board/page.test.tsx` (render jsdom del board real
  > con API mockeada: la fila superior pinta PLANES de `GET /plans`, no proyectos). Verde
  > (vitest 189 passed).

## Criterios de cierre

1. Checkboxes en `[x]` con test automático en verde.
2. Guard-tests estáticos activos: (a) mutación de estado solo por transición (T4); (b) tenant_id en queries del
   orquestador (T5).
3. Un plan no puede quedar atascado `in_progress` sin intervención posible (T7, sujeto a veredicto c3).
4. Matar el api-server a mitad de turno de planning no pierde la respuesta (T6).
5. El board gerencial muestra planes reales (T11).
