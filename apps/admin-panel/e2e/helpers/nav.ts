/**
 * Navegar por la barra lateral cuando el grupo que hace falta está CERRADO.
 *
 * La nav del panel son grupos colapsables (`Plan admin-menu-reorg`,
 * `task_menu_01`) y sólo se abre solo el grupo que contiene la ruta ACTIVA:
 *
 *     const [open, setOpen] = useState(hasActiveItem);   // admin-shell.tsx
 *
 * Consecuencia: estando en `/admin/dashboard`, el grupo `recursos` —el que
 * lleva Agentes, Equipos y Proyectos— está cerrado, y sus `<Link>` **no están
 * en el DOM**. Un `getByTestId("nav-agents").click()` no falla diciendo «no
 * existe»: se queda esperando los 30 s del test a que aparezca un elemento que
 * nadie va a renderizar. Cuatro specs no mockeados (`agents-catalog`,
 * `lang-switcher`, `project-wizard`, `team-detail`) se escribieron antes de esa
 * reorganización y llevaban desde entonces agotando el reloj en ese click.
 *
 * Que el grupo arranque cerrado es comportamiento DELIBERADO del producto, no
 * un fallo: `sidebar-complete.spec.ts` lo afirma explícitamente
 * (`aria-expanded="false"` + `nav-agents` con `toHaveCount(0)`). Así que el
 * arnés es el que tiene que abrirlo, y hacerlo con un click —en vez de sembrar
 * `localStorage`— mantiene la intención original de estos specs: recorrer la
 * navegación como la recorre una persona, no saltar a la URL.
 */

import { expect, type Page } from "@playwright/test";

/** Ids de grupo de `NAV_GROUPS` (`components/layout/admin-shell.tsx`). */
export type NavGroupId =
  "trabajo" | "recursos" | "config-tenant" | "plataforma" | "cortex" | "ayuda";

/**
 * Deja abierto el grupo `id` de la barra lateral (idempotente).
 *
 * Espera primero a que la cabecera exista, porque los grupos con ámbito
 * (`adminOnly`, `systemAdminOnly`, …) no se pintan hasta que `GET /me`
 * responde: sin esa espera el helper miraría una nav todavía a medio montar.
 */
export async function openNavGroup(page: Page, id: NavGroupId): Promise<void> {
  const header = page.getByTestId(`nav-group-${id}`);
  await expect(header).toBeVisible();
  if ((await header.getAttribute("aria-expanded")) !== "true") {
    await header.click();
  }
  await expect(header).toHaveAttribute("aria-expanded", "true");
}

/**
 * Abre el grupo que contiene `testid` y pulsa ese ítem de la nav.
 *
 * `group` es explícito a propósito: derivarlo del testid exigiría duplicar aquí
 * el reparto de `NAV_GROUPS`, que es justo la tabla que cambió y dejó estos
 * specs colgados.
 */
export async function navigateVia(page: Page, group: NavGroupId, testid: string): Promise<void> {
  await openNavGroup(page, group);
  await page.getByTestId(testid).click();
}
