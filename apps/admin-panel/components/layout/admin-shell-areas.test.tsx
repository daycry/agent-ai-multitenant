// @vitest-environment jsdom
/**
 * El shell pinta LA BARRA QUE TOCA: por ruta y por rol (`task_ui_01`).
 *
 * Plan `ui-reestructuracion-2026-09-09`, ola 1. `nav-model.test.ts` prueba la
 * decisión («¿qué área exige esta ruta?»); esto prueba que el shell la OBEDECE,
 * que es la mitad que se rompe en la práctica: una función pura correcta a la
 * que nadie llama es el patrón de fallo dominante de esta base.
 *
 * Se renderiza el `AdminShell` REAL con `usePathname` y `useCurrentUser`
 * mockeados, y se lee el `data-area` del `<nav data-testid="sidebar-nav">`.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { UseCurrentUserResult } from "@/lib/use-current-user";

const pathnameMock = vi.fn<() => string>();
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  usePathname: () => pathnameMock(),
  useRouter: () => ({ push: pushMock, replace: vi.fn() }),
}));

const currentUserMock = vi.fn<() => UseCurrentUserResult>();
vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => currentUserMock(),
}));

vi.mock("@/lib/lang-context", () => ({
  useLang: () => ({ lang: "es", setLang: vi.fn() }),
  useLangOptional: () => "es",
}));
vi.mock("@/components/layout/tenant-picker", () => ({
  TenantPicker: () => <div data-testid="tenant-picker-stub" />,
}));
vi.mock("@/lib/api", () => ({
  apiFetch: vi.fn().mockResolvedValue({ status: "ok", services: [] }),
  ApiError: class ApiError extends Error {},
}));
vi.mock("@/lib/auth", () => ({ clearClientSession: vi.fn() }));
vi.mock("@/lib/tenant-storage", () => ({ clearTenantId: vi.fn() }));
vi.mock("@/lib/session-cache", () => ({ purgeSessionCache: vi.fn() }));

import { AdminShell } from "@/components/layout/admin-shell";

const PROJECT_ID = "8f14e45f-ceea-467a-9f0a-1a2b3c4d5e6f";

function scope(over: Partial<UseCurrentUserResult> = {}): UseCurrentUserResult {
  return {
    user: null,
    isLoading: false,
    isError: false,
    isSystemAdmin: false,
    isSystemOwner: false,
    isTenantAdmin: true,
    isTenantMember: true,
    roleInActiveTenant: "tenant_admin",
    ...over,
  };
}

function renderAt(pathname: string, state: UseCurrentUserResult = scope()) {
  pathnameMock.mockReturnValue(pathname);
  currentUserMock.mockReturnValue(state);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AdminShell>
        <div>contenido</div>
      </AdminShell>
    </QueryClientProvider>,
  );
}

const area = () => screen.getByTestId("sidebar-nav").getAttribute("data-area");

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("el shell elige la barra por ruta", () => {
  it("pinta la de Trabajo en el dashboard y en el portfolio de proyectos", () => {
    renderAt("/admin/dashboard");
    expect(area()).toBe("work");
    cleanup();
    renderAt("/admin/projects");
    expect(area()).toBe("work");
  });

  it("pinta la del PROYECTO dentro de uno, con su vuelta al portfolio", () => {
    renderAt(`/admin/projects/${PROJECT_ID}/plans`);
    expect(area()).toBe("project");
    expect(screen.getByTestId("nav-back-to-projects")).toBeTruthy();
    // Y las entradas cuelgan de ESE proyecto, con testid estable (no el UUID).
    expect(screen.getByTestId("nav-project-overview").getAttribute("href")).toBe(
      `/admin/projects/${PROJECT_ID}`,
    );
    expect(screen.getByTestId("nav-project-plans").getAttribute("href")).toBe(
      `/admin/projects/${PROJECT_ID}/plans`,
    );
  });

  it("pinta la de Sistema en una ruta de plataforma, para el System Admin", () => {
    renderAt("/admin/users", scope({ isSystemAdmin: true }));
    expect(area()).toBe("system");
    expect(screen.getByTestId("nav-group-plataforma")).toBeTruthy();
  });

  it("NO deja la barra vacía a quien entra por enlace directo sin permiso", () => {
    // Un tenant_admin que pega `/admin/users` en la barra de direcciones: el
    // backend le va a decir 403, pero el shell no puede dejarle una barra
    // lateral en blanco de camino. Cae a Trabajo, que es lo que sí puede usar.
    renderAt("/admin/users", scope({ isSystemAdmin: false }));
    expect(area()).toBe("work");
    expect(screen.queryByTestId("nav-group-plataforma")).toBeNull();
  });
});

describe("el selector de área", () => {
  it("sólo se le ofrece a quien tiene más de un área", () => {
    renderAt("/admin/dashboard", scope({ isSystemAdmin: false }));
    expect(screen.queryByTestId("area-switcher")).toBeNull();
    cleanup();
    renderAt("/admin/dashboard", scope({ isSystemAdmin: true }));
    expect(screen.getByTestId("area-switcher")).toBeTruthy();
  });

  it("marca el área activa y navega al inicio de la otra al pulsarla", () => {
    renderAt("/admin/dashboard", scope({ isSystemAdmin: true }));
    expect(screen.getByTestId("area-work").getAttribute("aria-selected")).toBe("true");
    expect(screen.getByTestId("area-system").getAttribute("aria-selected")).toBe("false");

    screen.getByTestId("area-system").click();
    // Cambiar de barra sin cambiar de pantalla dejaría al usuario mirando una
    // página que su barra nueva no contiene: el selector navega.
    expect(pushMock).toHaveBeenCalledWith("/admin/users");
  });
});
