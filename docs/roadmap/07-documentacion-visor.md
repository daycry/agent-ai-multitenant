---
plan_id: 07-documentacion-visor
title: Documentación Estructurada y Visor Cross-Proyecto
status: pending_approval
blocking_plan: [06-testing-revision-git]
started_at: null
completed_at: null
estimated_duration_calendar: 3 semanas
estimated_effort_person_days: 55-65
estimated_cost_human_eur: 22.000 € – 26.000 €
estimated_cost_ai_eur: 100 € – 160 €
created_by: system_architect
spec_sections_referenced: [15, 16]
docs_language: es
---

# Plan 07 — Documentación Estructurada y Visor Cross-Proyecto

## Cabecera

| Campo                              | Valor                                     |
| ---------------------------------- | ----------------------------------------- |
| **ID del Plan**                    | `07-documentacion-visor`                  |
| **Estado**                         | `pending_approval`                        |
| **Bloqueado por**                  | `06-testing-revision-git`                 |
| **Tiempo estimado (calendario)**   | 3 semanas                                 |
| **Tiempo estimado (persona-días)** | 55-65                                     |
| **Previsión de coste — humano**    | 22.000 € – 26.000 € (tarifa media 50 €/h) |
| **Previsión de coste — IA**        | 100 € – 160 €                             |
| **Aprobador propuesto**            | System Admin                              |
| **Rama git**                       | `plan/07-documentacion-visor`             |
| **Secciones del .docx**            | [15, 16]                                  |

---

## Descripción Detallada

### Resumen Ejecutivo

Estructura canónica /docs en 7 carpetas obligatorias enforced por guardrails. Technical Writer agente mantiene docs al cierre de cada plan. Visor cross-proyecto solo lectura con búsqueda full-text y semántica.

### Contexto

La documentación es entregable del plan, no afterthought. Esta fase la institucionaliza con estructura, generación automática y un visor que la hace cross-proyecto navegable.

### Alcance

**Entra en este plan**:

- Estructura canónica /docs/ con 7 carpetas obligatorias.
- Lint estructural enforced por guardrail (rechaza PR si no se respetan reglas).
- Lint de Markdown (frontmatter, headers, enlaces internos, language tags).
- Generación automática de entradas /docs/07-changelog/ al cierre de plan.
- ADRs numerados secuencialmente generables desde plan.
- Rol Technical Writer agente con responsabilidad explícita post-plan.
- Sincronización /docs con KB interna del proyecto (kb_internal_docs) al mergear PR.
- Visor cross-proyecto: sidebar árbol, renderizado Markdown + Mermaid, búsqueda full-text + semántica, filtros, exportación.
- Vista de diff entre versiones de docs basada en commits Git.
- Permisos RBAC respetados en el visor.
- Idioma configurable por proyecto (es/en).

**Queda fuera (otras fases)**:

- Edición de docs desde el visor (queda para iteración posterior, MVP solo lectura).
- Translation automática es↔en (configuración manual del idioma por proyecto).

### Decisiones Clave

- Estructura Diátaxis adaptada: 7 carpetas numeradas (no las 4 originales) para que escale a proyectos grandes.
- Visor lee directo del filesystem persistente (gracias a Fase 6: worktrees), no replica almacenamiento.
- Renderizado al vuelo con react-markdown + remark-mermaid + rehype-highlight. Cache de HTML para docs pesados.

### Riesgos Identificados

| Riesgo                                               | Probabilidad | Impacto | Mitigación                                                                 |
| ---------------------------------------------------- | ------------ | ------- | -------------------------------------------------------------------------- |
| Technical Writer agente produce docs de baja calidad | Media        | Medio   | Validación con guardrails + revisión humana del plan incluye revisar docs. |
| Visor lento con miles de docs                        | Baja         | Medio   | Indexación incremental + paginación + lazy load.                           |

---

## Tareas

> Cada tarea con checkbox, descripción, tiempo estimado, complejidad, rol sugerido, dependencias entre tareas y tests automáticos en el runtime correspondiente. Los tests humanos a nivel de plan están al final del documento.

### Fase A — Estructura Canónica y Lint

#### `task_07_01` — Script de bootstrap que crea las 7 carpetas obligatorias en un repo nuevo

- [x] **Título**: Script de bootstrap que crea las 7 carpetas obligatorias en un repo nuevo
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_07_01_a
    description: "Script de bootstrap que crea las 7 carpetas obligatorias en un repo nuevo"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_bootstrap_docs.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_02` — Guardrail estructural: valida estructura al hacer push y bloquea PR si faltan carpetas

- [x] **Título**: Guardrail estructural: valida estructura al hacer push y bloquea PR si faltan carpetas
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + security
- **Dependencias**: `task_07_01`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_02_a
    description: "Guardrail estructural: valida estructura al hacer push y bloquea PR si faltan carpetas"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_docs_structure_guardrail.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_03` — Lint de Markdown (frontmatter, headers, enlaces, language tags) como check de CI

