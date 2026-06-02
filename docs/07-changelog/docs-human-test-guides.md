---
plan_id: docs-human-test-guides
title: Guías de tests humanos para todos los planes (docs/03-guides/human-tests/)
completed_at: null
docs_language: es
---

# Plan docs-human-test-guides — Guías de tests humanos para todos los planes

## Resumen

El operador pidió "asegúrate de haber creado los scripts para los tests
humanos". `docs/03-guides/human-tests/` ya documentaba **11 planes** (02,
04, 04.5, 05, 06, 06.6–06.9, 06.12, 06.14) pero **faltaba la mayoría**, y
en particular **todos los planes en `pending_human_validation`** que el
operador necesita validar ahora. Este plan **documental** (no de
plataforma) rellena las guías que faltaban y actualiza el índice.

Cada guía nueva sigue exactamente el formato de las existentes (plantilla:
`06.8-rbac-enforcement.md`): título `# Plan <id> — tests humanos`, intro,
nota de **Estado del plan**, bloque **TL;DR** con el setup (referencia a
`scripts/setup_demo_*.py` + `scripts/dev/run-human-tests-*.ps1` cuando
existen; pasos manuales `up.ps1` + URL del admin-panel cuando no), y **una
sección accionable por cada `human_*`** (precondiciones, pasos numerados,
resultado esperado, pass/fail) derivada del bloque "Tests Humanos del
Plan" del roadmap de cada plan. No se inventaron tests fuera de ese bloque.

> **Plan documental, sin cambios de código ni de los planes.** Solo crea
> docs en `docs/03-guides/human-tests/`, esta entrada de changelog y la
> fila del roadmap README. El frontmatter de cada plan no se toca.

## Guías creadas

### Fase A — Planes pendientes (`pending_human_validation`)

- ✅ **`task_htg_01`** — Guías de 07, 08, 09, 09.1:
  - [`07-documentacion-visor.md`](../03-guides/human-tests/07-documentacion-visor.md) (4 tests, `human_07_01..04`)
  - [`08-sso-empresarial.md`](../03-guides/human-tests/08-sso-empresarial.md) (3 tests, `human_08_01..03`)
  - [`09-marketplace.md`](../03-guides/human-tests/09-marketplace.md) (4 tests, `human_09_01..04`)
  - [`09.1-marketplace-seed-publish.md`](../03-guides/human-tests/09.1-marketplace-seed-publish.md) (1 test, `human_09_1_01`)
- ✅ **`task_htg_02`** — Guías de 10, 11, 11.2, 12:
  - [`10-asistente-personal.md`](../03-guides/human-tests/10-asistente-personal.md) (4 tests, `human_10_01..04`)
  - [`11-guardrails-precios.md`](../03-guides/human-tests/11-guardrails-precios.md) (4 tests, `human_11_01..04`)
  - [`11.2-llm-provider-admin-ui.md`](../03-guides/human-tests/11.2-llm-provider-admin-ui.md) (3 tests, `human_11_2_01..03`)
  - [`12-backup-restore.md`](../03-guides/human-tests/12-backup-restore.md) (4 tests, `human_12_01..04`)
- ✅ **`task_htg_03`** — Guías de 13, 14, 15, 16:
  - [`13-api-publica-webhooks.md`](../03-guides/human-tests/13-api-publica-webhooks.md) (4 tests, `human_13_01..04`)
  - [`14-evals-estadisticas.md`](../03-guides/human-tests/14-evals-estadisticas.md) (4 tests, `human_14_01..04`)
  - [`15-instalador-produccion.md`](../03-guides/human-tests/15-instalador-produccion.md) (5 tests, `human_15_01..05`)
  - [`16-human-agents.md`](../03-guides/human-tests/16-human-agents.md) (6 tests, `human_16_01..06`)
