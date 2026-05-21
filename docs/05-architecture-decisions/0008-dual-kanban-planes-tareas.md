---
adr: "0008"
title: Doble Kanban — vista de Planes + vista de Tareas
status: accepted
date: 2026-05-21
deciders: System Architect
phase: 01-dominio-minimo
---

# ADR 0008 — Doble Kanban: vista gerencial de Planes + vista operativa de Tareas

## Contexto

El producto orquesta planes (cada uno un grafo DAG de tareas). Dos
audiencias muy distintas miran el tablero:

- **El gerente / Project Manager** quiere ver iniciativas en curso,
  cuántos planes hay en cada fase del ciclo, dónde se ha atascado
  algo. Granularidad gruesa: un plan por tarjeta.
- **El operador / agente humano supervisor** quiere ver tareas
  individuales del plan que está ejecutando, mover una bloqueada a
  revisión, ver dependencias. Granularidad fina: una tarea por
  tarjeta.

La tentación de mostrar **un solo Kanban con todas las tareas de
todos los planes** existe. Es lo que hacen Jira-style boards por
defecto. En nuestro dominio rompe las dos audiencias a la vez:

- El gerente no entiende qué está pasando porque ve cientos de
  tareas mezcladas.
- El operador pierde contexto del plan al que pertenece la tarea.

## Decisión

Dos vistas Kanban apiladas en la misma pantalla
(`/admin/board`), siempre relacionadas por **selección explícita
de plan**:

1. **Vista superior — Planes**: cada tarjeta es un proyecto/plan.
   Click selecciona y filtra la vista inferior.
2. **Vista inferior — Tareas del plan seleccionado**: las 7
   columnas son los `TaskStatus` (`backlog → ready → in_progress →
in_review → blocked → done → cancelled`). Drag & drop sobre la
   tarjeta cambia su `status`.

Reglas duras:

- **Nunca un Kanban plano que mezcle tareas de varios planes.**
  Recogido en CLAUDE.md §6.
- **Cancelled** se muestra como columna pero en último lugar — es
  terminal y debe ser raro.
- El drag & drop usa la API HTML5 nativa (sin librerías de DnD) y
  envía `PUT /projects/{pid}/tasks/{tid}` con `{status}` actualizado
  optimistamente en cache. Si la mutación falla se revierte y se
  muestra un banner de error.

## Alternativas descartadas

1. **Pantallas separadas (`/planes` y `/tareas`).** Forzaba al
   usuario a saltar entre tabs perdiendo contexto. Rechazado a
   favor de tener ambas vistas a la vista a la vez.
2. **Solo Kanban de Tareas con un filtro de plan.** Es lo que
   muchas herramientas hacen. Rechazado porque convierte la vista
   de Planes en un "dropdown filter", restando peso gerencial al
   nivel de Plan. La doctrina del producto pone planes y tareas en
   pie de igualdad como unidades de cambio.
3. **dnd-kit / react-beautiful-dnd.** Más features (multi-select,
   accesibilidad teclado), pero suma una dependencia gorda para
   un MVP que sólo necesita "arrastra y suelta entre columnas". Si
   crecen los requisitos (reorder dentro de columna, swimlanes),
   sí migraremos.

## Consecuencias

Positivas:

- La doctrina del doble Kanban está plasmada en código y test
  E2E (`dual-kanban.spec.ts` verifica que las 7 columnas
  aparecen en el orden esperado y que un drag&drop dispara el
  `PUT`).
- Misma pantalla cubre las dos audiencias sin diseñar dos UIs.
- Permite extender la vista superior con KPIs por plan (en Plan
  02 añadiremos `tasks_done/tasks_total`, `progress%`).

Negativas / cuidados:

- En tenants con muchos planes activos, la vista de Planes puede
  necesitar paginación o filtros (status, team). Se difiere a Plan
  02 cuando aparezca la tabla `plans` real.
- Sin claim `tid` en el JWT, el `PUT` de mover una tarea devuelve
  400 (`active tenant required`). El test E2E lo cubre con
  `route.fulfill` para validar el camino del cliente sin tocar la
  BD. Plan 02 introduce la pantalla de selección de tenant y
  desactiva el mock.
- 7 columnas en pantallas pequeñas no caben en una sola fila; el
  layout usa `overflow-x-auto` + `grid-cols-2` mobile / `lg:grid-
cols-7` desktop.

## Referencias

- Documento maestro, sección 9 (modelo de planes y tareas).
- Implementación: `apps/admin-panel/app/admin/board/page.tsx`.
- Tests: `apps/admin-panel/e2e/dual-kanban.spec.ts`.
- Endpoint usado: `PUT /projects/{pid}/tasks/{tid}`.
