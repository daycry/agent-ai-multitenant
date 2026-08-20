// @vitest-environment jsdom

/**
 * `app/admin/users/` y el picker de tenants, en los dos idiomas (plan prod-16,
 * `task_prod16_03`).
 *
 * Este fichero cubre las DOS mitades que el enunciado de la casilla nombra
 * juntas —`users` y `tenants`— y hay que explicar por qué van en el mismo test,
 * porque el plan llevaba una corrección a medias:
 *
 *   - La nota del 2026-08-01 dijo que «`tenants` NO existe como pantalla … así
 *     que esa casilla del enunciado no tiene destino». La primera mitad es
 *     cierta y sigue comprobada (no hay ningún `app/admin/tenants/`); la
 *     segunda es FALSA. La gestión de tenants tiene dos superficies: las
 *     memberships, que viven en el diálogo de esta pantalla, y
 *     `components/layout/tenant-picker.tsx`, que **lista los tenants, cambia el
 *     activo y CREA el primero** — es la única vía de UI para arrancar un
 *     tenant desde cero.
 *   - Y el picker es el caso de libro del aviso que el plan repite: la
 *     `ATTR_ALLOWLIST` le veía **1 atributo**, y lo monta `AdminHeader`, o sea
 *     TODAS las pantallas del System Admin. Su desplegable («Todos los
 *     tenants», «Aún no hay tenants…», «Crear tenant») y su diálogo de alta
 *     entero salían en castellano con el toggle en EN, en la cabecera de una
 *     pantalla por lo demás inglesa.
 *
 * `users` ya estaba migrada desde el 2026-08-01 y se quedó sin este test: la
 * casilla la nombra como pantalla de mayor uso y era el único módulo migrado
 * sin su render en los dos idiomas. Los casos de abajo se comprobaron por
 * mutación (devolver un literal castellano al código) para que no sean una
 * guarda que no puede fallar.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

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

const tenantContextMock = vi.fn();
vi.mock("@/lib/tenant-context", () => ({
  useTenantContext: () => tenantContextMock(),
}));

import UsersPage from "@/app/admin/users/page";
import { TenantPicker } from "@/components/layout/tenant-picker";

const STORAGE_KEY = "admin-panel.lang";

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

function wire(users: unknown[] = [USER], memberships: unknown[] = [MEMBERSHIP]) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/admin/users") return Promise.resolve(users);
    if (path === "/admin/tenants") return Promise.resolve(TENANTS);
    if (path === "/admin/users/u-1/memberships") return Promise.resolve(memberships);
    return Promise.resolve([]);
  });
}

function renderIn(lang: "es" | "en", node: React.ReactElement) {
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>{node}</LanguageProvider>
    </QueryClientProvider>,
  );
}

const page = (lang: "es" | "en", users?: unknown[], memberships?: unknown[]) => {
  wire(users, memberships);
  return renderIn(lang, <UsersPage />);
};

/** El picker con su contexto: superadmin, `n` tenants y ninguno activo. */
function picker(lang: "es" | "en", tenants: typeof TENANTS | [] = TENANTS) {
  tenantContextMock.mockReturnValue({
    me: null,
    isSuperadmin: true,
    tenantId: null,
    setTenantId: vi.fn(),
    tenants,
    tenantsLoading: false,
    refreshTenants: vi.fn(),
  });
  return renderIn(lang, <TenantPicker />);
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  tenantContextMock.mockReset();
  window.localStorage.clear();
});

