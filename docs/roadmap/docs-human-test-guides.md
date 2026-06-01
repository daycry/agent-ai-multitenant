---
plan_id: docs-human-test-guides
title: Guías de tests humanos para todos los planes (docs/03-guides/human-tests/)
status: in_progress
blocking_plan: []
started_at: 2026-06-01
completed_at: null
estimated_duration_calendar: 2-3 días
estimated_effort_person_days: 2
estimated_cost_human_eur: 800 € – 1.500 €
estimated_cost_ai_eur: 30 € – 70 €
created_by: technical_writer
spec_sections_referenced: [8.8]
docs_language: es
---

# Plan docs-human-test-guides — Guías de tests humanos para todos los planes

> **Nota:** plan documental, no de plataforma. Materializa lo que el operador pidió: "asegúrate de haber creado
> los scripts para los tests humanos". `plan_id` descriptivo, sin número de fase.

## Cabecera

| Campo           | Valor                         |
| --------------- | ----------------------------- |
| **ID del Plan** | `docs-human-test-guides`      |
| **Rama git**    | `plan/docs-human-test-guides` |

## Resumen

Cada plan del roadmap define en su sección "Tests Humanos del Plan" una lista `human_*` (escenarios end-to-end que
un revisor humano valida antes de pasar el plan de `pending_human_validation` a `completed`). `docs/03-guides/
human-tests/` ya documenta **11 planes** (02, 04, 04.5, 05, 06, 06.6–06.9, 06.12, 06.14). **Faltan el resto**, y
en particular **todos los planes en `pending_human_validation`** que el operador necesita validar ahora (07–16,
09.1, 11.2, 06.15, 06.16, demo-webscorpo, 15). Este plan **rellena las guías que faltan**, con el mismo formato
que las existentes (intro + estado + TL;DR con setup/launcher + checklists accionables con precondiciones y
resultado esperado), y actualiza el índice.

## Alcance

**Entra** — crear las guías que faltan en `docs/03-guides/human-tests/<plan>.md` (formato de las existentes):

- **Prioridad (pending_human_validation)**: 07, 08, 09, 09.1, 10, 11, 11.2, 12, 13, 14, 15, 16, 06.15, 06.16,
  demo-webscorpo.
- **Completitud (completed sin guía)**: 00, 01, 03, 06.10, 06.11, 06.13.
- Cada guía deriva del bloque "Tests Humanos del Plan" del roadmap del plan: intro + "Estado del plan" + TL;DR
  (referencia al `scripts/setup_demo_*.py` / launcher si existe, si no pasos manuales de setup) + un checklist
  accionable por cada `human_*` (precondiciones, pasos, resultado esperado, pass/fail).
- Actualizar el índice `docs/03-guides/human-tests/README.md` con las filas nuevas + changelog + fila en roadmap README.

**Queda fuera**:

- Crear launchers `.ps1` nuevos por plan (se referencian los existentes; no se inventan).
- Ejecutar los tests humanos (eso lo hace el humano).
- Cambiar el contenido de los planes / código.

## Decisiones clave

- **No reinventar**: el formato y el índice ya existen; las nuevas guías los siguen exactamente (mirar 06.8 como
  plantilla). Reutilizan los `scripts/setup_demo_*.py` que ya existen.
- **Prioridad a lo pendiente**: las guías de los planes `pending_human_validation` son las accionables (el operador
  está a punto de validarlos). Las de planes `completed` se añaden por completitud histórica.

## Tareas

### Fase A — Guías de planes pendientes

#### `task_htg_01` — Guías 07, 08, 09, 09.1

- [x] **Título**: Crear `docs/03-guides/human-tests/{07-documentacion-visor,08-sso-empresarial,09-marketplace,09.1-marketplace-seed-publish}.md` desde sus bloques de tests humanos, formato de las existentes.
- **Tests**: `test -f` de las 4 guías + pre-commit prettier verde

#### `task_htg_02` — Guías 10, 11, 11.2, 12

- [x] **Título**: Crear las guías de 10-asistente-personal, 11-guardrails-precios, 11.2-llm-provider-admin-ui, 12-backup-restore.
- **Tests**: `test -f` de las 4 guías + pre-commit prettier verde

#### `task_htg_03` — Guías 13, 14, 15, 16

- [x] **Título**: Crear las guías de 13-api-publica-webhooks, 14-evals-estadisticas, 15-instalador-produccion, 16-human-agents.
- **Tests**: `test -f` de las 4 guías + pre-commit prettier verde

#### `task_htg_04` — Guías 06.15, 06.16, demo-webscorpo

- [x] **Título**: Crear las guías de 06.15-agent-tools-assignment-ui, 06.16-polyglot-tool-catalog, demo-webscorpo-team-kb (incl. cómo correr `scripts/setup_webscorpo.py`).
- **Tests**: `test -f` de las 3 guías + pre-commit prettier verde

### Fase B — Completitud + índice

#### `task_htg_05` — Guías 00, 01, 03, 06.10, 06.11, 06.13

- [x] **Título**: Crear las guías de los planes completed que aún no la tienen (00-fundaciones, 01-dominio-minimo, 03-chat-planning-aprobacion, 06.10-kb-categories, 06.11-kb-ingestion-fixes, 06.13-kb-catalog-content).
- **Tests**: `test -f` de las 6 guías + pre-commit prettier verde

#### `task_htg_06` — Índice + changelog

- [x] **Título**: Actualizar `docs/03-guides/human-tests/README.md` (todas las filas nuevas, ordenadas) + crear changelog `docs/07-changelog/docs-human-test-guides.md` + fila en `docs/roadmap/README.md`.
- **Tests**: README contiene las nuevas filas; `test -f` del changelog

## Tests humanos del Plan

```yaml
- id: human_htg_01
  description: "Las guías de tests humanos están completas y son usables"
  checklist:
    - "docs/03-guides/human-tests/ tiene una guía por cada plan con bloque de tests humanos"
    - "Cada guía pendiente lista precondiciones + pasos + resultado esperado por cada human_*"
    - "El índice README enlaza todas las guías sin enlaces rotos"
    - "Las guías referencian los setup scripts correctos donde existen"
```

## Criterios de cierre

1. Todas las tareas `[x]`; todas las guías objetivo creadas.
2. `pre-commit run --all-files` (prettier/markdown) verde.
3. Índice README sin enlaces rotos.
4. Test humano validado.
5. Changelog + fila en roadmap README.
6. PR de `plan/docs-human-test-guides` mergeado (lo hace el humano).

## Próximo Plan

Tras este: modernización UI + refactor, y actualización integral de documentación.
