# Plan 16 — tests humanos

Esta guía cubre los **6 tests humanos** del Plan 16 (Human Agents y
Workflows Mixtos Humano-IA). Validan lo que los e2e Playwright escritos
pero no ejecutados no pueden cubrir: el **ciclo end-to-end de una tarea
humana** en un plan mixto, el **acceptance timeout con escalación**, el
modo **peer_human_reviewer**, la **trazabilidad auditable** (HumanWorkSessions),
las **tools de Human Workload del asistente personal**, y el **coste
humano integrado en budget**.

> **Estado del plan**: `in_progress` (override humano del gate
> `blocking_plan`: los planes 10 y 11 están en `pending_human_validation`).
> Las 16 tareas (`task_16_01`..`task_16_16`) tienen su backend pytest
> verde contra DB real + `@pytest.mark.cross_tenant`, y el admin-panel en
> typecheck/lint/build verde; los **e2e Playwright están escritos pero NO
> ejecutados** — de ahí que estos 6 tests humanos sean el último paso
> antes de pasar a `completed` (junto al PR y al cierre por el
> orquestador).

## TL;DR

No hay `setup_demo_16.py` ni launcher dedicado para este plan: los tests
necesitan usuarios reales con bandeja personal, canales de notificación,
y un plan mixto IA+humano ejecutándose contra los workers. El setup es
manual:

```powershell
.\scripts\dev\up.ps1     # api-server :8001 + admin-panel :3000 + postgres + redis + workers + celery-beat
```

Las pantallas del admin-panel implicadas:

```
http://localhost:3000/admin/human-agents                  # galería de Human Agents (tenant) + plantillas globales clonables
http://localhost:3000/admin/projects/{id}/settings        # human_task_review_mode (auto_approve / peer_human_reviewer)
http://localhost:3000/my-tasks                             # bandeja personal "Tareas asignadas a mí" + histórico
http://localhost:3000/admin/projects/{id}/consumption     # dashboard 13.7: segmenta AI cost vs Human cost
```

La guía de creación/configuración de Human Agents está en
[`docs/03-guides/human-agents.md`](../human-agents.md) y el runbook
operativo en
[`docs/06-runbooks/human-tasks-operations.md`](../../06-runbooks/human-tasks-operations.md).
El diseño está en el ADR 0046 (modelo `agent_type` ai/human).

## Pre-requisitos

| Requisito                                           | Por qué                                                               |
| --------------------------------------------------- | --------------------------------------------------------------------- |
| Stack dev arriba (`up.ps1`) + workers + celery-beat | Tareas IA usan el pool; el sweep de timeout corre en Beat cada 10 min |
| Un usuario `tenant_admin`                           | Crear Human Agents, configurar review mode y budget                   |
| Al menos **2 usuarios** reales del tenant           | Uno asignado, otro como escalation target / peer reviewer             |
| Canal de notificación de los users (Plan 10)        | `human_16_01`/`02` esperan notificación por el canal preferido        |
| Un proyecto con un plan mixto IA + humano           | `human_16_01` ejecuta 3 tareas IA + 1 humana en el mismo DAG          |
| Asistente personal habilitado (Plan 10)             | `human_16_05` prueba las dos tools de Human Workload                  |
| `Project.budget_includes_human_cost` configurable   | `human_16_06` valida budget con coste humano incluido                 |

---

## `human_16_01` — Ciclo end-to-end completo de una tarea humana en un plan mixto

**Qué prueba**: en un plan con 3 tareas IA + 1 humana (revisión legal),
las IA ejecutan en su pool normal, la humana NO pide contenedor sino que
pasa a `assigned_to_human` y notifica al user; el user la acepta, la
completa con output y horas, el sistema crea la HumanWorkSession, la
tarea pasa a `done` (auto_approve) y el DAG continúa, con el coste humano
imputado y visible segmentado en el dashboard 13.7.

**Precondiciones**:

- Un proyecto con `human_task_review_mode=auto_approve` (default).
- Un Human Agent creado en `/admin/human-agents` asignado a un User real
  (`assignment_mode=specific_user`).
- Un plan con 3 tareas IA + 1 tarea humana (revisión legal) en el mismo
  DAG.
- Canal de notificación del user configurado.

