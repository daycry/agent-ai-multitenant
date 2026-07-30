// B3 (runs-visor): el grupo Trabajo del nav incluye la entrada Runs — la
// página de lista dejó de estar huérfana. Pin de datos (mismo estilo que
// admin-shell-cortex.test.ts); los e2e dependen de href + data-testid.

import { describe, expect, it } from "vitest";

import { NAV_GROUPS } from "@/components/layout/admin-shell";
import { translate } from "@/lib/i18n";

describe("nav Trabajo → Runs (runs-visor B3)", () => {
  it("contains the Runs entry pointing at /admin/runs", () => {
    const trabajo = NAV_GROUPS.find((g) => g.id === "trabajo");
    expect(trabajo).toBeDefined();
    const runs = trabajo?.items.find((i) => i.href === "/admin/runs");
    expect(runs).toBeDefined();
    // El texto vive en el diccionario desde prod-16 `task_prod16_02`.
    expect(runs?.labelKey).toBe("runs");
    expect(translate("es", "nav", "runs")).toBe("Runs");
    expect(translate("en", "nav", "runs")).toBe("Runs");
  });

  it("is visible to any member (no adminOnly gating on the item)", () => {
    const trabajo = NAV_GROUPS.find((g) => g.id === "trabajo");
    const runs = trabajo?.items.find((i) => i.href === "/admin/runs");
    // La lista /runs es para TODOS los miembros del tenant (decisión 2 del
    // plan); el ítem no puede llevar gating de admin.
    expect((runs as { adminOnly?: boolean } | undefined)?.adminOnly).toBeUndefined();
  });
});
