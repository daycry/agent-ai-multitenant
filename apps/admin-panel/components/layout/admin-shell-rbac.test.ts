import { describe, expect, it } from "vitest";

import { translate } from "@/lib/i18n";

import {
  NAV_GROUPS,
  navItemVisible,
  visibleNavGroups,
  type NavItem,
  type NavScope,
} from "./admin-shell";

/**
 * Plan 06.8 `task_06_8_08` — ocultar del NAV los ítems admin-only a un
 * `tenant_user`.
 *
 * Por qué existe este fichero además de `admin-shell-cortex.test.ts`: ese
 * cubre `navItemVisible` con ítems SINTÉTICOS (`{href:"/a", adminOnly:true}`),
 * lo que acredita el predicado pero NO que los ítems REALES lleven el flag.
 * Si mañana alguien borra `adminOnly: true` de "Settings", aquellos tests
 * siguen verdes y el `tenant_user` ve la entrada. Aquí se afirma sobre las
 * entradas reales de `NAV_GROUPS`.
 */

const SYSTEM_ADMIN: NavScope = { isTenantAdmin: true, isSystemAdmin: true, isSystemOwner: false };
const TENANT_ADMIN: NavScope = { isTenantAdmin: true, isSystemAdmin: false, isSystemOwner: false };
/** `tenant_user`: miembro sin privilegios de administración del tenant. */
const TENANT_USER: NavScope = { isTenantAdmin: false, isSystemAdmin: false, isSystemOwner: false };

/** Busca un ítem real del NAV por su `href` (falla si desaparece la ruta). */
function navItem(href: string): NavItem {
  for (const group of NAV_GROUPS) {
    const item = group.items.find((i) => i.href === href);
    if (item) return item;
  }
  throw new Error(`expected a NAV item with href '${href}'`);
}

const visibleHrefs = (scope: NavScope): string[] =>
  visibleNavGroups(NAV_GROUPS, scope).flatMap((g) => g.items.map((i) => i.href));

describe("gating del ítem REAL 'Settings' (/admin/settings)", () => {
  it("está declarado adminOnly", () => {
    const settings = navItem("/admin/settings");
    // El nav guarda la clave del diccionario desde prod-16 `task_prod16_02`; el
    // texto visible se comprueba a través de `translate`.
    expect(settings.labelKey).toBe("settings");
    expect(translate("es", "nav", settings.labelKey)).toBe("Settings");
    expect(settings.adminOnly).toBe(true);
  });

  it("un tenant_user NO lo ve; un tenant_admin y un system_admin sí", () => {
    const settings = navItem("/admin/settings");
    expect(navItemVisible(settings, TENANT_USER)).toBe(false);
    expect(navItemVisible(settings, TENANT_ADMIN)).toBe(true);
    expect(navItemVisible(settings, SYSTEM_ADMIN)).toBe(true);
  });

  it("tampoco aparece en el NAV completo de un tenant_user", () => {
    expect(visibleHrefs(TENANT_USER)).not.toContain("/admin/settings");
    expect(visibleHrefs(TENANT_ADMIN)).toContain("/admin/settings");
  });
});

describe("el NAV de un tenant_user no filtra nada admin-only", () => {
  it("no muestra ningún ítem marcado adminOnly / systemAdminOnly / systemOwnerOnly", () => {
    const visible = visibleNavGroups(NAV_GROUPS, TENANT_USER).flatMap((g) => g.items);
    // Guarda no-vacía: si el NAV se quedara vacío, el `every` de abajo pasaría
    // por vacuidad y el test envejecería sin avisar.
    expect(visible.length).toBeGreaterThan(3);
    const leaked = visible.filter(
      (i) => i.adminOnly === true || i.systemAdminOnly === true || i.systemOwnerOnly === true,
    );
    expect(leaked.map((i) => i.href)).toEqual([]);
  });

  it("sí conserva lo que es de cualquier miembro (Dashboard, Mis tareas, Docs)", () => {
    const hrefs = visibleHrefs(TENANT_USER);
    expect(hrefs).toContain("/admin/dashboard");
    expect(hrefs).toContain("/admin/inbox");
    expect(hrefs).toContain("/admin/docs");
  });
});