**Pasos**:

1. Lanza el plan mixto. Las **3 tareas IA** deben ejecutar en su **pool
   de runtime normal**.
2. La **tarea humana NO solicita contenedor**: pasa a
   **`assigned_to_human`** y se **notifica al user** (orchestrator crea
   `HumanTaskAssignment`).
3. El user recibe la **notificación por su canal preferido** (email /
   asistente / in-app).
4. El user **acepta la tarea** desde su bandeja `/my-tasks` → pasa a
   **`in_progress`**.
5. El user **marca completada** con **output y horas trabajadas** (modal
   de entrega con attachments).
6. El sistema crea la **HumanWorkSession** correctamente.
7. La tarea pasa a **`done`** (modo auto_approve) y el **DAG continúa**
   con las dependientes.
8. El **coste humano** se imputa al plan y se ve **segmentado** en el
   dashboard `13.7` (AI cost vs Human cost).

**Resultado esperado**: las IA corren en pool, la humana se asigna sin
contenedor y se notifica, el user la acepta y completa, se crea la
HumanWorkSession, la tarea pasa a done y el coste humano aparece
segmentado.

**Checklist**:

- [ ] Las 3 tareas IA ejecutan en su pool de runtime normal.
- [ ] La tarea humana NO solicita contenedor; pasa a `assigned_to_human`
      y se notifica al user.
- [ ] El user recibe la notificación por su canal preferido.
- [ ] El user acepta la tarea desde su bandeja personal y pasa a
      `in_progress`.
- [ ] El user marca la tarea como completada con output y horas
      trabajadas.
- [ ] El sistema crea HumanWorkSession correctamente.
- [ ] La tarea pasa a done (modo auto_approve) y el DAG continúa.
- [ ] El coste humano se imputa al plan y se ve en el dashboard 13.7
      segmentado.

**Pitfalls conocidos**:

- En MVP solo existe `assignment_mode=specific_user` (Decisión Clave):
  asigna a una **persona concreta**; `role_queue`/`team_pool` quedan
  fuera.
- La tarea humana usa **HumanWorkSession**, no Execution (`task_16_03`):
  si buscas el registro en la tabla de Executions no estará — está en
  `human_work_sessions`.
- El **coste en moneda del tenant** depende del FX; si no hay rate,
  `human_cost.py` cae al `DEFAULT_HOURLY_RATE_EUR`/USD canónico. La
  segmentación AI vs Human sí debe verse.

---

## `human_16_02` — Acceptance timeout y escalación automática

**Qué prueba**: con un Human Agent de `acceptance_timeout=1h`, asignar
una tarea y NO aceptarla la reasigna automáticamente al
`escalation_target_user_id` tras 1 h, llega notificación al escalation
target, y si este tampoco acepta en otra hora la tarea pasa a `blocked` y
se notifica al admin.

**Precondiciones**:

- Un Human Agent con `acceptance_timeout_hours=1` y un
  `escalation_target_user_id` configurado.
- Celery Beat arriba (el sweep corre cada 10 min, `task_16_06`).
- Canales de notificación del user asignado y del escalation target.

**Pasos**:

1. Crea/edita un Human Agent con **`acceptance_timeout=1h`** y un
   **escalation target** distinto del asignado.
2. **Asigna una tarea** al user y **NO la aceptes**.
3. Espera a que pase **1 h** (el sweep de Beat corre cada 10 min): la
   tarea se **reasigna automáticamente** al `escalation_target_user_id`.
4. Comprueba que llega **notificación al escalation target**.
5. Deja que el escalation target **tampoco acepte** en otra hora: la
   tarea pasa a **`blocked`** y se **notifica al Tenant Admin**.

**Resultado esperado**: tras el timeout la tarea se reasigna al
escalation target con notificación, y si este tampoco acepta pasa a
blocked y avisa al admin.

**Checklist**:

- [ ] Tras 1 h sin aceptar, la tarea se reasigna automáticamente al
      `escalation_target_user_id`.
- [ ] Llega notificación al escalation target.
- [ ] Si el escalation target tampoco acepta en otra 1 h, la tarea pasa a
      blocked y se notifica al admin.

**Pitfalls conocidos**:

