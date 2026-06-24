# Manuales de usuario (generados con Playwright → PDF)

Generador **reutilizable** de los manuales de usuario de la plataforma. Cada
manual es un script de Playwright que **navega la app real**, captura un
**pantallazo de cada pantalla** y renderiza un **PDF** con la explicación paso a
paso. Cuando la UI cambia, **se vuelve a ejecutar** y los PDF se regeneran.

- **Scripts (specs):** [`specs/`](./specs) — un fichero `*.manual.ts` por manual.
- **Núcleo reutilizable:** [`lib/manual.ts`](./lib/manual.ts) (captura + render PDF
  con identidad de marca), [`lib/auth.ts`](./lib/auth.ts) (login),
  [`lib/seed-demo-data.mjs`](./lib/seed-demo-data.mjs) (datos demo reales),
  [`lib/seed-helper.ts`](./lib/seed-helper.ts) (lee ids sembrados + `docker ps`),
  [`lib/combine-pdfs.mjs`](./lib/combine-pdfs.mjs) (PDF único).
- **PDF generados:** [`pdf/`](./pdf) — un PDF por manual + `manual-completo.pdf`
  (todo en uno). Esto es lo que se entrega.
- **Pantallazos intermedios:** `assets/` (regenerables).

## Cómo regenerar los manuales

Las capturas salen del **stack contenerizado completo** servido por **Caddy**
(single-origin, `http://localhost:8080`): así los manuales reflejan el producto
real de producción (proxy inverso, app contenerizada, datos reales).

Prerrequisitos: infra dev arriba (`scripts/dev/up.ps1` o `docker compose -f
docker/docker-compose.yml -f docker/docker-compose.dev.yml up -d`), Docker,
`node`/`npm`, y el admin del tenant **Demo Manuales** (lo crea
`apps/api-server/seeds/init_tenant.py`).

```powershell
# Desde la raíz del repo. Construye imágenes, levanta el overlay contenerizado
# (api-server + admin-panel + Caddy), siembra datos, captura y genera los PDFs.
./scripts/dev/generate-manuals.ps1

# Más rápido en iteración (sin reconstruir imágenes ni re-sembrar):
./scripts/dev/generate-manuals.ps1 -SkipBuild -SkipSeed

# Filtrar un manual concreto (por número de orden):
./scripts/dev/generate-manuals.ps1 -Grep "04" -SkipBuild -SkipSeed
```

El runner (`scripts/dev/generate-manuals.ps1`):

1. Construye `admin-panel:manuals` (con `NEXT_PUBLIC_API_URL=/api`, single-origin)
   y `api-server:manuals`.
2. Levanta el overlay [`docker/docker-compose.manuals.yml`](../../docker/docker-compose.manuals.yml)
   (api-server + admin-panel + Caddy) sobre la infra dev.
3. Siembra datos demo reales (proyecto **Hello World PHP** + plan + tareas) — idempotente —
   y la **demo de validación humana** (`seed-review-demo.ps1`: review-session + app levantada
   para el manual 12 · ADR 0062).
4. Captura `docker compose ps` en `assets/dockers.json` (lo usa el manual 11).
5. Ejecuta cada `*.manual.ts` por **Caddy** (`MANUALS_BASE_URL=http://localhost:8080`,
   `MANUALS_NO_WEBSERVER=1`) y combina todo en `manual-completo.pdf`.

## Añadir o editar un manual

Crea/edita un fichero en `specs/` siguiendo el patrón declarativo:

```ts
import { test } from "@playwright/test";
import { login } from "../lib/auth";
import { generateManual, ManualDef } from "../lib/manual";

const manual: ManualDef = {
  order: "11",
  slug: "11-mi-area",
  title: "Mi área",
  audience: "Administrador de tenant",
  intro: `<p>Qué cubre este manual…</p>`,
  steps: [
    {
      title: "Pantalla principal",
      goto: "/admin/mi-area",
      fullPage: true,
      body: `<p>Explicación paso a paso…</p>`,
    },
    // …un paso por pantalla; cada uno se acompaña de su pantallazo en el PDF.
  ],
};

test(`manual ${manual.order} — ${manual.title}`, async ({ page }) => {
  await login(page);
  await generateManual(page, manual);
});
```

> El login se hace una vez por manual. Si una pantalla requiere un rol que el
> usuario no tiene (p. ej. _system admin_) o no existe en el entorno, el paso se
> registra en el PDF como **"pantalla no disponible"** con su explicación, sin
> romper el resto del manual.

## Versiones

`@playwright/test` está pinneado a **1.60.0** (alineado con `apps/admin-panel`)
por compatibilidad con la versión de Node del entorno (Node 23 + Playwright 1.61
tiene un bug de resolución de módulos).