- [x] **Título**: Lint de Markdown (frontmatter, headers, enlaces, language tags) como check de CI
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: devops
- **Dependencias**: `task_07_02`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_03_a
    description: "Lint de Markdown (frontmatter, headers, enlaces, language tags) como check de CI"
    check_type: automated
    runtime: generic-shell
    command: 'npx markdownlint-cli --config .markdownlint.jsonc "docs/**/*.md"'
    expected_signal: "exit_code == 0"
  ```

#### `task_07_04` — Validador de idioma: detecta si un .md está en idioma distinto al declarado

- [x] **Título**: Validador de idioma: detecta si un .md está en idioma distinto al declarado
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_07_03`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_04_a
    description: "Validador de idioma: detecta si un .md está en idioma distinto al declarado"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_language_detector.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase B — Technical Writer Agente y Generación Automática

#### `task_07_05` — Agente Technical Writer con system_prompt curado y skills específicas

- [x] **Título**: Agente Technical Writer con system_prompt curado y skills específicas
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_07_05_a
    description: "Agente Technical Writer con system_prompt curado y skills específicas"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_tech_writer_agent.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_06` — Workflow automático al cierre del plan: el Technical Writer genera changelog + ADRs si aplica + updates a reference

- [ ] **Título**: Workflow automático al cierre del plan: el Technical Writer genera changelog + ADRs si aplica + updates a reference
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_07_05`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_06_a
    description: "Workflow automático al cierre del plan: el Technical Writer genera changelog + ADRs si aplica + updates a reference"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_docs_generation_post_plan.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_07` — Plantilla canónica de changelog por plan (frontmatter + resumen + tareas + decisiones + PR link)

- [x] **Título**: Plantilla canónica de changelog por plan (frontmatter + resumen + tareas + decisiones + PR link)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_07_06`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_07_a
    description: "Plantilla canónica de changelog por plan (frontmatter + resumen + tareas + decisiones + PR link)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_changelog_template.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_08` — Plantilla canónica de ADR numerado secuencialmente

- [x] **Título**: Plantilla canónica de ADR numerado secuencialmente
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: ai-engineer
- **Dependencias**: `task_07_07`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_08_a
    description: "Plantilla canónica de ADR numerado secuencialmente"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/unit/test_adr_template.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase C — Indexación y KB Interna

#### `task_07_09` — Sincronización /docs ↔ kb_internal_docs al mergear PR (webhook Git)

- [ ] **Título**: Sincronización /docs ↔ kb_internal_docs al mergear PR (webhook Git)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_07_09_a
    description: "Sincronización /docs ↔ kb_internal_docs al mergear PR (webhook Git)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_docs_kb_sync.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_10` — Reindexación incremental (solo los .md cambiados desde el último commit)

- [ ] **Título**: Reindexación incremental (solo los .md cambiados desde el último commit)
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev + ai-engineer
- **Dependencias**: `task_07_09`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_10_a
    description: "Reindexación incremental (solo los .md cambiados desde el último commit)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_incremental_reindex.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase D — Visor Cross-Proyecto

#### `task_07_11` — UI Next.js en /docs del tenant con sidebar árbol de proyectos → carpetas → archivos

- [ ] **Título**: UI Next.js en /docs del tenant con sidebar árbol de proyectos → carpetas → archivos
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_07_11_a
    description: "UI Next.js en /docs del tenant con sidebar árbol de proyectos → carpetas → archivos"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/docs-viewer-sidebar.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_12` — Renderizado Markdown con react-markdown + remark-mermaid + rehype-highlight + tabla de contenidos autogenerada

- [ ] **Título**: Renderizado Markdown con react-markdown + remark-mermaid + rehype-highlight + tabla de contenidos autogenerada
- **Tiempo estimado**: 12 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_07_11`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_12_a
    description: "Renderizado Markdown con react-markdown + remark-mermaid + rehype-highlight + tabla de contenidos autogenerada"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/docs-viewer-render.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_13` — Búsqueda full-text instantánea con resultados rankeados y snippets

- [ ] **Título**: Búsqueda full-text instantánea con resultados rankeados y snippets
- **Tiempo estimado**: 10 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_07_12`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_13_a
    description: "Búsqueda full-text instantánea con resultados rankeados y snippets"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/docs-viewer-search.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_14` — Búsqueda semántica sobre kb_internal_docs

- [ ] **Título**: Búsqueda semántica sobre kb_internal_docs
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_07_13`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_14_a
    description: "Búsqueda semántica sobre kb_internal_docs"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_docs_semantic_search.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_15` — Filtros (por proyecto, tipo de doc, fecha, autor) y bookmarks

- [ ] **Título**: Filtros (por proyecto, tipo de doc, fecha, autor) y bookmarks
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev
- **Dependencias**: `task_07_14`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_15_a
    description: "Filtros (por proyecto, tipo de doc, fecha, autor) y bookmarks"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/docs-viewer-filters.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_16` — Vista de diff entre versiones (basado en commits Git)

