# Convenciones de UI (admin-panel)

Guía del **design-system** del `apps/admin-panel`: tokens, primitivas,
componentes compartidos, patrones de estado, accesibilidad y "qué usar
cuándo". Es la referencia para construir pantallas nuevas y refactorizar
las existentes manteniendo coherencia visual y comportamiento.

> Complementa a [design-tokens.md](./design-tokens.md), que detalla la
> **paleta** y los **mapeos semánticos** (estados de Kanban, prioridad,
> scope de agentes, system health, approval-policy). Esta guía cubre el
> resto del sistema: tipografía, espaciado, radios, sombras, primitivas y
> los componentes compartidos del refresh `ui-refresh-refactor`.

## Principios

1. **Behavior-preserving.** El estilo es presentación. No cambies rutas,
   llamadas API (`apiFetch` / `lib/api`), claves/mutaciones de TanStack
   Query, props públicas ni la lógica de datos para "que se vea mejor".
2. **Tokens, no valores crudos.** Nunca `bg-indigo-600` / `#7c3aed` /
   `text-red-500` en código de pantalla. Usa los tokens
   (`bg-primary`, `text-danger-soft-foreground`, …). Si falta un tono,
   se declara como token en `globals.css` + `tailwind.config.ts`.
3. **Tema oscuro violeta refinado, no reemplazado.** El refresh ajusta
   spacing/tipografía/radios/sombras y matices de paleta; mantiene el
   contraste AA y las variantes `-soft`.
4. **Preservar `data-testid`.** Los e2e Playwright (`e2e/*.spec.ts`)
   seleccionan por `data-testid`. Las primitivas y componentes
   compartidos **reenvían** el `data-testid` (directo o vía prop) para
   que el selector siga estable tras un refactor. Nunca borres/renombres
   un `data-testid` existente.
5. **Una sola forma de hacer las cosas comunes.** Listas, formularios,
   estados vacío/cargando/error y tablas tienen un componente canónico.
   Adóptalo en vez de re-implementar el patrón a mano.

## Tokens de diseño

Los CSS vars viven en
[`apps/admin-panel/app/globals.css`](../../apps/admin-panel/app/globals.css)
y se cablean a utilities Tailwind en
[`apps/admin-panel/tailwind.config.ts`](../../apps/admin-panel/tailwind.config.ts).

### Color

Ver [design-tokens.md](./design-tokens.md) para la tabla completa. En
resumen: surfaces (`background` / `card` / `popover` / `muted`), neutros
(`border` / `input` / `foreground` / `muted-foreground`), brand violeta
(`primary` + `ring` lavanda), y semánticos `success` / `warning` /
`danger` / `info`, cada uno con su par **`-soft`** + **`-soft-foreground`**
para fondos tintados (badges, alerts). La sidebar tiene su propia
sub-paleta (`sidebar*`) más profunda que `background`.

Gradiente de marca (headers/CTAs/iconos): utilidades `.bg-brand-gradient`
y `.text-brand-gradient` (indigo→violet).

### Tipografía

- **Escala con leading propio.** El `fontSize` de Tailwind se sobrescribe
  para que cada tamaño (`text-xs`…`text-4xl`) lleve su `line-height` y, a
  partir de `lg`, un `letter-spacing` negativo. Las clases `text-*`
  existentes siguen funcionando: solo ganan ritmo. La familia tipográfica
  **no** cambia.
- **Cuerpo:** `line-height: 1.55` + `letter-spacing: -0.006em` en `body`,
  con `optimizeLegibility` + antialias.
- **Encabezados** (`h1`–`h4`): tracking más cerrado (`-0.018em`) +
  `text-wrap: balance`. Los tamaños siguen siendo utility-driven.
- **Números tabulares** en `<table>` (`font-variant-numeric: tabular-nums`)
  para alinear métricas y columnas numéricas.

### Espaciado

