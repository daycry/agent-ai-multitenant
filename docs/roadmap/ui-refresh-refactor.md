---
plan_id: ui-refresh-refactor
title: Refresh visual moderado + refactor del admin-panel
status: in_progress
blocking_plan: []
started_at: 2026-06-01
completed_at: null
estimated_duration_calendar: 4-6 días
estimated_effort_person_days: 5
estimated_cost_human_eur: 2.000 € – 3.500 €
estimated_cost_ai_eur: 50 € – 110 €
created_by: frontend_lead
spec_sections_referenced: [33]
docs_language: es
---

# Plan ui-refresh-refactor — Refresh visual moderado + refactor del admin-panel

> **Nota:** plan de frontend, no de plataforma. `plan_id` descriptivo. Decisión del operador: **refresh visual
> moderado** (no rediseño atrevido).

## Cabecera

| Campo           | Valor                      |
| --------------- | -------------------------- |
| **ID del Plan** | `ui-refresh-refactor`      |
| **Rama git**    | `plan/ui-refresh-refactor` |

## Resumen

El admin-panel (52 páginas) ya tiene un design-system decente (badge/button/card/dialog/input/tabs/comboboxes/
role-guard/spinner + shell/header/breadcrumb/error-boundary/page-header) sobre un tema oscuro violeta con CSS
vars. Este plan hace un **refresh visual moderado + refactor**: refina los tokens (spacing/tipografía/radius/
sombras/paleta) de forma centralizada, añade las primitivas que faltan, extrae componentes compartidos (anti-
duplicación) y aplica la capa visual renovada + consistencia + accesibilidad a las páginas principales —
**preservando comportamiento, rutas, llamadas API y TODOS los `data-testid`** (de los que dependen los e2e).

## Alcance

**Entra**:

- **Tokens + primitivas**: refinar `globals.css` + `tailwind.config.ts` (spacing, tipografía, radius, sombras,
  ajustes sutiles de paleta — sin romper contraste/accesibilidad ni los `-soft`); añadir primitivas que faltan
  (Checkbox, Select, EmptyState, Skeleton, Table) y migrar los elementos nativos sueltos a ellas.
- **Refactor**: extraer patrones recurrentes a componentes compartidos (toolbar de lista + buscador,
  FormSection, StateBlock para vacío/cargando/error, DataTable wrapper) y adoptarlos para reducir duplicación.
- **Refresh + consistencia + a11y** en las páginas principales/nuevas: dashboard, agents, projects, settings,
  /admin/llm-providers, tools del agente, marketplace (+private), comandos del proyecto, human inbox/gallery,
  model-prices, guardrails, eval-quality, tenant-stats. Estados vacío/cargando/error consistentes, foco/labels/
  aria, spacing y micro-interacciones modernas.
- Verificación (typecheck/lint/build) + guía de convenciones UI + changelog.

**Queda fuera (GUARDRAILS DUROS)**:

- **NO** cambiar comportamiento, rutas, llamadas API, props públicas, ni la lógica de datos (TanStack Query,
  mutaciones). Es puramente presentacional/estructural.
- **NO** eliminar ni renombrar `data-testid` existentes (rompería los e2e). Preservarlos todos.
- **NO** rediseño atrevido (el operador eligió "moderado"); el tema oscuro violeta se refina, no se reemplaza.
- **NO** tocar el backend.

## Decisiones clave

- **Behavior-preserving**: cada cambio es de presentación; los tests existentes (typecheck/lint/build + e2e por
  selector) deben seguir verdes. Se verifica build tras cada fase.
- **Refresh centralizado**: la mayor parte del "más moderno" sale de refinar los tokens (un punto), no de tocar
  52 páginas a mano. Las páginas solo adoptan primitivas/componentes nuevos donde aporta.
- **Refactor con medida**: extraer solo patrones realmente repetidos; no sobre-abstraer.

## Tareas

### Fase A — Tokens + primitivas