describe("usuarios en los dos idiomas", () => {
  it("rinde cabecera, buscador y tabla en castellano", async () => {
    page("es");

    expect(await screen.findByText("Usuarios")).toBeDefined();
    expect(screen.getByTestId("users-search").getAttribute("placeholder")).toBe(
      "Buscar por email o nombre…",
    );
    await waitFor(() => expect(screen.getByTestId("users-table")).toBeTruthy());
    expect(screen.getByText("Acceso a tenants")).toBeDefined();
    expect(screen.getByTestId("user-memberships-open-u-1").textContent).toContain(
      "Gestionar tenants",
    );
  });

  it("traduce cabecera, buscador, columnas y badges", async () => {
    page("en");

    expect(await screen.findByText("Users")).toBeDefined();
    expect(screen.getByText(/Platform-wide users/)).toBeDefined();
    expect(screen.getByTestId("users-search").getAttribute("placeholder")).toBe(
      "Search by email or name…",
    );
    expect(screen.getByTestId("users-search").getAttribute("aria-label")).toBe("Search users");
    await waitFor(() => expect(screen.getByTestId("users-table")).toBeTruthy());
    const table = within(screen.getByTestId("users-table"));
    expect(table.getByText("Type")).toBeDefined();
    expect(table.getByText("Status")).toBeDefined();
    expect(table.getByText("Tenant access")).toBeDefined();
    expect(table.getByText("active")).toBeDefined();
    expect(screen.getByTestId("user-memberships-open-u-1").textContent).toContain("Manage tenants");

    expect(screen.queryByText("Acceso a tenants")).toBeNull();
    expect(screen.queryByText("Gestionar tenants")).toBeNull();
  });

  it("traduce el estado vacío del buscador", async () => {
    page("en", []);

    const empty = await screen.findByTestId("users-empty");
    expect(empty.textContent).toBe("There are no users yet.");
    expect(empty.textContent).not.toContain("No hay usuarios");
  });

  it("traduce el diálogo de memberships, que es donde vive la gestión de tenants", async () => {
    page("en");

    fireEvent.click(await screen.findByTestId("user-memberships-open-u-1"));
    await waitFor(() => expect(screen.getByTestId("memberships-dialog")).toBeTruthy());

    const dialog = within(screen.getByTestId("memberships-dialog"));
    expect(dialog.getByText("Tenant access — Ana Ruiz")).toBeDefined();
    expect(dialog.getByText(/Access to a tenant is granted by a membership/)).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("memberships-table")).toBeTruthy());
    // «Role» sale DOS veces (columna de la tabla y etiqueta del formulario de
    // alta), así que la columna se afirma dentro de su tabla.
    const table = within(screen.getByTestId("memberships-table"));
    expect(table.getByText("Role")).toBeDefined();
    expect(table.getByText("Actions")).toBeDefined();
    expect(screen.getByTestId("membership-role-m-1").getAttribute("aria-label")).toBe(
      "Role for Equipo Plataforma",
    );
    expect(screen.getByTestId("membership-toggle-m-1").getAttribute("aria-label")).toBe(
      "Deactivate membership",
    );
    expect(screen.getByTestId("membership-revoke-m-1").getAttribute("aria-label")).toBe(
      "Revoke access",
    );
    expect(dialog.getByText("Grant access to a tenant")).toBeDefined();
    expect(screen.getByTestId("memberships-close").textContent).toBe("Close");

    expect(dialog.queryByText("Acciones")).toBeNull();
    expect(dialog.queryByText("Asignar acceso a un tenant")).toBeNull();
  });

  it("traduce el catálogo de roles en los DOS selectores donde se elige", async () => {
    page("en");

    fireEvent.click(await screen.findByTestId("user-memberships-open-u-1"));
    await waitFor(() => expect(screen.getByTestId("memberships-table")).toBeTruthy());

    // `ROLE_LABEL_KEY` guarda la CLAVE, no el texto: un solo catálogo alimenta
    // el selector por membership y el del formulario de alta.
    const existing = within(screen.getByTestId("membership-role-m-1"));
    expect(existing.getByText("Plan approver")).toBeDefined();
    expect(existing.queryByText("Aprobador de planes")).toBeNull();

    const assign = within(screen.getByTestId("assign-role"));
    expect(assign.getByText("Plan approver")).toBeDefined();
    expect(assign.getByText("System Operator")).toBeDefined();

    expect(within(screen.getByTestId("assign-tenant")).getByText("Select a tenant…")).toBeDefined();
    expect(screen.getByTestId("assign-submit").textContent).toContain("Assign");
  });
});