- El sweep es un **job de Celery Beat cada 10 min** (`task_16_06`): la
  reasignación no es instantánea al cumplir la hora, ocurre en el
  siguiente tick del sweep. Confirma que **celery-beat está arriba**.
- Para no esperar 1 h real, puedes bajar `acceptance_timeout_hours` a un
  valor pequeño en la config del Human Agent (sigue dependiendo del tick
  de Beat).
- Sin **canal de notificación** configurado, la reasignación ocurre igual
  pero el target no se entera por chat/email — configúralo (Plan 10).

---

## `human_16_03` — Modo peer_human_reviewer

**Qué prueba**: con `human_task_review_mode=peer_human_reviewer`, tras el
submit del primer humano la tarea pasa a `in_review` y se asigna a un
segundo Human Agent; el reviewer ve el output completo y aprueba o
rechaza con comentarios; si rechaza, la tarea vuelve a backlog con los
comentarios y aplica el flujo de reintento como en tareas IA;
`retry_count` se incrementa y tras `max_review_retries` hay escalación
humana (sección 7.9).

**Precondiciones**:

- Un proyecto con `human_task_review_mode=peer_human_reviewer`.
- Dos Human Agents: uno ejecutor, otro como peer reviewer (users
  distintos).

**Pasos**:

1. Configura el proyecto con
   **`human_task_review_mode=peer_human_reviewer`**.
2. Ejecuta una tarea humana: el primer humano **hace submit** del output.
3. La tarea pasa a **`in_review`** y se **asigna al segundo Human Agent**
   (el reviewer).
4. El **reviewer ve el output completo** y puede **aprobar o rechazar con
   comentarios**.
5. **Rechaza** con comentarios: la tarea **vuelve a backlog** con los
   comentarios y aplica el **flujo de reintento** igual que en tareas IA.
6. Comprueba que **`retry_count` se incrementa** correctamente; tras
   **`max_review_retries`** hay **escalación humana** (sección 7.9 →
   blocked + notificación).

**Resultado esperado**: el submit lleva a in_review con peer reviewer
asignado; aprobar/rechazar funciona; el rechazo reintenta como IA, sube
retry_count y escala tras el máximo.

**Checklist**:

- [ ] Tras submit del primer humano, la tarea pasa a in_review y se
      asigna al segundo Human Agent.
- [ ] El reviewer ve el output completo y puede aprobar o rechazar con
      comentarios.
- [ ] Si rechaza, la tarea vuelve a backlog con los comentarios; el flujo
      de reintento aplica igual que en tareas IA.
- [ ] `retry_count` se incrementa correctamente; tras
      `max_review_retries`, escalación humana (sección 7.9).

**Pitfalls conocidos**:

- El modo `ai_reviewer` queda **fuera del MVP** (Decisión Clave): solo
  `auto_approve` y `peer_human_reviewer`. No esperes un reviewer IA aquí.
- El `peer_human_reviewer` **reutiliza el state machine §7.2 + la
  escalación §7.9** (`review.py`, migración 0073): el reintento y la
  escalación se comportan igual que en tareas IA — si difieren, repórtalo.
- El reviewer debe ser **otro Human Agent del tenant**: si solo hay uno,
  no hay a quién asignar la review.

---

## `human_16_04` — Trazabilidad auditable de la tarea humana

**Qué prueba**: tras completar el ciclo, la vista de detalle de la tarea
humana muestra las HumanWorkSessions (no Executions) con horas,
comentarios y attachments; las reviews aparecen con verdict, reviewer y
feedback; el registro auditable está completo y exportable como bundle
JSON (sección 13.6.3); y el Memorizer genera MemoryEntries a partir de
las HumanWorkSessions.

**Precondiciones**:

- Una tarea humana ya completada (de `human_16_01` o `human_16_03`), con
  HumanWorkSession y (si aplica) review.
- El Memorizer/worker de memoria arriba.

**Pasos**:

1. Abre la **vista de detalle** de la tarea humana completada.
2. Comprueba que muestra las **HumanWorkSessions** (no Executions) con
   **horas, comments y attachments**.
3. Si hubo peer review, verifica que las **reviews** aparecen con
   **verdict, reviewer_user_id y feedback_text**.
