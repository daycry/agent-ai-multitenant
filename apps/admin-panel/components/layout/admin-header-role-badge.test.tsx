// @vitest-environment jsdom
// Plan 06.8 `task_06_8_07` — insignia de rol en la cabecera.
//
// El test humano decía "el header muestra el rol". Se acredita renderizando
// el AdminHeader REAL con `useCurrentUser` mockeado y leyendo el
// `data-testid="role-badge"`: para un `tenant_user` la insignia dice "user",
// para un `tenant_admin` "admin" y para el system admin "system_admin".
// Renderizamos el header entero (no la función RoleBadge aislada) para que el
// test también acredite que sigue CABLEADO en la cabecera — el patrón de
// fallo dominante de esta base es "mecanismo entregado, cero llamantes".

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CurrentUser, UseCurrentUserResult, UserRole } from "@/lib/use-current-user";

const currentUserMock = vi.fn<() => UseCurrentUserResult>();
vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => currentUserMock(),
}));

// El picker de tenant hace sus propias queries (TanStack) — fuera de este test.
vi.mock("@/components/layout/tenant-picker", () => ({
  TenantPicker: () => <div data-testid="tenant-picker-stub" />,
}));
// `useLangOptional` es lo que consume `useT()` (prod-16 `task_prod16_02`): sin
// él en el mock, la cabecera migrada al diccionario petaría al renderizar.
vi.mock("@/lib/lang-context", () => ({
  useLang: () => ({ lang: "es", setLang: vi.fn() }),
  useLangOptional: () => "es",
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ replace: vi.fn() }) }));
vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn(),
  ApiError: class ApiError extends Error {},
}));
vi.mock("@/lib/auth", () => ({ clearToken: vi.fn() }));
vi.mock("@/lib/tenant-storage", () => ({ clearTenantId: vi.fn() }));

import { AdminHeader } from "@/components/layout/admin-header";

const TENANT_ID = "11111111-0000-0000-0000-000000000001";

function user(role: UserRole | null, isSystemAdmin = false): CurrentUser {
  return {
    user_id: "00000000-0000-0000-0000-0000000000aa",
    email: "ana@example.com",
    full_name: "Ana Pérez",
    is_system_admin: isSystemAdmin,
    is_system_owner: false,
    memberships:
      role === null
        ? []
        : [{ tenant_id: TENANT_ID, tenant_name: "Tenant A", role, is_active: true }],
    active_tenant_id: role === null ? null : TENANT_ID,
  };
}

/** Estado de `useCurrentUser` coherente con el rol pedido. */
function asRole(role: UserRole | null, isSystemAdmin = false): UseCurrentUserResult {
  return {
    user: user(role, isSystemAdmin),
    isLoading: false,
    isError: false,
    isSystemAdmin,
    isSystemOwner: false,
    isTenantAdmin: isSystemAdmin || role === "tenant_admin",
    isTenantMember: isSystemAdmin || role !== null,
    roleInActiveTenant: role,
  };
}

function renderHeader(state: UseCurrentUserResult) {
  currentUserMock.mockReturnValue(state);
  return render(<AdminHeader onOpenMobileNav={() => {}} />);
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("RoleBadge en la cabecera admin", () => {
  it("un tenant_user ve la insignia 'user'", () => {
    renderHeader(asRole("tenant_user"));
    const badge = screen.getByTestId("role-badge");
    expect(badge.textContent).toBe("user");
    // Y NO se le promociona a admin por accidente.
    expect(badge.textContent).not.toBe("admin");
  });

  it("un tenant_admin ve 'admin'", () => {
    renderHeader(asRole("tenant_admin"));
    expect(screen.getByTestId("role-badge").textContent).toBe("admin");
  });

  it("el system admin ve 'system_admin'", () => {
    renderHeader(asRole("tenant_admin", true));
    expect(screen.getByTestId("role-badge").textContent).toBe("system_admin");
  });

  it("sin sesión no hay insignia", () => {
    renderHeader({
      user: null,
      isLoading: false,
      isError: false,
      isSystemAdmin: false,
      isSystemOwner: false,
      isTenantAdmin: false,
      isTenantMember: false,
      roleInActiveTenant: null,
    });
    expect(screen.queryByTestId("role-badge")).toBeNull();
  });

  it("logueado pero sin tenant activo tampoco hay insignia", () => {
    renderHeader(asRole(null));
    expect(screen.queryByTestId("role-badge")).toBeNull();
  });
});

describe("zona de tenant de la cabecera (admin-menu-reorg human_menu_01 v5)", () => {
  it("un tenant_user ve un pill estático con el nombre del tenant, no el picker", () => {
    renderHeader(asRole("tenant_user"));
    expect(screen.getByTestId("current-tenant-name").textContent).toBe("Tenant A");
    expect(screen.queryByTestId("tenant-picker-stub")).toBeNull();
  });

  it("el system admin ve el picker (puede cambiar de tenant)", () => {
    renderHeader(asRole("tenant_admin", true));
    expect(screen.getByTestId("tenant-picker-stub")).not.toBeNull();
    expect(screen.queryByTestId("current-tenant-name")).toBeNull();
  });

  it("muestra el usuario logueado junto al menú de cuenta", () => {
    renderHeader(asRole("tenant_user"));
    expect(screen.getByTestId("user-menu").getAttribute("aria-label")).toBe("Cuenta de Ana Pérez");
  });
});
