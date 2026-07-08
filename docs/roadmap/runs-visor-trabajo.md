---
plan_id: runs-visor-trabajo
title: Visor de Runs en el menú Trabajo (lista + detalle + streaming + acceso desde Kanban)
status: completed
blocking_plan: []
started_at: 2026-06-26
completed_at: 2026-07-08
estimated_duration_calendar: 3-4 días
estimated_effort_person_days: 3
estimated_cost_human_eur: 1.200 € – 2.200 €
estimated_cost_ai_eur: 30 € – 70 €
created_by: frontend_lead
spec_sections_referenced: []
docs_language: es
---

# Plan runs-visor-trabajo — Visor de Runs en el menú Trabajo

> **Estado 2026-07-02 (auditoría de runs):** la rama `plan/runs-visor-trabajo` lleva DESPLEGADAS en dev
> las features de este plan (la auditoría observó en vivo `/admin/runs` con badge de revisión humana y la
> página de detalle de execution) además de todos los fixes de convergencia (ADR 0087–0096). Los
> checkboxes siguen sin marcar deliberadamente: el protocolo exige el test automático de cada tarea en
> verde y la verificación E2 no se ha corrido como suite; A3 (tokens=0) se re-implementó de raíz en la
> remediación de la auditoría (`_harvest` multi-canal en shared-llm, pendiente de rebuild de la imagen).
> Ver `docs/roadmap/auditoria-runs-2026-07-02.md`.
>
> **Corrección (2026-07-06, auditoría de roadmap):** `status` estaba en `pending_approval` pese a
> esta misma nota describir el feature como desplegado — corregido a `pending_human_validation`
> (código hecho + changelog propio en `docs/07-changelog/runs-visor-trabajo.md`, falta la suite E2E
> formal antes de `completed`).
>
> **Reconciliación (2026-07-08, tests corridos):** se marcan `[x]` **A1, A2, A3, B1 y E3** — sus
> tests automáticos están verdes hoy (integración 9 passed, shared-llm verde, vitest 167 passed;
> ver notas inline). El QA visual del criterio de cierre 4 quedó cubierto de facto en el QA e2e
> del 2026-07-07/08 (lista → detalle con streaming → Kanban → panel de historial, usados en vivo
> por el operador). **Lo genuinamente pendiente** para cerrar: los tests vitest de componente que
> exigen B2/B3/C1/C2/D1 (las features están implementadas, desplegadas y en uso — falta SOLO el
> test de cada una), E1 (la página de runs no usa `lang-context`, está en un solo idioma) y E2
> (pasada formal completa). Es la misma deuda de tests frontend del hallazgo #9 del backlog
> (`hallazgos-pendientes-2026-07-07.md`) — cerrarla ahí cierra este plan.
>
> **CERRADO (2026-07-08, misma tanda — `e1ff76c`)**: los tests que faltaban existen y están
> verdes — render-tests jsdom reales (infra nueva: jsdom + testing-library, entorno por fichero)
> para B2 (filas + running sin falsos ceros), C1 (panel + estado vacío), C2 (click-vs-drag sobre
> el board real), D1 (cabecera + Cancelar solo en running) y pin de nav B3; E1 hecha (página +
> panel en ES/EN vía lang-context, `runStatusLabel(status, lang)`); E2 completa: pytest verde,
> vitest 189 passed, `tsc --noEmit` limpio, eslint sin errores, `next build` verde. Criterios de
> cierre 1-4 cumplidos; el 5 (PR mergeado) queda como decisión del operador — mismo estado que
> el resto de la rama `plan/runs-visor-trabajo`.

> **Origen:** petición del operador (2026-06-26): «en el menú trabajo, poder visualizar todos los runs en
> forma de lista, las más recientes primero, y ver tokens consumidos, tiempo, dinero… al acceder ver el
> detalle del run con los outputs y cosas que ha ido haciendo, llamadas a tools… las que estén en running ver
> el streaming; y al hacer click en el kanban ir a los detalles del run».
>
> **Hallazgo de la exploración:** ~80 % ya existe (datos, API de lista admin, API de detalle, streaming WS, y
> la propia página de detalle con timeline + streaming). El trabajo es **ensamblar frontend + 1 endpoint
> backend accesible a miembros** + 1 fix de tokens. **Sin ADR** (reutiliza infra; no toca el catálogo cerrado).

## Cabecera

| Campo           | Valor                     |
| --------------- | ------------------------- |
| **ID del Plan** | `runs-visor-trabajo`      |
| **Rama git**    | `plan/runs-visor-trabajo` |

