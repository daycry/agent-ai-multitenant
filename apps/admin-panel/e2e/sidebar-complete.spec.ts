import { expect, test, type Page } from "@playwright/test";
import { seedSession } from "./helpers/session";

/**
 * E2E del sidebar global (Plan 06.6 `task_06_6_10`), REPARADA para el menú
 * agrupado del Plan admin-menu-reorg (`human_menu_01` v6: "ningún enlace
 * cambió de ruta; los e2e existentes siguen pasando").
 *
 * Por qué estaba roto (verificado leyendo `components/layout/admin-shell.tsx`
 * el 2026-07-29 — un spec que no puede pasar es peor que no tenerlo):
 *
 *   1. **Grupos colapsados.** `NavGroupBlock` sólo renderiza su `<ul>` cuando
 *      `open`, y `open` arranca en `hasActiveItem` reconciliándose después con
 *      `localStorage["agentic.nav.group.<id>"]`. Playwright arranca SIN
 *      localStorage y en `/admin/dashboard`, así que sólo se auto-expande el
 *      grupo "trabajo". 7 de los 10 enlaces esperados (Agentes, Equipos,
 *      Proyectos, Memorias, Documentos, Validación humana, Settings) viven en
 *      "recursos"/"config-tenant", que estaban cerrados: sus enlaces NO
 *      existían en el DOM y `toBeVisible()` no podía pasar.
 *   2. **Ámbito de grupo.** "recursos" y "config-tenant" son `adminOnly`, así
 *      que hacen falta credenciales de `tenant_admin` EN EL TENANT ACTIVO.
 *   3. **Forma de `/me` obsoleta.** El mock devolvía `{user_id, tenant_id,
 *      role:"admin"}`, de antes del Plan 06.8. `useCurrentUser` hace
 *      `user?.memberships.find(...)`: con `memberships` undefined eso LANZA
 *      (`Cannot read properties of undefined`), lo caza el `AdminErrorBoundary`
 *      del layout y desaparece el shell entero. Ni un enlace.
 *
 * La reparación: mock de `/me` con la forma real (membership `tenant_admin` +
 * `active_tenant_id`), estado abierto de los grupos sembrado en localStorage, y
 * un segundo test que afirma el contrato de colapso en sí — cerrado no
 * renderiza enlaces, al pulsar la cabecera aparecen — para que el spec cubra
 * la reorganización en vez de fingir que no ocurrió.
 *
 * NOTA: reparada por lectura del código; NO ejecutada aquí (este entorno no
 * tiene navegador ni dev server). PENDIENTE de una pasada humana con
 * `npx playwright test e2e/sidebar-complete.spec.ts`.
 */

const TENANT_ID = "11111111-0000-0000-0000-000000000001";

/** Forma real de `GET /me` (Plan 06.8): memberships + active_tenant_id. */
const TENANT_ADMIN = {
  user_id: "aaaa0000-0000-0000-0000-000000000001",
  email: "admin@a.test",
  full_name: "Admin A",
  is_system_admin: false,
  is_system_owner: false,
  memberships: [
    { tenant_id: TENANT_ID, tenant_name: "Tenant A", role: "tenant_admin", is_active: true },
  ],
  active_tenant_id: TENANT_ID,
};

/** Las 10 entradas históricas, con el grupo del menú en el que viven hoy. */
const EXPECTED_ENTRIES: { label: string; group: string }[] = [
  { label: "Dashboard", group: "trabajo" },
  { label: "Tablero", group: "trabajo" },
  { label: "Aprobaciones", group: "trabajo" },
  { label: "Agentes", group: "recursos" },
  { label: "Equipos", group: "recursos" },
  { label: "Proyectos", group: "recursos" },
  { label: "Memorias", group: "recursos" },
  { label: "Documentos", group: "recursos" },
  { label: "Validación humana", group: "config-tenant" },
  { label: "Settings", group: "config-tenant" },
];

const GROUPS_WITH_ENTRIES = [...new Set(EXPECTED_ENTRIES.map((e) => e.group))];

/** Prefijo de la clave de localStorage que persiste abierto/cerrado. */
const NAV_GROUP_LS_PREFIX = "agentic.nav.group.";

async function setup(page: Page, opts: { openGroups?: string[] } = {}): Promise<void> {
  await seedSession(page, { tenantId: TENANT_ID });
  await page.addInitScript(
    (seed: { prefix: string; openGroups: string[] }) => {
      for (const id of seed.openGroups) {
        window.localStorage.setItem(seed.prefix + id, "1");
      }
    },
    {
      prefix: NAV_GROUP_LS_PREFIX,
      openGroups: opts.openGroups ?? [],
    },
  );

  // `**/me` cubre también el `/auth/me` del TenantProvider.
  await page.route("**/me", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(TENANT_ADMIN),
    }),
  );
}

test("el sidebar conserva las 10 entradas históricas (con sus grupos abiertos)", async ({
  page,
}) => {
  await setup(page, { openGroups: GROUPS_WITH_ENTRIES });
  await page.goto("/admin/dashboard", { waitUntil: "domcontentloaded" });

  const nav = page.getByTestId("sidebar-nav");
  await expect(nav).toBeVisible();

  for (const { label } of EXPECTED_ENTRIES) {
    await expect(nav.getByRole("link", { name: label }).first()).toBeVisible();
  }
});

test("un grupo colapsado no renderiza sus enlaces; al abrirlo aparecen", async ({ page }) => {
  // Sin sembrar nada: "recursos" arranca cerrado (no contiene la ruta activa).
  await setup(page);
  await page.goto("/admin/dashboard", { waitUntil: "domcontentloaded" });

  const nav = page.getByTestId("sidebar-nav");
  const header = page.getByTestId("nav-group-recursos");
  await expect(header).toBeVisible();
  await expect(header).toHaveAttribute("aria-expanded", "false");
  // El grupo con la ruta activa sí está abierto (guarda no-vacía: si NADA se
  // renderizara, el `toHaveCount(0)` de abajo pasaría por vacuidad).
  await expect(nav.getByRole("link", { name: "Dashboard" })).toBeVisible();
  // Por TESTID y no por nombre accesible: `{ name: "Agentes" }` dejó de ser
  // único el día que entró «Agentes humanos» en la nav, y Playwright en modo
  // estricto resuelve a DOS elementos (`nav-agents` y `nav-human-agents`) — el
  // spec caía con `strict mode violation`, no con un fallo de producto. El
  // testid es lo que esta casa usa para seleccionar y no lo mueve un cambio de
  // copy ni un vecino con nombre parecido.
  await expect(nav.getByTestId("nav-agents")).toHaveCount(0);

  await header.click();

  await expect(header).toHaveAttribute("aria-expanded", "true");
  await expect(nav.getByTestId("nav-agents")).toBeVisible();
});

test("un tenant_admin no ve el grupo Plataforma (ADR 0028)", async ({ page }) => {
  await setup(page, { openGroups: ["plataforma"] });
  await page.goto("/admin/dashboard", { waitUntil: "domcontentloaded" });

  await expect(page.getByTestId("sidebar-nav")).toBeVisible();
  // Ni la cabecera del grupo ni su entrada de SSO, ni forzando el abierto.
  await expect(page.getByTestId("nav-group-plataforma")).toHaveCount(0);
  await expect(page.getByTestId("nav-sso")).toHaveCount(0);
});
