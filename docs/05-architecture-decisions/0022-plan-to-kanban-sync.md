---
adr: "0022"
title: Sincronización Plan → Kanban con scopes (total / fase / selección) e idempotencia por `plan_task_spec_id`
status: accepted
date: 2026-05-25
deciders: System Admin
phase: 03-chat-planning-aprobacion
---

# ADR 0022 — Sincronización Plan → Kanban con scopes e idempotencia

> **Estado: `accepted`.** Decisión tomada en el cierre de la Fase G del
> Plan 03. Recoge cómo materializamos un plan aprobado en tarjetas del
> Kanban y cómo garantizamos que reintentar la operación no duplica
> trabajo ni rompe el DAG.

## Contexto

Al final del Plan 03 un plan aprobado vive en `plans.specification` —
un JSONB con `tasks` y `phases`. Para que la plataforma haga algo con
él hace falta convertir esa lista plana en filas de la tabla `tasks`
con sus `task_dependencies`, que es lo que ya orquesta Plan 02
(`fn_compute_task_ready` y compañía).

Tres preguntas:

1. **Qué se materializa.** El humano no siempre quiere arrancar el
   plan entero de golpe. Las opciones razonables son:
   - **Total**: empuja todo de una.
   - **Por fase**: empuja solo las tareas de una fase concreta.
   - **Selección custom**: el humano elige tareas sueltas.
2. **Cómo se mantienen las dependencias.** Las `depends_on` del spec
   son strings (`"t1"`, `"t2"`, …) que apuntan a otros spec ids. En
   la tabla `tasks` cada fila tiene un UUID nuevo — hay que traducir.
3. **Qué pasa si se reintenta.** Una sincronización es una acción
   pesada y un humano puede:
   - Equivocarse de fase y querer reintentar con la siguiente.
   - Sincronizar primero "Fase 0" y luego "Total" para incorporar el
     resto.
   - Pulsar el botón dos veces accidentalmente.
     Ninguna de esas tres situaciones debería duplicar tareas ni
     ensuciar el board.

## Decisión

### Endpoint único: `POST /plans/{id}/sync-to-kanban`

```json
{ "scope": "total" }
{ "scope": "phase", "phase_index": 0 }
{ "scope": "selection", "task_ids": ["t1", "t3"] }
```

Pydantic valida la coherencia (`phase` requiere `phase_index`,
`selection` requiere `task_ids` no vacío). El router devuelve
`PlanSyncResponse`:

```json
{
  "created_task_ids": { "t1": "11111111-...", "t2": "22222222-..." },
  "skipped_task_ids": { "t3": "33333333-..." },
  "dependencies_created": 2
}
```

- `created_task_ids[spec_id] = task_id_uuid` — recién materializadas.
- `skipped_task_ids[spec_id] = task_id_uuid` — ya existían (idempotencia).
- `dependencies_created` — filas nuevas en `task_dependencies`.

El frontend pinta una línea de resultado tras cada llamada para que el
humano vea exactamente qué pasó.

### Idempotencia: `Task.inputs["plan_task_spec_id"]`

Al crear cada `Task` desde el spec, escribimos el spec id bajo la
clave **`plan_task_spec_id`** dentro del JSONB `inputs`. Esa marca es
la que la siguiente sincronización mira para saber "esta tarea del
spec ya está materializada — su UUID es X". Ventajas:

- **Sin migración**: `inputs` ya existía como JSONB libre.
- **Sin tabla aparte**: no añadimos un mapping `plan_task_id → task_id`
  que habría que mantener sincronizado en cascadas.
- **Visible desde la UI**: el detalle de la tarjeta lo enseña tal cual.

Cuando una sincronización recibe scope `phase` o `selection` y luego
otra sincronización `total`, las tareas ya creadas se reportan como
`skipped_task_ids` con el **mismo UUID** que la primera vez. Las
dependencias entre una tarea nueva y una ya existente se cablean
buscando la `plan_task_spec_id` correspondiente.

### DAG en runtime: 422 al promover una tarea con dependencias pendientes

