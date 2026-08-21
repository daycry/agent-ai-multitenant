import { describe, expect, it } from "vitest";

import {
  NAV_GROUPS,
  navGroupVisible,
  navItemVisible,
  visibleNavGroups,
  type NavScope,
} from "./admin-shell";

/**
 * Córtex F1 (Tarea 11) — visibilidad del grupo NAV "Córtex" (`systemOwnerOnly`).
 *
 * El entorno vitest del admin-panel es `node` (no jsdom, sin
 * @testing-library/react), así que NO renderizamos el shell: testeamos los
 * predicados puros que deciden la visibilidad (factorizados fuera del
 * componente justo para esto). La e2e de Playwright (`e2e/cortex.spec.ts`)
 * cubre el render real de `nav-cortex` con el stack levantado.
 */

const OWNER: NavScope = { isTenantAdmin: true, isSystemAdmin: true, isSystemOwner: true };
const SYSTEM_ADMIN: NavScope = { isTenantAdmin: true, isSystemAdmin: true, isSystemOwner: false };
const TENANT_ADMIN: NavScope = { isTenantAdmin: true, isSystemAdmin: false, isSystemOwner: false };
const MEMBER: NavScope = { isTenantAdmin: false, isSystemAdmin: false, isSystemOwner: false };

const cortexGroup = () => {
  const group = NAV_GROUPS.find((g) => g.id === "cortex");
  if (!group) throw new Error("expected a NAV group with id 'cortex'");
  return group;
};

describe("Córtex NAV group", () => {
  it("is declared as systemOwnerOnly with the chat + Panel de Mente items", () => {
    const group = cortexGroup();
    expect(group.systemOwnerOnly).toBe(true);
    // Córtex F2 (ADR 0075) añade el "Panel de Mente" como vista hermana del chat;
    // Córtex F3 (ADR 0074/0077) añade "Identidad" (onboarding co-diseñado).
    const hrefs = group.items.map((i) => i.href);
    expect(hrefs).toContain("/admin/cortex");
    expect(hrefs).toContain("/admin/cortex/mind");
    expect(hrefs).toContain("/admin/cortex/identity");
    // Todos los ítems del grupo siguen siendo systemOwnerOnly.
    expect(group.items.every((i) => i.systemOwnerOnly === true)).toBe(true);
  });

  it("is visible to the System Owner", () => {
    const group = cortexGroup();
    expect(navGroupVisible(group, OWNER)).toBe(true);
    expect(group.items.every((i) => navItemVisible(i, OWNER))).toBe(true);

    const visible = visibleNavGroups(NAV_GROUPS, OWNER);
    expect(visible.some((g) => g.id === "cortex")).toBe(true);
  });

  it("is hidden from a non-owner (system_admin, tenant_admin, member)", () => {
    const group = cortexGroup();
    for (const scope of [SYSTEM_ADMIN, TENANT_ADMIN, MEMBER]) {
      expect(navGroupVisible(group, scope)).toBe(false);
      expect(group.items.every((i) => navItemVisible(i, scope) === false)).toBe(true);
      expect(visibleNavGroups(NAV_GROUPS, scope).some((g) => g.id === "cortex")).toBe(false);
    }
  });
});

describe("navItemVisible precedence", () => {
  it("treats systemOwnerOnly as the most restrictive gate", () => {
    const ownerItem = {
      href: "/x",
      labelKey: "docs",
      Icon: cortexGroup().Icon,
      systemOwnerOnly: true,
    } as const;
    // A system_admin who is NOT the owner must not see a systemOwnerOnly item.
    expect(navItemVisible(ownerItem, SYSTEM_ADMIN)).toBe(false);
    expect(navItemVisible(ownerItem, OWNER)).toBe(true);
  });

  it("keeps the existing admin/systemAdmin gates working", () => {
    const adminItem = {
      href: "/a",
      labelKey: "docs",
      Icon: cortexGroup().Icon,
      adminOnly: true,
    } as const;
    const sysItem = {
      href: "/s",
      labelKey: "docs",
      Icon: cortexGroup().Icon,
      systemAdminOnly: true,
    } as const;
    expect(navItemVisible(adminItem, MEMBER)).toBe(false);
    expect(navItemVisible(adminItem, TENANT_ADMIN)).toBe(true);
    expect(navItemVisible(sysItem, TENANT_ADMIN)).toBe(false);
    expect(navItemVisible(sysItem, SYSTEM_ADMIN)).toBe(true);
  });
});