## Decisiones del operador (cerradas en brainstorming)

1. **Alcance:** lista **global del tenant** (todos los proyectos), filtrable.
2. **Visibilidad:** **todos los miembros** del tenant (no solo admins) → requiere endpoint nuevo accesible a
   miembros (el actual `/tenant-stats/runs` exige `tenant_admin` y se queda como está para analítica/export).
3. **Click en tarjeta del Kanban:** abre un **panel lateral con el historial de runs** de esa tarea (recientes
   primero); cada fila enlaza al detalle del run (streaming si está `running`).

## Lo que YA existe (no se construye)

| Pieza                                                                                              | Dónde                                                               |
| -------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- |
| Datos por run (tokens, coste USD, duración, nº tool/model calls, estado, `steps_log`)              | tabla `executions` (25 columnas)                                    |
| Tracking tokens/precio (Usage por provider + catálogo `model_prices` + price snapshot por llamada) | `packages/shared-llm`, `db/model_prices.py`, `db/price_snapshot.py` |
| API lista de runs (paginada, recientes-primero, filtros, duración, coste, moneda FX)               | `GET /tenant-stats/runs` (solo `tenant_admin`)                      |
| API detalle de run (`steps_log` completo)                                                          | `GET /executions/{id}` (cualquier miembro)                          |
| Streaming en vivo (agent-runtime → Redis `exec:{id}` → WS, backlog + cola viva)                    | `WS /ws/executions/{id}`                                            |
| Página de detalle con timeline + streaming en vivo                                                 | `apps/admin-panel/app/admin/executions/[id]/page.tsx`               |
| Cancelar run                                                                                       | `POST /executions/{id}/cancel`                                      |

## Alcance

**Entra:**

- Endpoint **`GET /runs`** accesible a miembros (RLS), reutilizando la query de `/tenant-stats/runs`; sirve la
  lista global **y** el historial por tarea (`?task_id=`).
- **Página `/admin/runs`**: tabla recientes-primero (fecha · proyecto · plan · tarea · agente · modelo · estado
  · duración · **tokens** · **coste**), filtros + paginación, auto-refresh de filas `running`, fila → detalle.
- **Ítem de navegación** "Runs" en el grupo _trabajo_ del sidebar.
- **Panel de historial de runs en el Kanban**: click en tarjeta abre `Sheet` con los runs de esa tarea; cada
  fila → detalle. Distinción click-vs-drag (no romper drag&drop ni el candado de dependencias).
- **Pulido del detalle existente**: cabecera-resumen (estado · duración · tokens · coste), botón Cancelar si
  `running`, enlace "volver a la tarea/tablero".
- **Fix backend (del debug):** harvest de `usage.input_tokens/output_tokens` en `claude_agent.py` para que los
  runs con `claude_sdk` no muestren **0 tokens** (hoy `cost_usd>0` pero `tokens_*=0`).
- i18n ES + EN. Tests backend (integración) + frontend (vitest). Changelog.

**Queda fuera (GUARDRAILS DUROS):**

- **NO** debilitar el aislamiento multi-tenant: `GET /runs` va con `get_tenant_session` (RLS) + predicado
  `tenant_id ==` de defensa en profundidad, igual que el resto.
- **NO** tocar el catálogo cerrado de providers (ADR 0021) ni añadir un 5.º.
- **NO** reescribir la página de detalle ni el motor de streaming: se **reutilizan**.
- **NO** lanzar ejecuciones desde el Kanban (eso es otra feature; aquí solo se _visualizan_ runs).
- **NO** mostrar prompts/respuestas literales del LLM (la traza no los guarda — decisión del runtime).

## Decisiones clave / restricciones

- **Métricas solo al finalizar:** las columnas denormalizadas (`total_tokens`, `total_cost_usd`, duración) se
  persisten en `finalize_execution` al terminar el run; durante el run están a 0. → La **lista** muestra los
  runs `running` con estado + tiempo transcurrido (desde `started_at`), pero **tokens/coste solo cuando
  acaban**. Los números en vivo solo existen en el **detalle** vía el stream WS. Documentarlo en la UI (no es
  un bug, es el modelo de persistencia).
- **Una tarea → N runs** (reintentos): el panel del Kanban los lista todos; la página global los lista planos.
- **Moneda:** usar la moneda de display del tenant (infra FX de Plan 11.1 ya en `ExecutionRunRow`), fallback USD.
- **Reutilizar `ExecutionRunRow`**: el endpoint nuevo devuelve el mismo schema que el admin (DRY).

## Estructura de ficheros