Escala de Tailwind por defecto + pasos finos adicionales: `4.5` (1.125rem),
`13`, `15`, `18`. Son **aditivos** — no rompen ningún uso previo.

### Radios

`--radius: 0.625rem` como base. Escala derivada: `rounded-sm` →
`rounded-md` → `rounded-lg` (= base) → `rounded-xl` → `rounded-2xl`.

### Elevación (sombras)

Sombras **tintadas en violeta** (un negro plano desaparece sobre
`#1a1a2e`). Tokens `--shadow-xs/sm/md/lg` en `globals.css`, mapeados a
`shadow-xs/sm/md/lg` en Tailwind. Los usos previos de `shadow-sm/md/lg`
adoptan el ramp refinado sin tocar nada.

### Animación

- `animate-fade-in` (entrada suave 240ms) para popovers/diálogos/listas.
- `animate-shimmer` / `animate-progress-stripe` para skeletons y la barra
  de progreso global.

## Primitivas (`components/ui/`)

Componentes átomo. Todas reenvían `ref` + spread de props (incluido
`data-testid`) y se estilan solo con tokens.

| Primitiva                            | Para qué                                                                                                                                                    |
| ------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Button`                             | Acciones. Variantes `default` / `destructive` / `outline` / `ghost`; `asChild` para estilar un `<Link>` sin anidar `<button>` en `<a>`.                     |
| `Input`                              | Campo de texto/búsqueda. Altura, borde, radio y focus-ring estándar.                                                                                        |
| `Label`                              | Etiqueta de campo (asóciala con `htmlFor`).                                                                                                                 |
| `Card`                               | Superficie elevada (`card` + sombra tintada).                                                                                                               |
| `Badge`                              | Estado/etiqueta por **significado**, no por color. Variantes `muted` / `primary` / `info` / `success` / `warning` / `danger` (las semánticas usan `-soft`). |
| `Dialog`                             | Modales.                                                                                                                                                    |
| `Tabs`                               | Pestañas controladas.                                                                                                                                       |
| `Spinner`                            | Indicador de carga inline.                                                                                                                                  |
| `RoleGuard`                          | Oculta/condiciona UI por rol (RBAC en cliente; la barrera real es el backend).                                                                              |
| `ViewToggle`                         | Conmutador de vista (p.ej. lista/tarjetas).                                                                                                                 |
| `MarkdownTextarea`                   | Textarea con preview Markdown.                                                                                                                              |
| `*Combobox` (entity/project/team/kb) | Selectores con búsqueda sobre catálogos.                                                                                                                    |

### Primitivas nuevas (refresh `ui-refresh-refactor`)

Todas envuelven el elemento nativo o componen sobre los tokens, conservando
comportamiento y `data-testid`:

| Primitiva                                                                              | Notas                                                                                                                                                                                              |
| -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `Checkbox`                                                                             | `<input type="checkbox">` real (visualmente oculto con `peer`) + caja estilada encima. Conserva `checked`/`onChange`/`id`/`data-testid` y focus-ring.                                              |
| `Select`                                                                               | Wrapper del `<select>` nativo con la altura/borde/radio del `Input` + chevron. Hijos = `<option>`/`<optgroup>`. Conserva `value`/`onChange`/teclado.                                               |
| `EmptyState`                                                                           | Placeholder centrado para "sin datos": `icon` opcional en disco tintado, `title` (requerido), `description`, `action`. Borde discontinuo.                                                          |
| `Skeleton`                                                                             | Bloque muted con pulso. Tamaño/forma vía clases (`h-*`/`w-*`/`rounded-*`). Decorativo (`aria-hidden`); el contenedor anuncia el estado.                                                            |
| `Table` (+ `TableHeader`/`TableBody`/`TableFooter`/`TableRow`/`TableHead`/`TableCell`) | Wrappers finos sobre la tabla nativa que codifican las convenciones (cuerpo `text-sm`, head uppercase muted, filas con `border-t`, scroll horizontal). Reenvían `data-testid`/`colSpan`/`onClick`. |

**Regla:** migra `<input type="checkbox">` / `<select>` nativos sueltos a
`Checkbox` / `Select`. Para tablas "a mano", usa los wrappers `Table*`
(o `DataTable`, abajo) salvo que necesites control por celda imposible de
expresar con la API.

## Componentes compartidos (`components/shared/`)

Extraen patrones recurrentes para reducir duplicación. Son **presentación
pura y controlados**: la página dueña conserva estado, queries y
`data-testid`.

| Componente         | Patrón que resuelve                                                                                                                                                                                                                                      |
| ------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ListToolbar`      | Cabecera de lista: `title` + `count`, buscador controlado (`search` / `onSearchChange`, con `searchTestId` + `searchAriaLabel`) y slot `actions`. Omite las props de búsqueda para solo título + acciones.                                               |
| `FormSection`      | Bloque de campos relacionados dentro de un formulario largo: `title` / `description` / `action`, ritmo de espaciado uniforme y `aria-labelledby` automático que asocia el `<h3>` a la `<section>`.                                                       |
| `StateBlock`       | El triple **cargando → error → vacío → children**. Recibe los flags de la query (`isLoading`/`isError`/`error`/`isEmpty`) y los `loadingTestId`/`errorTestId`/`emptyTestId`, que reenvía a `data-testid`. Precedencia fija. Spinner o `loadingSkeleton`. |
| `DataTable`        | Caso común tabla: `columns` declarativas + `data`, `getRowKey`, `rowProps` y `emptyMessage` (fila full-width cuando no hay datos). Compone las primitivas `Table*`. Azúcar, no contrato: usa las primitivas crudas para casos a medida.                  |
| `SegmentedControl` | El "elige uno de varios" (ventana 7/30/90, moneda, …): grupo de pills, `role="radiogroup"`/`role="radio"`. Controlado (`value`/`onChange`); `getOptionTestId` reenvía el `data-testid` exacto de cada opción.                                            |

