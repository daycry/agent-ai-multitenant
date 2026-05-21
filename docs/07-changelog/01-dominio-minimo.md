---
plan_id: 01-dominio-minimo
title: Dominio Mínimo
started_at: 2026-05-21
completed_at: 2026-05-21
status: completed
tasks_done: 27
tasks_total: 27
tasks_pending_local: []
tests_automated_passing: 225
human_validations_passing: 0
docs_language: es
---

> **Estado:** plan cerrado. Backend completo para los seis catálogos
> built-in (agentes, skills, tools, teams, plantillas de proyecto y
> políticas de aprobación) con sus endpoints REST y RLS multi-tenant.
> Panel admin Next.js 14 con seis pantallas funcionales (Dashboard,
> Catálogo de Agentes, Equipos, Proyectos, Wizard de creación,
> Tablero doble Kanban, Validación humana). 209 tests pytest + 16
> tests Playwright en verde (uno skipped por dependencia de Plan 02).

# Changelog — Plan 01 · Dominio Mínimo

Fase **1** del Plan de Implementación. Sobre las fundaciones del Plan
00 (auth, RLS, admin panel base, observabilidad), Plan 01 mete las
entidades de negocio del sistema: agentes con scope global/local,
equipos, proyectos basados en plantilla, tareas en Kanban y políticas
de validación humana configurables.

## Resultado

Al cierre del plan, un tenant puede:

1. Explorar el catálogo plataforma de **11 agentes built-in**
   (Project Manager, Arquitecto, Backend Dev, Frontend Dev, QA,
   Reviewer, DevOps, Technical Writer, Data Scientist, Security
   Engineer, Personal Assistant) con descripción ES/EN.
2. Recorrer las **33 skills** y **18 tools** seedeadas, filtrar por
   `is_builtin` y crear sus propias variantes custom.
3. Ver los **5 equipos built-in** (API Team, Suite E2E, Backend
   Heavy, Frontend, Mixto) y crear teams nuevos componiéndolos con
   agentes en modo **Linked** (referencia) o **Forked** (copia
   editable, con diff/merge contra el origen).
4. Crear un proyecto desde el **wizard de 2 pasos** que toma una de
   las **8 plantillas de proyecto** y pre-rellena nombre,
   `repository_config` y `human_approval_policy`.
5. Configurar la **Política de Validación Humana** del proyecto
   eligiendo uno de los **4 presets** (Sandbox, Desarrollo,
   Producción, Cliente Externo) y, opcionalmente, sobreescribir
   categorías individuales (`code_changes`, `git_push`,
   `production_deploy`, … — 13 categorías canónicas).
6. Usar el **Doble Kanban** (`/admin/board`): vista superior de
   Planes (proyectos) + vista inferior de Tareas filtrada por plan
   seleccionado, con drag & drop entre las 7 columnas de
   `TaskStatus` (`PUT /projects/{pid}/tasks/{tid}` con actualización
   optimista).
7. Verlo todo en una UI moderna AI-SaaS con sidebar oscuro
   gradient + iconos lucide-react + responsive (drawer móvil
   debajo de `md`).

## Tareas completadas

### Fase A — Modelado y migraciones (task_01_01 a task_01_08)

- `task_01_01..04`: tablas `agents`, `skills`, `tools`, `teams` +
  RLS + policies `<tabla>_builtin_read` para lectura cross-tenant
  de built-ins.
- `task_01_05..07`: endpoints REST de los cuatro catálogos
  anteriores con CRUD + filtros y rechazo 404 sobre writes a
  built-ins (sin info-leak).
- `task_01_08`: tabla `tasks` + `task_dependencies` con CHECK de
  no-self-loop + endpoint `/projects/{id}/tasks`.

### Fase B — Linked vs Forked (task_01_09 a task_01_12)

- `task_01_09`: columnas `scope`, `project_id`, `parent_agent_id`
  en `agents`. Tres scopes: `global_builtin`,
  `global_tenant_template`, `project_local`.
- `task_01_10`: endpoint `POST /agents/{id}/fork` que clona prompts,
  modelo, skills y tools como `project_local` con `parent_agent_id`
  apuntando al origen.
- `task_01_11`: endpoints `GET /agents/{id}/diff` y
  `POST /agents/{id}/merge` para inspeccionar y absorber cambios
  del origen — ver [ADR 0006](../05-architecture-decisions/0006-linked-vs-forked-agents.md).
- `task_01_12`: invariantes verificadas por tests
  (`test_linked_vs_forked_invariants.py`): el cambio en el origen
  se ve en los linked; el cambio en el fork no se ve en el origen
  ni en otros forks.

