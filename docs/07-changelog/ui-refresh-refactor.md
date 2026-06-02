---
plan_id: ui-refresh-refactor
title: Refresh visual moderado + refactor del admin-panel
completed_at: null
docs_language: es
---

# Plan ui-refresh-refactor — Refresh visual moderado + refactor del admin-panel

## Resumen

Plan **de frontend** (no de plataforma): refresh visual **moderado** +
refactor del `apps/admin-panel`. El admin-panel ya tenía un design-system
decente (primitivas badge/button/card/dialog/input/tabs/comboboxes/
role-guard/spinner y layout shell/header/breadcrumb/error-boundary/
page-header) sobre un tema oscuro violeta con CSS vars. Este plan lo
**refina de forma centralizada** (tokens de spacing/tipografía/radios/
sombras + matices de paleta), **añade las primitivas que faltaban**,
**extrae componentes compartidos** para reducir duplicación, y **aplica la
capa visual renovada + estados consistentes + a11y** a las pantallas
principales.

> **Behavior-preserving (GUARDRAIL DURO).** Cambios solo presentacionales/
> estructurales. **No** se tocaron rutas, llamadas API (`apiFetch`/`lib/api`),
> claves/mutaciones de TanStack Query, props públicas ni la lógica de datos.
> **Ningún `data-testid` eliminado/renombrado** (los e2e seleccionan por
> ellos). El tema oscuro violeta se **refina**, no se reemplaza; se mantiene
> el contraste AA y las variantes `-soft`. **Backend intacto.**

## Cambios por tarea

### Fase A — Tokens + primitivas

- ✅ **`task_ui_01`** — **Refinar tokens + añadir primitivas.**
  `app/globals.css` + `tailwind.config.ts`: escala tipográfica con
  `line-height`/tracking propios por tamaño (sin cambiar la familia ni
  romper usos `text-*`), ritmo de cuerpo y encabezados (`text-wrap: balance`,
  números tabulares en tablas), pasos finos de spacing (`4.5`/`13`/`15`/`18`),
  ramp de radios derivado de `--radius`, **sombras tintadas en violeta**
  (`--shadow-xs/sm/md/lg`, remapeadas a `shadow-*`) y ajustes sutiles de
  paleta manteniendo AA + `-soft`. Nuevas primitivas en `components/ui/`:
  **`Checkbox`**, **`Select`**, **`EmptyState`**, **`Skeleton`**, **`Table`**
  (+ sub-componentes). Sin cambiar el contrato de las primitivas existentes.

### Fase B — Refactor de componentes compartidos

- ✅ **`task_ui_02`** — **Extraer componentes compartidos + adoptar (piloto).**
  Nuevos en `components/shared/`: **`ListToolbar`** (cabecera de lista +
  buscador controlado), **`FormSection`** (sección de formulario con
  `aria-labelledby`), **`StateBlock`** (triple cargando/error/vacío con
  reenvío de `data-testid` vía `loadingTestId`/`errorTestId`/`emptyTestId`)
  y **`DataTable`** (wrapper declarativo sobre las primitivas `Table*`).
  Adoptados en páginas piloto para validar el patrón y reducir duplicación.

### Fase C — Refresh + consistencia en flujos clave

- ✅ **`task_ui_03`** — **Refresh batch 1 (núcleo + nuevos).** Capa visual +
  estados consistentes + a11y + adopción de primitivas/componentes en:
  dashboard, agents (+`[id]` y sus secciones KBs/Tools), projects (+`[id]`),
  settings, `/admin/llm-providers`. Migración de checkbox/select nativos a
  las primitivas. `data-testid`, rutas y llamadas preservados.
- ✅ **`task_ui_04`** — **Refresh batch 2 (resto de flujos).** Igual para:
  marketplace (+private + listings/playwright-config), comandos del proyecto,
  dep-cache, model-prices, guardrails, eval-quality, tenant-stats, approvals,
  documents. Se añadió **`SegmentedControl`** (compartido) para los
  conmutadores "uno de varios" (ventana 7/30/90, moneda) reenviando los
  `data-testid` por opción con `getOptionTestId`.

### Fase D — Verificación + docs

