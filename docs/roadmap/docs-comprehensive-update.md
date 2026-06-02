---
plan_id: docs-comprehensive-update
title: Actualización integral de la documentación (arquitectura, guías, referencia, todo)
status: in_progress
blocking_plan: []
started_at: 2026-06-02
completed_at: null
estimated_duration_calendar: 3-4 días
estimated_effort_person_days: 3
estimated_cost_human_eur: 1.200 € – 2.000 €
estimated_cost_ai_eur: 40 € – 90 €
created_by: technical_writer
spec_sections_referenced: [8.8]
docs_language: es
---

# Plan docs-comprehensive-update — Documentación integral al día

> Plan documental, el último del backlog. `plan_id` descriptivo. Materializa "que toda la documentación esté
> actualizada al detalle: arquitectura, guías, todo".

## Cabecera

| Campo           | Valor                            |
| --------------- | -------------------------------- |
| **ID del Plan** | `docs-comprehensive-update`      |
| **Rama git**    | `plan/docs-comprehensive-update` |

## Resumen

Cada plan dejó sus docs (changelogs, ADRs, guías, referencia, runbooks — las 7 carpetas están pobladas: 47 ADRs,
37 changelogs, 20 guías, 17 runbooks…). Lo que falta es la **capa transversal/holística**: los docs de
`docs/context/` (architecture-overview, glossary, tech-stack, conventions) **están desfasados** (apenas mencionan
los subsistemas nuevos: human agents, proveedores LLM, marketplace, guardrails, presupuestos/FX, webhooks, evals,
comandos por proyecto…), faltan **diagramas Mermaid** del sistema final, y hay **gotchas sin documentar** que
surgieron en esta tanda. Este plan hace la **pasada integral**: refresca lo transversal, añade diagramas, asegura
coherencia + cross-links en las 7 carpetas, documenta los gotchas nuevos, y verifica que el visor de docs lo
refleja.

## Alcance

**Entra**:

- **Context transversal** (`docs/context/`): reescribir `architecture-overview.md` para reflejar el sistema final
  end-to-end (todos los subsistemas + topología de contenedores), `glossary.md` (términos nuevos: Human Agent,
  HumanWorkSession, review modes, llm_providers, allowed_commands, runtime templates, marketplace listing/trust,
  budget/FX, guardrails, webhooks, evals…), `tech-stack.md` y `conventions.md` (al día). **Diagramas Mermaid** del
  sistema final (arquitectura de componentes + flujo de un plan + topología multi-tenant).
- **01-overview + 04-reference**: que el overview de producto liste todas las capacidades; que la referencia
  (domain-model, rbac, índices) refleje lo construido (human agents, llm_providers, allowed_commands/runtime,
  marketplace, budgets…). Cross-links.
- **Gotchas** (`docs/03-guides/gotchas/`): documentar los nuevos — (1) prettier `--all-files` crashea por libuv en
  Windows (`UV_HANDLE_CLOSING`) → usar prettier _scoped_; (2) revision-id de Alembic ≤32 chars
  (`alembic_version.version_num` varchar(32)); (3) volumen dev de MinIO con `xl meta version 3` incompatible con la
  imagen fijada (downgrade) → recrear el volumen dev o subir el pin. (Buscar primero; no duplicar.)
- **Coherencia + índices**: READMEs/índices de cada carpeta al día, sin enlaces rotos; el visor `/admin/docs` lee
  `docs/` así que se actualiza solo, pero verificar que el índice/categorías lo recogen.
- Changelog + verificación (enlaces, estructura, prettier scoped).

**Queda fuera**:

- Reescribir los changelogs/ADRs/guías por-plan ya existentes (están al día; solo se cross-linkan).
- Cambiar código (salvo, si hiciera falta, el índice del visor de docs — pero lee la carpeta, no debería).

## Decisiones clave

- **Holístico, no redundante**: se refresca lo transversal + se cierra coherencia; no se re-escribe lo que cada
  plan ya documentó.
- **Diagramas como código** (Mermaid en Markdown, per convención).
- **ES**, frontmatter YAML, estructura canónica de 7 carpetas.

## Tareas

### Fase A — Context transversal + diagramas

#### `task_doc_01` — architecture-overview + glossary + tech-stack + conventions + Mermaid

- [x] **Título**: Reescribir `docs/context/architecture-overview.md` (sistema final end-to-end + topología) con
      diagramas Mermaid (componentes, flujo de un plan, multi-tenancy); actualizar `glossary.md` (todos los términos
      nuevos), `tech-stack.md`, `conventions.md`. Cross-link a los ADRs/guías relevantes.
- **Tests**: `test -f` + los 4 ficheros mencionan los subsistemas nuevos; bloques Mermaid presentes

### Fase B — Overview + referencia

#### `task_doc_02` — 01-overview + 04-reference al día + cross-links

- [ ] **Título**: Que `docs/01-overview/` liste todas las capacidades del producto final; que `docs/04-reference/`
      (domain-model, rbac, índices) refleje human agents, llm_providers, allowed_commands/runtime, marketplace,
      budgets/FX, webhooks, evals; índices sin enlaces rotos; cross-links.
- **Tests**: `pytest`/grep: la referencia menciona los modelos/endpoints nuevos; índices sin enlaces rotos

### Fase C — Gotchas + coherencia

#### `task_doc_03` — Gotchas nuevos + índices de carpeta + visor

- [ ] **Título**: Añadir los gotchas (prettier/libuv `--all-files`; alembic rev-id ≤32; MinIO xl-meta volumen) a
      `docs/03-guides/gotchas/` (síntoma + causa + fix, sin duplicar) + actualizar su README/índice; revisar
      READMEs/índices de las 7 carpetas (sin enlaces rotos); confirmar que el visor `/admin/docs` recoge las
      categorías/nuevos docs (lee la carpeta).
- **Tests**: `test -f` de los gotchas nuevos; índices sin enlaces rotos

### Fase D — Changelog + verificación

#### `task_doc_04` — Changelog + verificación final de enlaces

- [ ] **Título**: Changelog `docs/07-changelog/docs-comprehensive-update.md`; fila en roadmap README; verificación
      global de enlaces internos de `docs/` (sin rotos) + prettier scoped sobre los docs tocados.
- **Tests**: `test -f` changelog; verificación de enlaces internos `docs/**/*.md` sin rotos

## Tests humanos del Plan

```yaml
- id: human_doc_01
  description: "La documentación refleja el sistema final"
  checklist:
    - "architecture-overview describe todos los subsistemas (incl. human agents, providers, marketplace, guardrails, budgets, webhooks, evals) con diagramas Mermaid que renderizan"
    - "El glosario tiene los términos nuevos; la referencia (domain-model/rbac) refleja lo construido"
    - "Los gotchas nuevos están documentados (prettier/libuv, alembic rev-id, MinIO volumen)"
    - "El visor /admin/docs muestra las categorías y docs actualizados"
    - "No hay enlaces internos rotos en docs/"
```

## Criterios de cierre

1. Tareas `[x]`; verificación de enlaces internos de `docs/` sin rotos.
2. `pre-commit` (prettier scoped sobre los docs tocados) verde.
3. Diagramas Mermaid presentes + válidos.
4. Test humano validado.
5. Changelog + fila en README.
6. PR de `plan/docs-comprehensive-update` mergeado (lo hace el humano).

## Próximo Plan

Ninguno: este es el último del backlog actual. El sistema queda construido (pending_human_validation) +
documentado. Pendiente del humano: validar tests humanos + mergear las ramas + (Plan 15) pentest externo + release v1.0.0.