### Fase C — Seeds plataforma (task_01_13 a task_01_18)

- `task_01_13..18`: seed runner idempotente con `uuid5(slug)` +
  `ON CONFLICT (id) DO UPDATE`. Seis fixtures que cubren los seis
  catálogos: 11 agentes, 33 skills, 18 tools, 5 teams, 8
  plantillas de proyecto, 4 políticas humanas. Estrategia
  documentada en [ADR 0007](../05-architecture-decisions/0007-idempotent-seed-strategy.md).

### Fase D — Pantallas funcionales (task_01_19 a task_01_21)

- `task_01_19` — **Catálogo de Agentes** (`/admin/agents`) con
  tres tabs: Built-in, Tenant, Local.
- `task_01_20` — **Detalle de Equipo** (`/admin/teams/[id]`) con
  diálogo "Añadir miembro" que ofrece modo Linked o Forked
  (Forked pide proyecto destino).
- `task_01_21` — **Wizard de Proyecto** (`/admin/projects/new`)
  en 2 pasos: pick plantilla → personalizar nombre + descripción.

### Fase E — Doble Kanban + Validación humana (task_01_22 a task_01_23)

- `task_01_22` — **Tablero** (`/admin/board`) con doble Kanban:
  Planes arriba, Tareas abajo. Drag & drop HTML5 nativo con
  actualización optimista vía TanStack Query —
  [ADR 0008](../05-architecture-decisions/0008-dual-kanban-planes-tareas.md).
- `task_01_23` — **Validación humana** (`/admin/approval-policy`)
  con grid de presets, tabla de 13 categorías con toggle/override,
  selector de proyecto + Aplicar política. Substrate: nuevo
  endpoint `GET /approval-policies` (read-only catálogo).

### Fase F — Documentación y cierre (task_01_24 a task_01_27)

- `task_01_24` — [Referencia del modelo de dominio](../04-reference/domain-model.md).
- `task_01_25` — [Guía: Crear tu primer proyecto](../03-guides/01-create-first-project.md).
- `task_01_26` — 3 ADRs nuevos (0006/0007/0008). Total de ADRs
  acumulados en la decisión arquitectónica: 8.
- `task_01_27` — Este changelog.

## UI refresh (lateral al plan)

Durante la implementación de Plan 01 se hizo un pase de modernización
visual sobre el admin-panel: sidebar oscuro tipo Linear/Vercel con
gradient brand mark, hero icons en gradient indigo→violet, KPI cards
en el dashboard, hover-lift en tarjetas interactivas, drawer móvil
debajo de `md`, fade-in al cambiar de página. Documentado en
[`docs/03-guides/design-tokens.md`](../03-guides/design-tokens.md).

## Tests automáticos

| Capa                                             | Cantidad |
| ------------------------------------------------ | -------- |
| Unit + integration (`pytest`)                    | 209      |
| E2E Playwright admin-panel                       | 16       |
| Skipped (esperando Plan 02 — tid claim en login) | 1        |

E2E breakdown:

- `admin-login.spec.ts` × 2
- `agents-catalog.spec.ts` × 3
- `team-detail.spec.ts` × 2 (+1 skipped)
- `project-wizard.spec.ts` × 2
- `dual-kanban.spec.ts` × 3
- `approval-policy.spec.ts` × 4

## Diferencias respecto al alcance original del documento maestro

- **`plans` table en stub.** Plan 01 introduce la tabla y los
  campos pero las tareas se agrupan por `project_id`. El
  orquestador (Plan 02) la activará materializando planes desde el
  Project Manager agent.
- **Selección de tenant.** El login sigue emitiendo JWT sin claim
  `tid` (igual que Plan 00); por eso un puñado de endpoints POST
  no son alcanzables desde el panel sin claim. Cubierto por mocks
  Playwright (`page.route(...)`) en `dual-kanban.spec.ts` y
  `team-detail.spec.ts`. El selector de tenant llega con Plan 02.

## Tests humanos (pendientes)

Los 4 tests humanos del plan (`human_01_01..04`) están listados en
[`docs/roadmap/01-dominio-minimo.md`](../roadmap/01-dominio-minimo.md)
y se ejecutarán como parte de la validación de cierre antes de
mergear `plan/01-dominio-minimo` a `master`.

## Próximo plan

[Plan 02 — Ejecución de Agentes](../roadmap/02-ejecucion-agentes.md):
LangGraph orquestando el grafo DAG de tareas, workers Celery
lanzando runtime templates, primera generación automática de un
plan a partir de un brief.