- ✅ **`task_ui_05`** — **Verificar + guía de convenciones UI + changelog**
  (esta entrada). Verificación final en verde (typecheck/lint/build) desde
  estado limpio; **cross-check de `data-testid`**: 0 selectores de
  `e2e/*.spec.ts` perdidos respecto al punto de rama. Creada la guía
  [`docs/03-guides/ui-conventions.md`](../03-guides/ui-conventions.md)
  (tokens, primitivas —incl. las nuevas—, componentes compartidos, patrones
  de estado, a11y, "qué usar cuándo"); añadida la fila del plan a
  `docs/roadmap/README.md`.

## Primitivas añadidas (`components/ui/`)

| Primitiva                   | Qué aporta                                                                                         |
| --------------------------- | -------------------------------------------------------------------------------------------------- |
| `Checkbox`                  | Casilla estilada sobre `<input type="checkbox">` real (conserva `checked`/`onChange`/`id`/testid). |
| `Select`                    | `<select>` nativo con look del `Input` + chevron (conserva `value`/`onChange`/teclado).            |
| `EmptyState`                | Placeholder "sin datos" centrado y consistente (icono/title/description/action).                   |
| `Skeleton`                  | Bloque de carga con pulso, decorativo (`aria-hidden`).                                             |
| `Table` (+ sub-componentes) | Wrappers finos sobre la tabla nativa con las convenciones del panel.                               |

## Componentes compartidos añadidos (`components/shared/`)

| Componente         | Qué aporta                                                                                          |
| ------------------ | --------------------------------------------------------------------------------------------------- |
| `ListToolbar`      | Cabecera de lista (título + count + buscador controlado + acciones).                                |
| `FormSection`      | Sección de formulario con título/descripción y `aria-labelledby`.                                   |
| `StateBlock`       | Triple cargando/error/vacío con reenvío de `data-testid` y a11y (`role="alert"`, `aria-busy/live`). |
| `DataTable`        | Tabla declarativa (columnas + datos + fila vacía) sobre las primitivas `Table*`.                    |
| `SegmentedControl` | Conmutador "uno de varios" como `radiogroup`/`radio`, con `getOptionTestId`.                        |

## Preservación de `data-testid` (NON-NEGOTIABLE)

Cross-check automatizado entre el punto de rama y `HEAD` (extrae todo
productor de `data-testid` del código: literales, plantillas y los reenvíos
vía `loadingTestId`/`errorTestId`/`emptyTestId` de `StateBlock` y
`getOptionTestId` de `SegmentedControl`):

- **Static testids presentes en base y ausentes en HEAD: 0.**
- **Prefijos de plantilla ausentes en HEAD: 0.**

Los pocos productores que cambiaron de mecanismo (de `data-testid` literal en
la página a una **prop reenviada** del componente compartido) siguen
emitiendo el mismo `data-testid` en el DOM, por lo que los selectores de
`e2e/*.spec.ts` quedan intactos.

## Migraciones

**Ninguna.** Plan puramente frontend; no toca esquema ni backend.

## Verificación

- `npm run typecheck` ✅ (tsc `--noEmit`).
- `npm run lint` ✅ (sin errores; solo warnings preexistentes
  `react-hooks/exhaustive-deps` ajenos al plan).
- `npm run build` ✅ (Next.js production build).
- `pre-commit` (prettier/eslint) ✅ en cada commit (sin `--no-verify`).
- **Cross-check `data-testid`:** 0 selectores de `e2e/*.spec.ts` perdidos.

## Pendiente

- **Tests humanos del plan** — pendientes de ejecutar por un humano:
  recorrer las páginas principales y verificar que se ven más pulidas y
  coherentes; estados vacío/cargando/error consistentes; ningún flujo cambió
  de comportamiento ni de ruta; foco/teclado visibles y contraste legible;
  los e2e Playwright existentes siguen pasando (selectores intactos).
- **Merge del PR de `plan/ui-refresh-refactor` a `main`** — lo gestiona el
  humano tras los tests humanos. El plan no se marca `completed` aquí.

## PR

Pendiente de apertura/merge a `main` (lo gestiona el humano tras validar los
tests humanos del plan).
