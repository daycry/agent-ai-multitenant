// @vitest-environment jsdom
// mejoras-2026-06 (+ ADR 0079): la pantalla de Usuarios del System Admin debe
// poder conceder el rol `plan_approver` — «Aprobador de planes», el que aprueba
// planes del tenant SIN ser admin (segregación de funciones).
//
// Sin test, el rol se cae del desplegable sin que nada avise: el backend seguiría
// aceptándolo por API y la única vía de UI para asignarlo desaparecería en
// silencio. Es exactamente el patrón «mecanismo entregado, cero llamantes»
// (docs/03-guides/verificar-antes-de-implementar.md §5).
//
// Se cubre en los DOS sitios donde el rol se elige: el formulario de asignación
// y el selector por membership existente (cambiar el rol ya concedido).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: true,
    isSystemOwner: true,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import UsersPage from "@/app/admin/users/page";

const USER = {
  id: "u-1",
  email: "ana@example.com",
  full_name: "Ana Ruiz",
  is_system_admin: false,
  is_active: true,
};

const MEMBERSHIP = {
  id: "m-1",
  user_id: "u-1",
  tenant_id: "t-1",
  tenant_name: "Equipo Plataforma",
  tenant_slug: "plataforma",
  role: "tenant_user",
  is_active: true,
  created_at: "2026-07-01T00:00:00Z",
  updated_at: "2026-07-01T00:00:00Z",
};

const TENANTS = [
  { id: "t-1", name: "Equipo Plataforma", slug: "plataforma" },
  { id: "t-2", name: "Equipo Cliente", slug: "cliente" },
];

function wireApi({ memberships = [MEMBERSHIP] as unknown[] } = {}) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/admin/users") return Promise.resolve([USER]);
    if (path === "/admin/tenants") return Promise.resolve(TENANTS);
    if (path === "/admin/users/u-1/memberships" && opts?.method === "POST") {
      return Promise.resolve({ ...MEMBERSHIP, id: "m-new" });
    }
    if (path === "/admin/users/u-1/memberships") return Promise.resolve(memberships);
    if (path.startsWith("/admin/users/u-1/memberships/")) {
      return Promise.resolve({ ...MEMBERSHIP, ...(opts?.body as Record<string, unknown>) });
    }
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <UsersPage />
    </QueryClientProvider>,
  );
}

/** Abre el diálogo de memberships del usuario de la tabla. */
async function openMemberships() {
  await waitFor(() => expect(screen.getByTestId("user-memberships-open-u-1")).toBeTruthy());
  fireEvent.click(screen.getByTestId("user-memberships-open-u-1"));
  await waitFor(() => expect(screen.getByTestId("memberships-dialog")).toBeTruthy());
}

function optionsOf(testId: string): { value: string; text: string }[] {
  const select = screen.getByTestId(testId) as HTMLSelectElement;
  return Array.from(select.options).map((o) => ({ value: o.value, text: o.textContent ?? "" }));
}

// Los `waitFor` de este fichero esperan transiciones de TanStack Query. El
// timeout por defecto de RTL (1s) se queda corto cuando la suite corre entera en
// paralelo y la máquina va cargada: se vio un rojo fantasma así. Se sube aquí
// (por fichero) en vez de tocar la config compartida.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("Usuarios — el rol «Aprobador de planes» es asignable (ADR 0079)", () => {
  it("el formulario de asignación ofrece plan_approver con su etiqueta en claro", async () => {
    wireApi();
    mount();
    await openMemberships();
    await waitFor(() => expect(screen.getByTestId("assign-role")).toBeTruthy());

    const opts = optionsOf("assign-role");
    const approver = opts.find((o) => o.value === "plan_approver");
    expect(approver).toBeDefined();
    expect(approver?.text).toBe("Aprobador de planes");
    // Y no se ha comido a los demás roles de membership por el camino.
    expect(opts.map((o) => o.value)).toEqual([
      "tenant_admin",
      "tenant_user",
      "plan_approver",
      "system_operator",
    ]);
    // `system_admin` es un flag GLOBAL del usuario, nunca un rol de membership.
    expect(opts.map((o) => o.value)).not.toContain("system_admin");
  });

  it("el selector de una membership existente también lo ofrece", async () => {
    wireApi();
    mount();
    await openMemberships();
    await waitFor(() => expect(screen.getByTestId("membership-role-m-1")).toBeTruthy());
    const opts = optionsOf("membership-role-m-1");
    expect(opts.find((o) => o.value === "plan_approver")?.text).toBe("Aprobador de planes");
  });

  it("elegirlo en una membership existente hace el PATCH con role=plan_approver", async () => {
    wireApi();
    mount();
    await openMemberships();
    await waitFor(() => expect(screen.getByTestId("membership-role-m-1")).toBeTruthy());
    fireEvent.change(screen.getByTestId("membership-role-m-1"), {
      target: { value: "plan_approver" },
    });
    await waitFor(() => {
      const patch = apiFetchMock.mock.calls.find(
        ([p, o]) =>
          p === "/admin/users/u-1/memberships/m-1" &&
          (o as { method?: string })?.method === "PATCH",
      );
      expect(patch).toBeDefined();
      expect((patch?.[1] as { body: unknown }).body).toEqual({ role: "plan_approver" });
    });
  });

  it("asignar un tenant nuevo con ese rol lo manda en el POST", async () => {
    wireApi();
    mount();
    await openMemberships();
    await waitFor(() => expect(screen.getByTestId("assign-tenant")).toBeTruthy());
    // El tenant ya asignado no se reofrece: solo queda t-2.
    fireEvent.change(screen.getByTestId("assign-tenant"), { target: { value: "t-2" } });
    fireEvent.change(screen.getByTestId("assign-role"), { target: { value: "plan_approver" } });
    fireEvent.click(screen.getByTestId("assign-submit"));
    await waitFor(() => {
      const post = apiFetchMock.mock.calls.find(
        ([p, o]) =>
          p === "/admin/users/u-1/memberships" && (o as { method?: string })?.method === "POST",
      );
      expect((post?.[1] as { body: unknown }).body).toEqual({
        tenant_id: "t-2",
        role: "plan_approver",
      });
    });
  });
});