- ✅ **`task_htg_04`** — Guías de 06.15, 06.16, demo-webscorpo:
  - [`06.15-agent-tools-assignment-ui.md`](../03-guides/human-tests/06.15-agent-tools-assignment-ui.md) (2 tests, `human_06_15_01..02`)
  - [`06.16-polyglot-tool-catalog.md`](../03-guides/human-tests/06.16-polyglot-tool-catalog.md) (1 test, `human_06_16_01`)
  - [`demo-webscorpo-team-kb.md`](../03-guides/human-tests/demo-webscorpo-team-kb.md) (1 test, `human_demo_ws_01`; incluye cómo correr `scripts/setup_webscorpo.py`)

### Fase B — Completitud (`completed` sin guía) + índice

- ✅ **`task_htg_05`** — Guías de planes completed sin guía:
  - [`00-fundaciones.md`](../03-guides/human-tests/00-fundaciones.md) (5 tests, `human_00_01..05`)
  - [`01-dominio-minimo.md`](../03-guides/human-tests/01-dominio-minimo.md) (4 tests, `human_01_01..04`)
  - [`03-chat-planning-aprobacion.md`](../03-guides/human-tests/03-chat-planning-aprobacion.md) (5 tests, `human_03_01..05`)
  - [`06.10-kb-categories.md`](../03-guides/human-tests/06.10-kb-categories.md) (4 tests, `human_06_10_01..04`)
  - [`06.11-kb-ingestion-fixes.md`](../03-guides/human-tests/06.11-kb-ingestion-fixes.md) (4 tests, `human_06_11_01..04`)
  - [`06.13-kb-catalog-content.md`](../03-guides/human-tests/06.13-kb-catalog-content.md) (2 tests, `human_06_13_01..02`)
- ✅ **`task_htg_06`** — Índice + changelog (esta tarea):
  - Actualizado [`docs/03-guides/human-tests/README.md`](../03-guides/human-tests/README.md): una fila por cada guía (00–16, 06.10–06.16, 09.1, 11.2, demo-webscorpo), ordenadas por número de plan, sin enlaces rotos.
  - Creada esta entrada de changelog.
  - Añadida la fila `docs-human-test-guides` a [`docs/roadmap/README.md`](../roadmap/README.md).

## Cobertura del índice

Tras este plan, `docs/03-guides/human-tests/README.md` enlaza **33
guías** (todas las de los planes con bloque "Tests Humanos del Plan" más el
seed demo-webscorpo). Plan 06.5 no tiene tests humanos propios (reusa los
del Plan 06) — sigue marcado como "0 propios" en el índice, sin guía.

## Setup scripts referenciados (verificados)

Las guías referencian solo scripts que existen en `scripts/`:
`setup_demo_04_5.py`, `setup_demo_05.py`, `setup_demo_06.py`,
`setup_demo_06_6_7.py`, `setup_demo_06_8.py`, `setup_demo_06_9.py`,
`setup_demo_project.py`, `setup_webscorpo.py`, y los launchers
`scripts/dev/run-human-tests-{05,06,06.8,06.9}.ps1`. Donde un plan no tiene
script propio, la guía da pasos manuales (`.\scripts\dev\up.ps1` + abrir la
URL del admin-panel correspondiente). No se crearon launchers `.ps1`
nuevos (fuera de alcance).

## Verificación

- `pre-commit run --files <cambiados>` (prettier/markdown/trailing-whitespace)
  ✅ por tarea.
- `test -f` de cada guía objetivo + índice/changelog/roadmap README ✅.
- Índice README sin enlaces rotos (rutas relativas a archivos existentes).

## Pendiente

- **Test humano del plan** (`human_htg_01`) — pendiente de validar por un
  humano: comprobar que hay una guía por cada plan con bloque de tests
  humanos, que cada guía lista precondiciones + pasos + resultado esperado
  por cada `human_*`, que el índice README enlaza todo sin enlaces rotos, y
  que las guías referencian los setup scripts correctos donde existen.
  Checklist en `docs/roadmap/docs-human-test-guides.md`.
- **Merge del PR `plan/docs-human-test-guides` a `master`** — lo gestiona
  el humano. El plan queda en `in_progress`, NO en `completed`.

## PR

Pendiente de apertura/merge a `master` (lo gestiona el humano).