4. **Exporta el registro auditable** como **bundle JSON** (sección
   13.6.3): debe estar **completo**.
5. Comprueba que el **Memorizer** ha generado **MemoryEntries** a partir
   de las HumanWorkSessions (con cita a la sesión correcta).

**Resultado esperado**: la vista de detalle muestra HumanWorkSessions +
reviews, el bundle JSON exporta el registro completo, y el Memorizer
produce MemoryEntries citando las HumanWorkSessions.

**Checklist**:

- [ ] La vista de detalle muestra las HumanWorkSessions (no Executions)
      con horas, comments, attachments.
- [ ] Las reviews aparecen con verdict, reviewer_user_id, feedback_text.
- [ ] El registro auditable está completo y exportable como bundle JSON
      (sección 13.6.3).
- [ ] El Memorizer genera MemoryEntries a partir de las
      HumanWorkSessions.

**Pitfalls conocidos**:

- Las MemoryEntries de tareas humanas citan la **HumanWorkSession** vía
  `memory_entries.source_human_work_session_id` (migración 0075, CHECK
  `ck_memory_entries_single_source`: Execution XOR HumanWorkSession). Si
  ves una entry citando una Execution para una tarea humana, repórtalo.
- El **scope private** del agente humano se atribuye al **user
  trabajador** (a diferencia del agente IA) — es intencional.
- El Memorizer destila al **task=done** (gate `should_memorize_human_session`):
  si la tarea no está done o el worker de memoria está caído, no verás
  MemoryEntries todavía.

---

## `human_16_05` — Asistente personal: tools de Human Workload

**Qué prueba**: con el asistente habilitado, preguntar por la carga de un
usuario y por las tareas pendientes responde correctamente, y las
respuestas respetan el RBAC del admin que pregunta.

**Precondiciones**:

- Asistente personal habilitado para un admin (Plan 10), con las tools
  `tenant_human_workload` y `tenant_human_assignments_pending` en los
  enabled tools.
- Datos de carga humana en el tenant (asignaciones + sesiones de la
  semana).

**Pasos**:

1. En el asistente, pregunta **"¿Cuántas tareas tiene Fulano esta
   semana?"** → debe responder con el **número correcto** (asignaciones
   abiertas pending+accepted + sesiones de la semana ISO).
2. Pregunta **"¿Hay tareas humanas sin aceptar desde hace más de 24 h?"**
   → debe **listarlas correctamente** (pending_acceptance > 24 h por
   defecto).
3. Comprueba el **RBAC**: un admin **no debe ver datos de proyectos a los
   que no tiene acceso**; "Fulano" solo se resuelve si es **miembro del
   tenant** del admin.

**Resultado esperado**: ambas tools responden con datos correctos y las
respuestas respetan el RBAC del admin que pregunta.

**Checklist**:

- [ ] "¿Cuántas tareas tiene Fulano esta semana?" responde con número
      correcto.
- [ ] "¿Hay tareas humanas sin aceptar desde hace más de 24 h?" lista
      correctamente.
- [ ] Las respuestas respetan RBAC: un admin no ve datos de proyectos a
      los que no tiene acceso.

**Pitfalls conocidos**:

- `tenant_human_workload` cuenta **asignaciones abiertas (pending +
  accepted) + sesiones de la semana ISO** y resuelve al usuario **solo
  entre miembros del tenant del admin** (RLS): si "Fulano" no es miembro,
  no se resuelve — es el comportamiento correcto, no un fallo.
- `tenant_human_assignments_pending` usa **24 h por defecto** como umbral
  (parametrizable): si no devuelve nada, comprueba que hay asignaciones
  en `pending_acceptance` con antigüedad > 24 h.
- Ambas tools deben estar en `ASSISTANT_TOOLS` + `DEFAULT_ENABLED_TOOLS`:
  si el asistente no las invoca, revisa que estén habilitadas para ese
  admin.

---

## `human_16_06` — Coste humano y budget

**Qué prueba**: en un plan con tareas humanas costosas y
`project.budget_includes_human_cost=true`, el coste humano se imputa
(rate × horas reales), el dashboard 13.7 segmenta AI cost vs Human cost,
las alertas de budget incluyen el coste humano, y al cruzar el 100 % con
coste humano incluido los nuevos arranques se pausan (sección 28.7.4).