## Patrones de estado (vacío / cargando / error)

Toda lista o detalle que cargue datos usa **`StateBlock`** para los tres
estados, en este orden de precedencia: cargando → error → vacío → contenido.

```tsx
<StateBlock
  isLoading={q.isLoading}
  isError={q.isError}
  error={q.error}
  isEmpty={(q.data ?? []).length === 0}
  loadingTestId="teams-loading"
  errorTestId="teams-error"
  emptyTestId="teams-empty"
  emptyTitle="No hay equipos"
  emptyDescription="Crea el primero para empezar."
>
  {/* filas / grid: solo se renderiza cuando hay datos */}
</StateBlock>
```

- **Cargando:** `Spinner` + texto (`aria-busy` + `aria-live="polite"`), o
  `loadingSkeleton` para filas `Skeleton`.
- **Error:** `Card` con `role="alert"`; el mensaje se deriva del objeto
  de error (`.body` / `.message`).
- **Vacío:** `EmptyState` (o `empty` a medida).
- Los `*TestId` mantienen los selectores e2e que la pantalla tenía cuando
  el triple se escribía a mano.

## Accesibilidad (a11y)

- **Foco visible siempre.** Patrón de anillo en todo control interactivo:
  `focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2`
  (las primitivas ya lo traen). No elimines el outline sin reemplazo.
- **Labels.** Cada campo con `Label htmlFor` o `aria-label`. El buscador de
  `ListToolbar` usa `searchAriaLabel` (cae al placeholder).
- **Roles/estado.** `StateBlock` marca `role="alert"` en error y
  `aria-busy`/`aria-live` en carga; `SegmentedControl` es un
  `radiogroup`/`radio` real; `FormSection` asocia su título con
  `aria-labelledby`; los decorativos (chevrons, iconos de `EmptyState`,
  `Skeleton`) van `aria-hidden`.