- [ ] **Título**: Vista de diff entre versiones (basado en commits Git)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: frontend-dev + backend-dev
- **Dependencias**: `task_07_15`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_16_a
    description: "Vista de diff entre versiones (basado en commits Git)"
    check_type: automated
    runtime: node-playwright
    command: "npx playwright test e2e/docs-viewer-diff.spec.ts"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_17` — Exportación PDF / ZIP

- [ ] **Título**: Exportación PDF / ZIP
- **Tiempo estimado**: 6 h
- **Complejidad**: m
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_07_16`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_17_a
    description: "Exportación PDF / ZIP"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_docs_export.py -v"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_18` — Permisos RBAC respetados (filtro por proyecto accesible al usuario)

- [ ] **Título**: Permisos RBAC respetados (filtro por proyecto accesible al usuario)
- **Tiempo estimado**: 4 h
- **Complejidad**: s
- **Rol sugerido**: backend-dev
- **Dependencias**: `task_07_17`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_18_a
    description: "Permisos RBAC respetados (filtro por proyecto accesible al usuario)"
    check_type: automated
    runtime: python-pytest
    command: "pytest tests/integration/test_docs_rbac.py -v"
    expected_signal: "exit_code == 0"
  ```

### Fase E — Cierre

#### `task_07_19` — Documentación interna del propio sistema usando su propia estructura (eat your own dog food)

- [ ] **Título**: Documentación interna del propio sistema usando su propia estructura (eat your own dog food)
- **Tiempo estimado**: 8 h
- **Complejidad**: m
- **Rol sugerido**: technical-writer
- **Dependencias**: ninguna (primera tarea de la fase)
- **Tests automáticos**:
  ```yaml
  - id: auto_07_19_a
    description: "Documentación interna del propio sistema usando su propia estructura (eat your own dog food)"
    check_type: automated
    runtime: generic-shell
    command: "ls docs/01-overview docs/02-getting-started docs/03-guides docs/04-reference docs/05-architecture-decisions docs/06-runbooks docs/07-changelog | wc -l | awk '$1>=20 {exit 0} {exit 1}'"
    expected_signal: "exit_code == 0"
  ```

#### `task_07_20` — Changelog del plan

- [ ] **Título**: Changelog del plan
- **Tiempo estimado**: 2 h
- **Complejidad**: xs
- **Rol sugerido**: technical-writer
- **Dependencias**: `task_07_19`
- **Tests automáticos**:
  ```yaml
  - id: auto_07_20_a
    description: "Changelog del plan"
    check_type: automated
    runtime: generic-shell
    command: "test -f docs/07-changelog/07-documentacion-visor.md"
    expected_signal: "exit_code == 0"
  ```

---

## Tests Humanos del Plan

Tests que se ejecutan UNA sola vez al finalizar todas las tareas del plan, cuando el plan está en estado `pending_human_validation`. Cubren validación integral del resultado del plan que no se puede automatizar.

```yaml
- id: human_07_01
  description: "Documentación se genera automáticamente al cierre de plan"
  hint: "Cerrar un plan y verificar que /docs se actualiza"
  checklist:
    - "Entrada /docs/07-changelog/{plan_id}.md generada con cabecera + resumen + tareas"
    - "Si el plan tomó decisiones nuevas, ADR generado en /docs/05-architecture-decisions/"
    - "Si el plan tocó APIs o schemas, /docs/04-reference/ actualizado"
    - "Todo en el idioma configurado del proyecto"

- id: human_07_02
  description: "Guardrail estructural bloquea PR malformados"
  hint: "Intentar mergear PR que borra una de las 7 carpetas obligatorias"
  checklist:
    - "El sistema bloquea el merge con error claro"
    - "El feedback se muestra al equipo en el chat o como comentario en el PR"

- id: human_07_03
  description: "Visor funciona con tenant grande"
  hint: "Tenant con 5 proyectos, ~200 docs en total"
  checklist:
    - "Navegación fluida en el sidebar"
    - "Render de un .md complejo con Mermaid en menos de 2s"
    - "Búsqueda full-text devuelve resultados en menos de 500ms"
    - "Búsqueda semántica devuelve resultados en menos de 1s"

- id: human_07_04
  description: "Permisos respetados en el visor"
  hint: "Usuario con acceso solo a Proyecto A"
  checklist:
    - "El sidebar solo muestra Proyecto A"
    - "Búsqueda no devuelve resultados de Proyecto B aunque haya match"
    - "Si conoce la URL directa de un .md de Proyecto B, recibe 403"
```

---

## Criterios de Cierre del Plan

El plan se cierra como `completed` cuando se cumplen TODOS estos criterios:

1. ✅ Todas las tareas están en estado `done`.
2. ✅ Todos los tests automáticos de las tareas están en `pass`.
3. ✅ Todos los `human_*` están marcados como `pass` por el revisor humano.
4. ✅ CI verde en `main`.
5. ✅ Generada entrada en `/docs/07-changelog/{plan_id}.md`.
6. ✅ PR del plan abierto y mergeado a `main`.

## Próximo Plan

Tras cerrar este plan, el siguiente es **Plan 08** (`08-sso-empresarial.md`).