**Precondiciones**:

- Un proyecto con **`budget_includes_human_cost=true`** y un budget
  configurado.
- Un plan con tareas humanas de coste apreciable (rate × horas) ya
  completadas con horas logueadas.

**Pasos**:

1. Ejecuta tareas humanas con **horas reales** logueadas: el **coste
   humano se imputa** como **rate × horas** (no estimado).
2. Abre el dashboard **13.7** (`/admin/projects/{id}/consumption`): debe
   **segmentar AI cost vs Human cost**.
3. Con **`budget_includes_human_cost=true`**, comprueba que las **alertas
   de budget incluyen el coste humano** (no solo el AI).
4. Lleva el consumo a **cruzar el 100 %** contando el coste humano: los
   **nuevos arranques se pausan** (sección 28.7.4, banner de pausa).

**Resultado esperado**: el coste humano se imputa por horas reales, el
dashboard lo segmenta, las alertas de budget lo incluyen, y al 100 % con
coste humano los arranques se pausan.

**Checklist**:

- [ ] Coste humano se imputa correctamente (rate × horas reales).
- [ ] El dashboard 13.7 segmenta AI cost vs Human cost.
- [ ] Si `budget_includes_human_cost=true`, las alertas de budget
      incluyen el coste humano.
- [ ] Al cruzar 100 % con coste humano incluido, los nuevos arranques se
      pausan (sección 28.7.4).

**Pitfalls conocidos**:

- `Project.budget_includes_human_cost` es **`false` por defecto**
  (`task_16_12`, migración 0074): con `false`, el coste humano se
  **muestra segmentado** pero **NO suma al budget** — para validar la
  pausa al 100 % debes ponerlo en `true`.
- `human_cost.py` imputa **rate × horas → USD** vía FX, con fallback
  `DEFAULT_HOURLY_RATE_EUR` si no hay rate del Human Agent: si el coste
  sale raro, comprueba el `hourly_rate` del Human Agent y el FX.
- La pausa al 100 % reutiliza el mecanismo de **budget del Plan 11**
  (sección 28.7.4): si no pausa, verifica que el budget del proyecto está
  configurado y que el consumo (AI+Human) realmente cruzó el umbral.

---

## Cierre del plan

Tras pasar los 6 tests humanos:

1. Edita `docs/roadmap/16-human-agents.md`:
   ```yaml
   status: completed
   completed_at: 2026-MM-DD
   ```
2. Verifica el ADR 0046 (modelo `agent_type` ai/human), la guía
   [`docs/03-guides/human-agents.md`](../human-agents.md), el runbook
   [`docs/06-runbooks/human-tasks-operations.md`](../../06-runbooks/human-tasks-operations.md)
   y la entrada en
   [`docs/07-changelog/16-human-agents.md`](../../07-changelog/).
3. Verifica que el PR `plan/16-human-agents` está mergeado a `master`.

## Troubleshooting

| Síntoma                                      | Causa probable                                             | Fix                                                                 |
| -------------------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------- |
| La tarea humana pide contenedor del pool     | (No debería) el orchestrator no detectó `agent_type=human` | Repórtalo; debe crear HumanTaskAssignment, no pedir runtime         |
| El timeout no reasigna al escalation target  | celery-beat caído (el sweep corre cada 10 min)             | Levanta celery-beat; el sweep detecta los timeouts vencidos         |
| El user no recibe la notificación            | Canal de notificación no configurado (Plan 10)             | Configura el canal preferido del user                               |
| El peer reviewer no recibe la review         | Solo hay un Human Agent en el tenant                       | Crea un segundo Human Agent para que haya a quién asignar la review |
| No aparecen MemoryEntries de la tarea humana | Tarea no done o worker de memoria caído                    | El Memorizer destila al task=done; comprueba el worker de memoria   |
| El coste humano no suma al budget            | `budget_includes_human_cost=false` (default)               | Ponlo en `true` para que el coste humano cuente en el budget/pausa  |
| La tool del asistente no resuelve a "Fulano" | El usuario no es miembro del tenant del admin (RLS)        | Comportamiento correcto; pregunta por un miembro del tenant         |

Errores transversales viven en `docs/03-guides/gotchas/`.