- **Contraste AA.** Mantener al elegir tonos; cada `-soft` tiene su
  `-soft-foreground` con contraste suficiente (los tests visuales lo
  asumen).
- **Navegación por teclado.** Controles nativos por debajo (`Checkbox`,
  `Select`, `<button>`) ⇒ tab/space/flechas funcionan sin JS extra.

## Navegación del panel — sidebar + header (Plan `admin-menu-reorg`)

El shell del `apps/admin-panel` vive en
[`components/layout/admin-shell.tsx`](../../apps/admin-panel/components/layout/admin-shell.tsx)
(sidebar) +
[`components/layout/admin-header.tsx`](../../apps/admin-panel/components/layout/admin-header.tsx)
(top bar). El menú **no** es una lista plana: se organiza en **5 grupos
con submenús colapsables**, con **visibilidad por ámbito** (RBAC + ADR 0028) y una **scrollbar moderna** cuando desborda.

### Grupos del menú (estructura canónica)

El orden y el ámbito de cada grupo son fijos. El ámbito decide qué grupos
ve cada rol (el gating del cliente es UX; **la barrera real es el
backend**):

| Grupo                        | Ámbito (flag)                    | Ítems (rutas preservadas)                                                                                     |
| ---------------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| **Trabajo**                  | por rol (todos los logueados)    | Dashboard, Mis tareas, Tablero, Aprobaciones, Bandeja                                                         |
| **Recursos**                 | `tenant_admin` (`adminOnly`)     | Agentes, Agentes humanos, Equipos, Proyectos, Knowledge Bases, Memorias, Documentos                           |
| **Configuración del tenant** | `tenant_admin` (`adminOnly`)     | Guardrails, Validación humana, Notificaciones, Calidad (Evals), Estadísticas, Marketplace, Ajustes (Settings) |
| **Plataforma**               | System Admin (`systemAdminOnly`) | Proveedores LLM, Modelos & Precios, **Auth/SSO**, Backups (+ destinos + restaurar)                            |
| **Ayuda**                    | por rol (todos)                  | Documentación                                                                                                 |

- **`Plataforma` es platform-global (ADR 0028):** solo el System Admin del
  tenant especial la ve. Reúne lo que **no** pertenece a un tenant concreto
  (catálogo de proveedores/modelos, autenticación, backups del sistema).
- **`Auth/SSO` pasó de _Ajustes del tenant_ al grupo `Plataforma`** por
  coherencia con ADR 0028 (proveedores de auth platform-global). Es solo
  recolocación de menú: la **ruta no cambia** (`/admin/settings/sso`,
  `/admin/settings/sso/saml`) y el **backend de SSO sigue siendo
  per-tenant** (ADR 0031: ACS SAML en `/auth/sso/{tenant_id}/saml/acs`). El
  re-scope del backend de auth, si llegara, es **otro plan** — aquí no se
  toca.

### Comportamiento colapsable

- Cada grupo es un encabezado clicable que abre/cierra su submenú; el
  estado abierto/cerrado por grupo **persiste en `localStorage`**.
- El grupo que contiene el **ítem activo** se **auto-expande** al cargar,
  para que la ruta actual sea siempre visible sin clicar.
- El estado activo de un ítem se calcula con `isActive` (coincidencia
  exacta o por prefijo `href + "/"`), y marca `aria-current="page"`.

### Scrollbar moderna del sidebar

Cuando el sidebar desborda, usa una **scrollbar fina tematizada** en vez
de la nativa, expuesta como **utilidad reutilizable** (`scrollbar-thin` /
`.scrollbar-slim`) en
[`app/globals.css`](../../apps/admin-panel/app/globals.css): combina
`scrollbar-width: thin` + `scrollbar-color` (Firefox) con
`::-webkit-scrollbar` / `::-webkit-scrollbar-thumb` (Chromium/WebKit)
cableados a tokens del tema (`--sidebar*`). Aplícala al contenedor
`overflow-y-auto` del `<nav>`; reutilízala en cualquier panel con scroll
interno en lugar de re-declarar reglas de scrollbar a mano.

