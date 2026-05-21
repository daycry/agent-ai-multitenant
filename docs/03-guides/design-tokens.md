# Design tokens (admin-panel)

Tokens del sistema de diseño del admin-panel. Brand **indigo-600**,
shadcn/ui Slate underneath. Dark mode con paridad. Decisión tomada
en Plan 01 antes de construir las pantallas de Fase E (sección 19-23).

Los CSS vars viven en
[apps/admin-panel/app/globals.css](../../apps/admin-panel/app/globals.css)
y los utilities Tailwind correspondientes en
[apps/admin-panel/tailwind.config.ts](../../apps/admin-panel/tailwind.config.ts).

## Paleta

| Token         | Light               | Dark                | Uso típico                                     |
| ------------- | ------------------- | ------------------- | ---------------------------------------------- |
| `background`  | `hsl(0 0% 100%)`    | `hsl(222 84% 4.9%)` | Fondo de página                                |
| `foreground`  | `hsl(222 84% 4.9%)` | `hsl(210 40% 98%)`  | Texto base                                     |
| `card`        | `background`        | `background`        | Tarjetas (mismo nivel que page)                |
| `popover`     | `background`        | `background`        | Diálogos, dropdowns                            |
| `muted`       | `hsl(210 40% 96%)`  | `hsl(217 33% 17%)`  | Fondos atenuados (skeletons, dividers)         |
| `muted-fg`    | `hsl(215 16% 47%)`  | `hsl(215 20% 65%)`  | Texto secundario                               |
| `border`      | `hsl(214 32% 91%)`  | `hsl(217 33% 17%)`  | Bordes neutros                                 |
| `primary`     | `hsl(239 84% 56%)`  | `hsl(239 84% 67%)`  | **Brand indigo.** Acción primaria, links, ring |
| `secondary`   | `muted`             | `muted`             | Botón secundario                               |
| `accent`      | `muted`             | `muted`             | Hover / item-activo                            |
| `destructive` | `hsl(0 72% 51%)`    | `hsl(0 62% 50%)`    | Acción destructiva (Delete)                    |
| `success`     | `hsl(142 71% 45%)`  | `hsl(142 71% 55%)`  | OK / done / auto-approve                       |
| `warning`     | `hsl(38 92% 50%)`   | `hsl(38 92% 60%)`   | Degradado / in_review / requiere atención      |
| `danger`      | `destructive`       | `destructive`       | Sinónimo. Down / blocked / critical            |
| `info`        | `hsl(217 91% 60%)`  | `hsl(217 91% 70%)`  | Ready / neutral-informativo                    |

Cada semántico (`success` / `warning` / `danger` / `info`) lleva además
una variante **`-soft`** (fondo tintado claro) con su **`-soft-foreground`**
para badges y bg de alerts. P.ej.:

```html
<span class="bg-success-soft text-success-soft-foreground rounded px-2 py-0.5"> done </span>
```

## Mapeos canónicos

### Estados de tareas (Kanban)

| Status        | Color                               |
| ------------- | ----------------------------------- |
| `backlog`     | `muted` (gris)                      |
| `ready`       | `info` (azul)                       |
| `in_progress` | `primary` (indigo)                  |
| `in_review`   | `warning` (ámbar)                   |
| `blocked`     | `danger` (rojo)                     |
| `done`        | `success` (verde)                   |
| `cancelled`   | `muted` (gris atenuado, opacity-60) |

### Prioridad de tareas

| Priority   | Tratamiento                                          |
| ---------- | ---------------------------------------------------- |
| `low`      | Sin badge, `text-muted-foreground`                   |
| `medium`   | Sin badge, normal                                    |
| `high`     | Badge `bg-warning-soft text-warning-soft-foreground` |
| `critical` | Badge `bg-danger-soft text-danger-soft-foreground`   |

### Scope de agentes (linked-vs-forked)

| Scope                    | Badge                            |
| ------------------------ | -------------------------------- |
| `global_builtin`         | `bg-muted text-muted-foreground` |
| `global_tenant_template` | `bg-info-soft text-info-soft-fg` |
| `project_local`          | `bg-primary/10 text-primary`     |

Razón: built-in es "catálogo de la plataforma" (neutral); tenant_template
es "tuyo pero genérico" (info); project_local es "tu copia para este
proyecto" (brand).

### System health (dashboard)

| Status     | Visual                                                    |
| ---------- | --------------------------------------------------------- |
| `ok`       | Dot `bg-success`                                          |
| `degraded` | Dot `bg-warning`                                          |
| `down`     | Dot `bg-danger` + texto opcional `text-danger-foreground` |

### Decisión de approval-policy

| Decision         | Color                                  |
| ---------------- | -------------------------------------- |
| `auto`           | `bg-success-soft text-success-soft-fg` |
| `human_required` | `bg-warning-soft text-warning-soft-fg` |

## Reglas duras

- **NO** uses colores Tailwind directos (`bg-indigo-600`, `text-red-500`,
  …) en componentes nuevos. Solo tokens. Si te falta uno, añádelo aquí
  primero y luego en `globals.css` + `tailwind.config.ts`.
- **NO** hardcodees hex en el código. Si necesitas un tono específico
  para un gráfico o diagrama, declara un token nuevo.
- Cualquier token nuevo debe tener variante dark en `globals.css`.
- Cualquier token con `-soft` debe tener su `-soft-foreground` pareja
  (los tests visuales asumen contraste AA).
