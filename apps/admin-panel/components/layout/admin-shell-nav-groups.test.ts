import { describe, expect, it } from "vitest";

import { NAV_GROUPS, navGroupVisible, visibleNavGroups, type NavScope } from "./admin-shell";

/**
 * Plan admin-menu-reorg — `human_menu_01` v1..v3, acreditado por test.
 *
 * El test humano pedía comprobar a ojo tres cosas del sidebar reorganizado:
 *   v1 "grupos colapsables (Trabajo/Recursos/Config tenant/Plataforma/Ayuda)"
 *   v2 "como tenant_admin (no system): NO se ve el grupo Plataforma"
 *   v3 "como System Admin: se ve Plataforma; SSO está ahí (no en Ajustes)"
 *
 * Se acredita sobre la ESTRUCTURA + los predicados puros (`visibleNavGroups`),
 * que es donde vive la decisión; el render del `<ul>` colapsable lo cubre la
 * e2e (`e2e/sidebar-complete.spec.ts`). Vitest corre en entorno `node` aquí:
 * ningún import de React.
 *
 * Nota deliberada sobre el número de grupos: el plan aprobó CINCO grupos, y
 * después el córtex F1 (ADR 0074) añadió un sexto (`cortex`, systemOwnerOnly).
 * Este test fija la lista completa REAL en orden — si alguien añade, quita o
 * reordena un grupo, se pone rojo y hay que decidirlo a conciencia.
 */

/** Los 5 grupos que aprobó el plan admin-menu-reorg, en su orden. */
const PLAN_GROUP_IDS = ["trabajo", "recursos", "config-tenant", "plataforma", "ayuda"] as const;

/** La lista completa hoy: los 5 del plan + `cortex` (ADR 0074) antes de Ayuda. */
const ALL_GROUP_IDS = [
  "trabajo",
  "recursos",
  "config-tenant",
  "plataforma",
  "cortex",
  "ayuda",
] as const;

const SSO_HREF = "/admin/settings/sso";

const TENANT_ADMIN: NavScope = {
  isTenantAdmin: true,
  isSystemAdmin: false,
  isSystemOwner: false,
};
const SYSTEM_ADMIN: NavScope = {
  isTenantAdmin: true,
  isSystemAdmin: true,
  isSystemOwner: false,
};

const groupById = (id: string) => {
  const group = NAV_GROUPS.find((g) => g.id === id);
  if (!group) throw new Error(`expected a NAV group with id '${id}'`);
  return group;
};

/** Todos los hrefs visibles para un rol, aplanados. */
const visibleHrefs = (scope: NavScope): string[] =>
  visibleNavGroups(NAV_GROUPS, scope).flatMap((g) => g.items.map((i) => i.href));

describe("NAV_GROUPS — estructura (human_menu_01 v1)", () => {
  it("declara los grupos esperados, en orden", () => {
    expect(NAV_GROUPS.map((g) => g.id)).toEqual([...ALL_GROUP_IDS]);
  });

  it("mantiene los 5 grupos del plan en su orden aprobado", () => {
    const planOrder = NAV_GROUPS.map((g) => g.id).filter((id) =>
      (PLAN_GROUP_IDS as readonly string[]).includes(id),
    );
    expect(planOrder).toEqual([...PLAN_GROUP_IDS]);
  });

  it("etiqueta cada grupo del plan con su nombre visible", () => {
    expect(groupById("trabajo").label).toBe("Trabajo");
    expect(groupById("recursos").label).toBe("Recursos");
    expect(groupById("config-tenant").label).toBe("Configuración del tenant");
    expect(groupById("plataforma").label).toBe("Plataforma");
    expect(groupById("ayuda").label).toBe("Ayuda");
  });

  it("no deja ningún grupo vacío (un grupo sin ítems no se renderiza)", () => {
    for (const group of NAV_GROUPS) {
      expect(group.items.length, `grupo '${group.id}' sin ítems`).toBeGreaterThan(0);
    }
  });
});

describe("ámbito del grupo Plataforma (human_menu_01 v2 y v3)", () => {
  /**
   * El ámbito del GRUPO se afirma por separado a propósito. Los ítems de
   * Plataforma son además `systemAdminOnly` uno a uno, así que si alguien
   * degradase el flag del grupo a `adminOnly` el grupo se seguiría cayendo
   * por quedarse sin ítems visibles y la aserción de "no lo ve" pasaría
   * igual (comprobado rompiéndolo). Esta aserción es la que detecta esa
   * regresión de la primera capa.
   */
  it("declara el grupo 'plataforma' como systemAdminOnly y no adminOnly", () => {
    const plataforma = groupById("plataforma");
    expect(plataforma.systemAdminOnly).toBe(true);
    expect(plataforma.adminOnly).toBeUndefined();
    expect(navGroupVisible(plataforma, TENANT_ADMIN)).toBe(false);
    expect(navGroupVisible(plataforma, SYSTEM_ADMIN)).toBe(true);
  });

  it("un tenant_admin que NO es system admin no ve el grupo 'plataforma'", () => {
    const ids = visibleNavGroups(NAV_GROUPS, TENANT_ADMIN).map((g) => g.id);
    expect(ids).not.toContain("plataforma");
    // …y sí ve los suyos, para que el test no pase por vacío.
    expect(ids).toContain("recursos");
    expect(ids).toContain("config-tenant");
  });

  it("un tenant_admin no ve NINGUNA entrada de Plataforma, ni SSO", () => {
    const hrefs = visibleHrefs(TENANT_ADMIN);
    expect(hrefs).not.toContain(SSO_HREF);
    for (const item of groupById("plataforma").items) {
      expect(hrefs, `no debería ver ${item.href}`).not.toContain(item.href);
    }
  });

  it("el System Admin sí ve 'plataforma' y con SSO dentro", () => {
    const groups = visibleNavGroups(NAV_GROUPS, SYSTEM_ADMIN);
    const plataforma = groups.find((g) => g.id === "plataforma");
    expect(plataforma).toBeDefined();
    expect(plataforma?.items.map((i) => i.href)).toContain(SSO_HREF);
  });
});

describe("SSO vive en Plataforma, no en el tenant (human_menu_01 v3, ADR 0028)", () => {
  it("está declarado sólo en el grupo 'plataforma'", () => {
    const owners = NAV_GROUPS.filter((g) => g.items.some((i) => i.href === SSO_HREF)).map(
      (g) => g.id,
    );
    expect(owners).toEqual(["plataforma"]);
  });

  it("la entrada SSO es systemAdminOnly", () => {
    const sso = groupById("plataforma").items.find((i) => i.href === SSO_HREF);
    expect(sso?.systemAdminOnly).toBe(true);
  });

  it("no aparece en 'Configuración del tenant' (de donde se movió)", () => {
    const tenantHrefs = groupById("config-tenant").items.map((i) => i.href);
    expect(tenantHrefs).not.toContain(SSO_HREF);
    // El grupo sigue conteniendo lo que es del tenant (guarda no-vacía).
    expect(tenantHrefs).toContain("/admin/settings");
  });
});