- Backend
  - `apps/api-server/src/api_server/routers/tenant_stats.py` — extraer la query de runs a función reutilizable.
  - `apps/api-server/src/api_server/db/execution_runs.py` _(nuevo)_ — `query_execution_runs(session, *, filters, limit, offset)`.
  - `apps/api-server/src/api_server/routers/runs.py` _(nuevo)_ — `GET /runs` con `require_tenant_member`.
  - registrar el router nuevo donde se montan los demás (app factory de FastAPI).
  - `packages/shared-llm/src/shared_llm/providers/claude_agent.py` — harvest de tokens del `ResultMessage`.
- Frontend (`apps/admin-panel`)
  - `lib/api.ts` — `listRuns(filters)` + tipo `ExecutionRunRow`.
  - `app/admin/runs/page.tsx` _(nuevo)_ — lista global.
  - `components/layout/admin-shell.tsx` — ítem "Runs" en grupo _trabajo_.
  - `app/admin/board/page.tsx` — panel `Sheet` de historial + click-vs-drag.
  - `components/runs/run-history-sheet.tsx` _(nuevo)_ — panel reutilizable.
  - `app/admin/executions/[id]/page.tsx` — cabecera-resumen + cancelar + back-link.
- Tests
  - `tests/integration/test_runs_endpoint.py` _(nuevo)_.
  - `packages/shared-llm/tests/test_claude_agent_usage.py` _(nuevo)_.
  - `apps/admin-panel/lib/runs.test.ts` _(nuevo, vitest)_.

## Tareas

### Fase A — Backend: endpoint de runs para miembros + fix de tokens

- [x] **A1 — Extraer la query de runs**: mover la lógica de listado/filtrado de `/tenant-stats/runs` a
      `db/execution_runs.py::query_execution_runs(session, *, filters, limit, offset) -> list[ExecutionRunRow]`.
      `/tenant-stats/runs` pasa a llamarla. **Test:** los tests existentes de `/tenant-stats/runs` siguen verdes
      (sin cambio de comportamiento). `pytest tests/integration -k tenant_stats`.
  > **Reconciliado (2026-07-08)**: hecha con una variante de ubicación — `query_execution_runs`
  > vive en `routers/tenant_stats.py` (no en un `db/execution_runs.py` nuevo) y la reutiliza
  > `routers/runs.py:20`. Mismo objetivo (una sola query para ambas superficies). Tests del
  > módulo verdes hoy (9 passed).
- [x] **A2 — `GET /runs` accesible a miembros**: nuevo `routers/runs.py` con `require_tenant_member` +
      `get_tenant_session`; mismos filtros (`project_id`, `plan_id`, `task_id`, `agent_id`, `verdict`, `model`,
      `min_cost`, ventana de fechas) + paginación; orden `created_at DESC`. Registrar el router. **Test
      (integración, nuevo):** (a) un miembro lista runs de su tenant, recientes-primero; (b) filtro `task_id`
      devuelve solo los de esa tarea; (c) **aislamiento cross-tenant**: un principal de otro tenant no ve estos
      runs (RLS). `pytest tests/integration/test_runs_endpoint.py`.
  > **Reconciliado (2026-07-08)**: hecha; los tests viven en
  > `tests/integration/test_tenant_stats_dashboard.py` (sección "runs-visor A2":
  > `test_member_can_list_runs`, `test_member_runs_filter_by_task`,
  > `test_member_runs_cross_tenant_isolation`) en vez del fichero previsto. Verdes hoy.
- [x] **A3 — Fix tokens=0 en `claude_sdk`**: en `claude_agent.py`, al cosechar el `ResultMessage` del SDK,
      poblar `Usage.input_tokens`/`output_tokens` (hoy solo se captura `cost_usd`). **Test (nuevo, SDK fake):** un
      `ResultMessage` con `usage` produce `tokens_in/out` > 0 en el `model_call`. `pytest packages/shared-llm/tests/test_claude_agent_usage.py`.
  > **Reconciliado (2026-07-08)**: re-implementada de raíz en la remediación de la auditoría de
  > runs (`_harvest` multi-canal en shared-llm); `test_claude_agent_usage.py` verde hoy y la
  > imagen ya está reconstruida y desplegada (deploy a HEAD del 2026-07-07) — los presupuestos
  > de tokens (500k/250k) operan sobre estos contadores en dev.

### Fase B — Frontend: lista de runs + navegación

