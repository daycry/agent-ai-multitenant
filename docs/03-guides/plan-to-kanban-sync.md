---
title: Sincronizar un plan al Kanban
audience: usuario tenant
phase: 03-chat-planning-aprobacion
updated: 2026-05-25
---

# Sincronizar un plan al Kanban

Esta guía cubre el último paso del ciclo de planning: convertir el
plan aprobado en tarjetas del Kanban listas para que los agentes
empiecen a ejecutarlas.

> **Prerrequisitos.** El plan está en estado `approved` (Fase F
> firmada por uno o dos humanos según el umbral del tenant). El
> proyecto tiene al menos un equipo asignado.

## 1. Abrir la vista de detalle del plan

1. Pestaña **Planes** del proyecto.
2. Click en el plan que quieres materializar.
3. Verás la cabecera con la insignia **Aprobado** y, debajo de las
   estimaciones, la tarjeta **Sincronizar al Kanban**.

## 2. Elegir un scope

El botón **Sincronizar al Kanban** abre un diálogo con tres opciones:

| Scope                | Cuándo usarlo                                                                                                                                                  |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Plan completo**    | El humano confía en el plan y quiere que el orchestrator empiece a tirar de todas las tareas.                                                                  |
| **Por fase**         | Quieres validar primero los entregables de una fase antes de comprometerte con el resto. La barra de fases en el detalle te muestra qué tareas entra cada una. |
| **Selección custom** | Necesitas adelantar una tarea concreta (típico: una investigación previa) sin arrastrar a las que dependen de ella.                                            |

Cuando confirmas, el backend escribe en `tasks` las filas necesarias
con `status = backlog`, marca cada una con su origen
(`inputs.plan_task_spec_id`) y crea las filas de `task_dependencies`
que el spec declare.

## 3. Re-sincronizar más tarde es seguro

La sincronización es **idempotente** (ver [ADR 0022](../05-architecture-decisions/0022-plan-to-kanban-sync.md)):

- Una segunda llamada con el mismo scope **no duplica tareas**. Las
  ya creadas aparecen como `skipped_task_ids` en la respuesta y la
  UI muestra "X ya existían".
- Si arrancaste con scope `phase 0` y luego pulsas `total`, solo se
  materializan las tareas que faltaban. Las nuevas dependencias entre
  tareas nuevas y existentes se cablean automáticamente.

> **Lo que no es seguro.** Renombrar el `id` de una tarea del spec
> después de una primera sincronización **sí** crea un duplicado: la
> nueva tarea no tiene `plan_task_spec_id` en la columna marcadora.
> Trata los spec ids como inmutables tras la primera aprobación.

## 4. Garantías DAG en el Kanban

Una vez en el Kanban, intentar **arrastrar una tarjeta a
`in_progress`** (o `awaiting_human_approval`, o `in_review`) **fallará
con 422** si alguna dependencia upstream sigue sin estar `done`. El
mensaje del 422 lista cada dependencia pendiente con su estado actual,
para que la UI pueda mostrarte exactamente qué bloquea el arranque.

Esto se aplica también al orchestrator: el `fn_compute_task_ready` de
Plan 02 ya no promueve a `ready` mientras haya una dependencia
pendiente. La diferencia es que aquí también gobierna las
transiciones manuales desde el board, no solo las automáticas.

Estados libres (sin guard DAG): `backlog`, `ready`, `blocked`,
`done`, `cancelled`. Sólo los que implican "el agente va a empezar a
gastar minutos" pasan por el check.

## 5. Solución de problemas

| Síntoma                                                  | Causa probable                                                  | Qué hacer                                                                                                           |
| -------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| 422 al sincronizar con `phase`                           | `phase_index` fuera de rango o referencia tareas que no existen | Mira la respuesta; el campo `detail.message` lo explica.                                                            |
| 422 al sincronizar con `selection`                       | Algún task id no existe en el plan                              | El `detail.message` lista los ids desconocidos.                                                                     |
| 422 al mover una tarjeta a `in_progress`                 | Hay dependencias upstream sin `done`                            | Revisa el grafo DAG en el detalle del plan; cierra primero las dependencias.                                        |
| Tareas duplicadas en el Kanban tras renombrar un spec id | Cambiaste el `id` después de sincronizar                        | Borra las tarjetas extra a mano y vuelve a sincronizar; los spec ids deben ser estables tras la primera aprobación. |

## Referencias

- [ADR 0022 — Sincronización Plan → Kanban](../05-architecture-decisions/0022-plan-to-kanban-sync.md)
- [Plan 03 — fase G](../roadmap/03-chat-planning-aprobacion.md#fase-g--sincronización-al-kanban)
- API Reference: `POST /plans/{id}/sync-to-kanban`,
  `PUT /projects/{p}/tasks/{t}`