#### `task_ui_01` — Refinar tokens + añadir primitivas

- [ ] **Título**: Refinar `app/globals.css` + `tailwind.config.ts` (spacing/tipografía/radius/sombras + ajustes
      sutiles de paleta, manteniendo contraste AA y los `-soft`); añadir primitivas `components/ui/{checkbox,select,
    empty-state,skeleton,table}.tsx` consistentes con las existentes. Sin cambiar el contrato de las primitivas
      actuales.
- **Tests**: `npm run typecheck && lint && build` verde

### Fase B — Refactor de componentes compartidos

#### `task_ui_02` — Extraer componentes compartidos + adoptar (piloto)

- [ ] **Título**: Extraer `components/shared/{list-toolbar,form-section,state-block,data-table}.tsx` (patrones
      recurrentes: cabecera de lista + buscador, sección de formulario, bloque vacío/cargando/error, tabla) y
      adoptarlos en 2-3 páginas piloto para probar el patrón + reducir duplicación. Preservar `data-testid`.
- **Tests**: `npm run typecheck && lint && build` verde

### Fase C — Refresh + consistencia en flujos clave

#### `task_ui_03` — Refresh batch 1 (núcleo + nuevos)

- [ ] **Título**: Aplicar la capa visual + estados consistentes + a11y + primitivas/componentes nuevos a:
      dashboard, agents (+[id]), projects (+[id]), settings, /admin/llm-providers, sección Tools del agente.
      Migrar checkbox/select nativos a las primitivas. Preservar `data-testid`, rutas y llamadas. _(e2e existentes
      siguen verdes por selector)._
- **Tests**: `npm run typecheck && lint && build` verde + grep de `data-testid` preservados

#### `task_ui_04` — Refresh batch 2 (resto de flujos)

- [ ] **Título**: Igual que batch 1 para: marketplace (+private), comandos del proyecto, human inbox/gallery/
      submit, model-prices, guardrails, eval-quality, tenant-stats, approvals. Preservar `data-testid`/rutas/llamadas.
- **Tests**: `npm run typecheck && lint && build` verde + grep de `data-testid` preservados

### Fase D — Verificación + docs

#### `task_ui_05` — Verificar + guía de convenciones UI + changelog

- [ ] **Título**: Verificación final (`typecheck && lint && build`), confirmar que NINGÚN `data-testid` usado por
      `e2e/*.spec.ts` desapareció (grep cruzado); guía `docs/03-guides/ui-conventions.md` (design-system, primitivas,
      componentes compartidos, estados, a11y, tokens); changelog `docs/07-changelog/ui-refresh-refactor.md`; fila en
      roadmap README.
- **Tests**: `npm run typecheck && lint && build` verde; `test -f` guía + changelog; 0 `data-testid` de e2e perdidos

## Tests humanos del Plan

```yaml
- id: human_ui_01
  description: "El admin-panel se ve más moderno y sigue funcionando igual"
  checklist:
    - "Recorrer las páginas principales: se ven más pulidas (spacing/tipografía/cards/tablas) y coherentes entre sí"
    - "Estados vacío/cargando/error son consistentes en todas"
    - "Ningún flujo cambió de comportamiento ni de ruta; los formularios guardan igual"
    - "Navegación por teclado + foco visibles; contraste legible (modo oscuro)"
    - "Los e2e Playwright existentes siguen pasando (selectores intactos)"
```

## Criterios de cierre

1. Todas las tareas `[x]`; `npm run typecheck && lint && build` verde.
2. `pre-commit run --all-files` (prettier/eslint) verde.
3. NINGÚN `data-testid` usado por los e2e eliminado; comportamiento/rutas/API sin cambios.
4. Test humano validado.
5. Guía de convenciones UI + changelog + fila en README.
6. PR de `plan/ui-refresh-refactor` mergeado (lo hace el humano).

## Próximo Plan

Tras este: actualización integral de la documentación (#24).