### Header — tenant actual + usuario (Plan `admin-menu-reorg`)

El `admin-header.tsx` es **sticky** y siempre visible. Cluster derecho:

1. **Tenant actual.** Para el **System Admin** es el `TenantPicker`
   existente (puede cambiar de tenant / "Todos los tenants" / crear
   tenant; conserva sus `data-testid`). Para `tenant_admin`/`tenant_user`
   es un **pill estático** (`data-testid="current-tenant"` +
   `current-tenant-name`) con el nombre del tenant activo, resuelto desde
   las memberships de `useCurrentUser()` (`/me`) — estos roles no eligen
   tenant en esta versión.
2. **`RoleBadge`** (`data-testid="role-badge"`): pill que codifica el nivel
   (system_admin / admin / user) por color.
3. **Selector ES/EN** (`lang-switcher` + `lang-es`/`lang-en`) vía
   `useLang()`.
4. **Menú de usuario** (`user-menu` → `user-menu-popover`): avatar con la
   inicial + nombre/email y dropdown con **Perfil** (`user-menu-profile`,
   → `/admin/settings`) y **Cerrar sesión** (`logout`, el de siempre: POST
   `/auth/logout` → limpia token + tenant → `/login`). Cierra con `Escape`
   y mueve el foco al primer ítem al abrir (`role="menu"`/`menuitem`).

> **Behavior-preserving (igual que el refresh).** El menú agrupado y el
> header son **presentación + arquitectura de información**: no cambian
> rutas, llamadas API, ni el contrato de datos, y **preservan todos los
> `data-testid`** que usan los e2e (`nav-*`, `sidebar-nav`, `mobile-nav`,
> `admin-header`, `open-mobile-nav`, `lang-*`, `role-badge`, `user-menu*`,
> `logout`, los del `TenantPicker`). Los testids nuevos (de grupo, de
> tenant/usuario) son **aditivos**.

## Qué usar cuándo (cheatsheet)

| Necesito…                                       | Usa                                                           |
| ----------------------------------------------- | ------------------------------------------------------------- |
| Un color/tono                                   | un **token** (`bg-primary`, `text-danger-soft-foreground`, …) |
| Una acción                                      | `Button` (variante por intención)                             |
| Un estado/etiqueta                              | `Badge` (variante por **significado**)                        |
| Casilla / desplegable                           | `Checkbox` / `Select` (no el nativo suelto)                   |
| Cargando / error / vacío                        | `StateBlock`                                                  |
| Placeholder "sin datos"                         | `EmptyState`                                                  |
| Cabecera de lista con buscador                  | `ListToolbar`                                                 |
| Sección de un formulario largo                  | `FormSection`                                                 |
| Tabla de columnas fijas                         | `DataTable` (o primitivas `Table*` para control fino)         |
| Conmutador "uno de varios" (ventana, moneda, …) | `SegmentedControl`                                            |
| Skeleton de carga                               | `Skeleton` dentro de un contenedor `aria-busy`                |

## Reglas duras

- **NO** colores Tailwind crudos ni hex en código de pantalla: solo tokens.
- **NO** borres/renombres un `data-testid` (rompe los e2e). Si refactorizas
  a una primitiva/componente compartido, reenvía el testid (prop directa,
  `*TestId` de `StateBlock`, o `getOptionTestId` de `SegmentedControl`).
- **NO** cambies comportamiento, rutas, llamadas API ni props públicas al
  refrescar el estilo.
- Cualquier token nuevo: variante dark en `globals.css` + entrada en
  `tailwind.config.ts`; los `-soft` necesitan su `-soft-foreground`.
- Antes de re-implementar a mano un patrón común (lista, form, estado,
  tabla), comprueba si ya existe el componente compartido y adóptalo.
