---
title: Visor de Runs en el menú Trabajo
plan_id: runs-visor-trabajo
date: 2026-06-27
status: entregado
---

# Visor de Runs (menú Trabajo)

Lista de ejecuciones (runs) de los agentes en el menú **Trabajo**, con detalle por
run y acceso desde el Kanban. La mayor parte de la infraestructura ya existía
(tabla `executions`, API de detalle, streaming WS, página de timeline); este plan
añade la **lista accesible a miembros**, el **ítem de navegación**, el **panel de
historial por tarea** en el Kanban y el **fix de tokens** de claude_sdk.

## Backend

- **`GET /runs`** (nuevo, `routers/runs.py`): lista de runs del tenant accesible a
  **cualquier miembro** (`require_tenant_member` + RLS), recientes-primero,
  paginada y filtrable (`task_id`, `plan_id`, `agent_id`, `role`, `verdict`,
  `model`, `min_cost`, ventana de fechas). Reutiliza `query_execution_runs()`
  —extraída de `/tenant-stats/runs`— así que el shape de fila (`ExecutionRunRow`)
  y las garantías de aislamiento son idénticas al explorador admin; solo cambia el
  rol. `/tenant-stats/runs` (analítica/export) sigue siendo `tenant_admin`.
- **Tokens claude_sdk** (`shared-llm`): los runs con `claude_sdk` mostraban
  `cost>0` pero `tokens=0` porque el `usage` del SDK llega como dict y se leía con
  `getattr`. Nuevo `_usage_get(u, name)` lee objeto-o-dict; aplicado en el harvest
  del path de decisión, el stream y los eventos de `run_agent`.
- Tests de integración (`/runs`: miembro lista, filtro por tarea, **aislamiento
  cross-tenant**) + unitarios del harvest de tokens.

## Frontend (admin-panel)

- **`/admin/runs`**: tabla de runs recientes-primero (fecha · plan · tarea · agente
  · modelo · estado · duración · **tokens** · **coste**), filtro por estado,
  paginación. Las filas `running` se auto-refrescan; al hacer click se abre el
  Timeline de la ejecución (`/admin/executions/{id}`, con streaming en vivo).
- **Navegación**: nuevo ítem **"Runs"** en el grupo _Trabajo_ del sidebar (antes la
  página de detalle estaba huérfana).
- **Panel de historial en el Kanban** (`RunHistorySheet`): al hacer click en una
  tarjeta se abre un diálogo con los runs de esa tarea (`GET /runs?task_id=`); cada
  fila abre su Timeline. Distinción click-vs-drag para no romper el drag&drop ni el
  candado de dependencias.
- **Detalle del run**: enlace "Volver a Runs". El resumen (estado · iteraciones ·
  tokens · coste) y el botón Cancelar (si `running`) + el streaming en vivo ya
  existían.
- `lib/runs.ts`: cliente `listRuns()` + tipo `ExecutionRunRow` + formateadores
  compartidos. Tests vitest de la construcción de querystring.

## Notas / límites

- **Métricas solo al finalizar**: las columnas denormalizadas (tokens/coste/
  duración) se persisten en `finalize_execution`; un run `running` se muestra con
  estado pero sin esas métricas hasta que termina. Los números en vivo están en el
  detalle vía el stream WS. No es un bug, es el modelo de persistencia.
- **i18n**: el admin-panel **no tiene framework de i18n** (todas las páginas usan
  castellano hardcodeado). Las páginas nuevas siguen esa convención; soportar EN
  sería un esfuerzo de plataforma transversal, fuera de alcance de este plan.
- La traza no guarda prompts/respuestas literales del LLM (decisión del runtime);
  el detalle muestra tool calls (args+result) y el output final.