describe("picker de tenants en los dos idiomas", () => {
  it("rinde el rótulo, el desplegable y el alta en castellano", () => {
    picker("es");

    expect(screen.getByTestId("tenant-picker-label").textContent).toBe("Todos los tenants");
    fireEvent.click(screen.getByTestId("tenant-picker"));
    expect(screen.getByTestId("tenant-picker-all").textContent).toContain("Todos los tenants");
    expect(screen.getByTestId("tenant-picker-create").textContent).toContain("Crear tenant");
  });

  it("traduce el rótulo, el desplegable y su estado vacío", () => {
    picker("en", []);

    expect(screen.getByTestId("tenant-picker-label").textContent).toBe("All tenants");
    fireEvent.click(screen.getByTestId("tenant-picker"));
    expect(screen.getByTestId("tenant-picker-all").textContent).toContain("All tenants");
    expect(screen.getByTestId("tenant-picker-all").textContent).toContain("portfolio");
    expect(screen.getByTestId("tenant-picker-empty").textContent).toBe(
      "There are no tenants yet. Create the first one below.",
    );
    expect(screen.getByTestId("tenant-picker-create").textContent).toContain("Create tenant");

    expect(screen.queryByText("Todos los tenants")).toBeNull();
    expect(screen.queryByText(/Aún no hay tenants/)).toBeNull();
  });

  it("traduce el diálogo de alta de tenant, campos y ayuda del slug incluidos", async () => {
    picker("en");

    fireEvent.click(screen.getByTestId("tenant-picker"));
    fireEvent.click(screen.getByTestId("tenant-picker-create"));
    await waitFor(() => expect(screen.getByTestId("create-tenant-dialog")).toBeTruthy());

    const dialog = within(screen.getByTestId("create-tenant-dialog"));
    // «Create tenant» sale dos veces (título del diálogo y botón de envío): el
    // título se afirma por su rol, que es lo que lo distingue.
    expect(dialog.getByRole("heading", { name: "Create tenant" })).toBeDefined();
    expect(dialog.getByText(/A tenant is the isolated space of a team/)).toBeDefined();
    expect(dialog.getByText("Name")).toBeDefined();
    expect(
      dialog.getByText(/Lowercase identifier: letters, numbers and hyphens only/),
    ).toBeDefined();
    expect(screen.getByTestId("create-tenant-cancel").textContent).toBe("Cancel");
    expect(screen.getByTestId("create-tenant-submit").textContent).toContain("Create tenant");

    expect(dialog.queryByText("Nombre")).toBeNull();
    expect(dialog.queryByText(/Identificador en minúsculas/)).toBeNull();
  });

  it("traduce los dos avisos de validación del slug", async () => {
    picker("en");

    fireEvent.click(screen.getByTestId("tenant-picker"));
    fireEvent.click(screen.getByTestId("tenant-picker-create"));
    await waitFor(() => expect(screen.getByTestId("create-tenant-slug")).toBeTruthy());

    // Formato inválido: empieza por guión.
    fireEvent.change(screen.getByTestId("create-tenant-slug"), { target: { value: "-nope" } });
    expect(screen.getByText(/Invalid format: start with a letter or a number/)).toBeDefined();

    // Slug ya usado: el picker le pasa al diálogo los existentes.
    fireEvent.change(screen.getByTestId("create-tenant-slug"), { target: { value: "plataforma" } });
    expect(screen.getByText("That slug already exists, pick another one.")).toBeDefined();
    expect(screen.queryByText(/Ese slug ya existe/)).toBeNull();
  });

  it("no pinta el cuerpo crudo del backend cuando el alta falla", async () => {
    const { ApiError } = await import("@/lib/api");
    picker("en");

    fireEvent.click(screen.getByTestId("tenant-picker"));
    fireEvent.click(screen.getByTestId("tenant-picker-create"));
    await waitFor(() => expect(screen.getByTestId("create-tenant-name")).toBeTruthy());

    apiFetchMock.mockRejectedValue(new ApiError(500, "<html>nginx traceback</html>"));
    fireEvent.change(screen.getByTestId("create-tenant-name"), { target: { value: "Nuevo" } });
    fireEvent.click(screen.getByTestId("create-tenant-submit"));

    const error = await screen.findByTestId("create-tenant-error");
    expect(error.textContent).not.toContain("nginx");
    expect(error.textContent).not.toContain("<html>");
    expect(error.textContent).toBe(
      "The server failed. If it keeps happening, contact an administrator.",
    );
  });
});