Una vez en el Kanban, el `PUT /projects/{p}/tasks/{t}` debe vetar el
salto a `in_progress` / `awaiting_human_approval` / `in_review` si
alguna `task_dependencies` upstream no está `done`. El módulo
`api_server.chat.dag_enforcement` lo implementa con una sola consulta
JOIN `task_dependencies → tasks` y devuelve un 422 con
`detail.pending = [{task_id, status}, …]` para que la UI pueda decir
"no puedes empezar B hasta que A pase a `done`".

Estados libres (no consumen tiempo de agente y no se gatean):
`ready`, `backlog`, `blocked`, `done`, `cancelled`. Sólo los tres que
representan "el agente va a gastar minutos en esto" están bajo la
puerta del DAG.

## Alternativas consideradas

### Opción B — Una tabla `plan_task_materializations`

Mapping explícito `(plan_id, plan_task_spec_id) -> task_id`. Más
"limpio" en el sentido relacional, pero:

- Una migración por algo que ya cabe en una clave JSONB.
- Una tabla que mantener al borrar/clonar planes.
- Un join extra para todas las queries que ahora pueden leer
  `tasks.inputs->>'plan_task_spec_id'` directamente.

Se descartó por sobre-ingeniería.

### Opción C — Materializar todo y dejar la sincronización parcial al frontend

El backend siempre haría "total"; el frontend filtraría qué cards
muestra. Más simple, pero rompe el caso real "quiero arrancar solo la
Fase 0 para validar antes de comprometerme con el resto del plan": si
todas las tareas están ya en backlog, no hay forma de evitar que el
orchestrator empiece a tirar de las que no tocan.

Se descartó porque pierde el control que pide `human_03_04`.

### Opción D — DAG enforcement en la base de datos (CHECK trigger)

Un trigger `BEFORE UPDATE` sobre `tasks` que abortara la transición.
Más estricto, pero:

- Saltarse el trigger desde tests / scripts puntuales se vuelve un
  dolor (toca `SET LOCAL session_replication_role = replica`).
- El mensaje de error del trigger no puede llevar la lista estructurada
  de dependencias pendientes que la UI necesita.
- El orchestrator de Plan 02 ya promueve tareas vía `fn_compute_task_ready`,
  que es un trigger — añadir otro genera un orden de ejecución delicado.

El check vive en aplicación, en el router. Si en el futuro queremos
"defense in depth" añadimos el trigger sin retirar el check.

## Consecuencias

**Positivas**:

- El humano controla qué entra al Kanban (alineado con `human_03_04`).
- Reintentar es seguro: dos clicks accidentales no duplican.
- La UI puede mostrar "ya está en el board" sin lookup adicional.
- El error 422 al promover con dependencias pendientes es accionable.

**Negativas**:

- `plan_task_spec_id` vive en un campo libre (JSONB) — los lectores
  deben usar la constante exportada `PLAN_TASK_SPEC_ID_KEY`, no
  hardcodear `"plan_task_spec_id"`.
- Si el spec cambia el id de una tarea después de una sincronización
  parcial, la siguiente sincronización creará un duplicado. El editor
  de planes debe tratar los spec ids como inmutables tras la primera
  aprobación. Documentado en `docs/03-guides/plan-to-kanban-sync.md`.

## Tests / referencias

- `tests/unit/test_sync_scope.py` — selección de spec ids por scope.
- `tests/integration/test_sync_kanban.py` — materialización extremo a
  extremo, dependencias.
- `tests/integration/test_sync_idempotency.py` — re-sync sin duplicar.
- `tests/integration/test_dag_enforcement.py` — 422 al intentar
  arrancar tareas con dependencias pendientes.
- `apps/admin-panel/e2e/sync-to-kanban.spec.ts` — el diálogo y los
  tres scopes desde la UI.

## Próximos pasos

- (futuro Plan 05) extender `sync-to-kanban` para que admita re-syncs
  cuando el plan se refina post-aprobación (nuevas tareas, no cambios
  de id).
- Considerar añadir un trigger `BEFORE UPDATE ON tasks` como segunda
  capa (defense in depth) si pasamos del estado "MVP" a "producción
  multi-tenant abierta".