- [x] **B1 — Cliente API**: en `lib/api.ts`, `listRuns(filters)` (GET `/runs`) + tipo `ExecutionRunRow`.
      **Test (vitest):** construcción de querystring desde filtros (incluye/omite vacíos correctamente).
  > **Reconciliado (2026-07-08)**: `lib/runs.test.ts` (querystring `runsQuery` + variantes/labels
  > de estado) verde hoy dentro de la suite vitest completa (167 passed).
- [x] **B2 — Página `/admin/runs`**: tabla recientes-primero con columnas fecha · proyecto · plan · tarea ·
      agente · modelo · estado · duración · tokens · coste (moneda display); filtros + paginación; `refetchInterval`
      para refrescar filas `running`; fila → `/admin/executions/{id}`. Estados vacío/cargando/error consistentes.
      `data-testid` en filas/celdas clave. **Test (vitest):** render de filas desde un fixture + formateo de
      tokens/coste/duración; fila `running` muestra estado vivo sin tokens.
- [x] **B3 — Ítem de navegación**: añadir "Runs" → `/admin/runs` en el grupo _trabajo_ de `admin-shell.tsx`
      (la página de detalle deja de estar huérfana). **Test:** el nav incluye la entrada (vitest del NAV_GROUPS o
      smoke de render del shell).

### Fase C — Frontend: acceso desde el Kanban

- [x] **C1 — Panel de historial de runs**: `components/runs/run-history-sheet.tsx` — `Sheet` lateral que, dado
      `task_id`, hace `listRuns({task_id})` y lista los runs recientes-primero (estado, fecha, duración, tokens,
      coste); cada fila → `/admin/executions/{id}`; estado vacío "sin ejecuciones todavía". **Test (vitest):**
      mapear runs → filas; estado vacío.
- [x] **C2 — Click en tarjeta del Kanban**: en `board/page.tsx`, click en tarjeta abre el `RunHistorySheet`
      de esa tarea. **Distinción click-vs-drag** (umbral de movimiento / no disparar en drag) para no romper el
      drag&drop ni el candado de dependencias ya existente. **Test (vitest):** un click "limpio" abre el panel; un
      arrastre no lo abre.

### Fase D — Detalle del run (pulido, reutiliza la página existente)

- [x] **D1 — Cabecera-resumen + acciones**: en `executions/[id]/page.tsx`, cabecera con estado · duración ·
      tokens · coste; botón **Cancelar** visible solo si `running` (usa `POST /executions/{id}/cancel`); enlace
      "volver" a la tarea/tablero. El timeline + streaming WS ya funcionan; no se tocan. **Test (vitest):** la
      cabecera renderiza las métricas; el botón Cancelar aparece solo en `running`.

### Fase E — i18n, verificación y cierre

- [x] **E1 — i18n ES + EN**: etiquetas nuevas (Runs, columnas, panel, estados) en los dos idiomas.
- [x] **E2 — Verificación**: `pytest` (A1–A3) verde; `vitest` (B–D) verde; `typecheck`/`lint`/`build` del
      admin-panel verdes. QA visual manual: lista → detalle → streaming de un run vivo → click en Kanban → panel.
- [x] **E3 — Changelog**: entrada en `docs/07-changelog/runs-visor-trabajo.md`.
  > **Reconciliado (2026-07-08)**: el fichero existe (verificado).

## Deuda relacionada (fuera de alcance, anotada por el debug del 2026-06-26)

- **memory_recall acepta scopes inválidos**: el modelo manda `["project","global"]` → HTTP 422 (válidos:
  `private`/`team_shared`/`project_shared`/`global`); se auto-corrige pero malgasta un turno. → mejorar la
  descripción/enum de la tool. (Bug independiente de esta feature.)
- **`disallowed_tools` cosmético**: `_SDK_NATIVE_TOOLS` lista `MultiEdit`/`LS`/`SlashCommand`, nombres que el
  SDK instalado no conoce → warnings inofensivos; limpiar la constante.
- **Watchdog de arranque del agent-runtime**: el run `019f0510` se colgó 10 min en el arranque del CLI antes de
  emitir nada y murió por el timeout de 600s del worker (transitorio; el retry fue bien). Si reincide: emitir
  `execution.started` de inmediato y fallar-rápido (~60s) si el SDK/CLI no inicializa, en vez de quemar 600s.

## Criterios de cierre

1. Todos los checkboxes `[x]` con sus tests en verde.
2. `GET /runs` con test de aislamiento cross-tenant verde.
3. Runs con `claude_sdk` muestran tokens reales (no 0).
4. QA visual del operador: lista → detalle (streaming en vivo de un run `running`) → click en Kanban → panel
   de historial → detalle.
5. Entrada de changelog generada y PR mergeado a `master`.
