// @vitest-environment jsdom
//
// Pantalla de administración de invitaciones (ADR 0134, opción C).
//
// Lo que se fija aquí, y por qué cada cosa importa:
//
//   1. **El token se enseña UNA vez.** El backend solo lo devuelve al emitir y
//      no lo guarda en claro en ninguna parte, así que si esta pantalla no lo
//      pone delante del admin, el token se pierde y hay que revocar y reemitir.
//      Y el listado NO lo trae: si alguien "mejorara" la tabla para mostrarlo,
//      estaría mostrando un campo que no existe.
//   2. **El enlace que se le pasa al invitado** apunta a `/accept-invite` con el
//      token — sin eso el admin tiene un secreto y ninguna instrucción de qué
//      hacer con él, que es el patrón «mecanismo entregado, cero llamantes».
//   3. **Revocar llama al endpoint** y refresca. Una invitación revocada es la
//      única forma de cerrar una puerta ya abierta.
//   4. El desplegable de rol ofrece los cuatro roles de membership y NUNCA
//      `system_admin`, que es un flag global del usuario y no un rol de tenant.

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

import InvitationsPage from "@/app/admin/invitations/page";

const TENANTS = [
  { id: "t-1", name: "Equipo Plataforma", slug: "plataforma" },
  { id: "t-2", name: "Equipo Cliente", slug: "cliente" },
];

const INVITATION = {
  id: "i-1",
  tenant_id: "t-1",
  tenant_name: "Equipo Plataforma",
  email: "ana@example.com",
  role: "tenant_user",
  token_prefix: "aainv_1234abcd",
  status: "pending",
  expires_at: "2026-08-07T10:00:00Z",
  redeemed_at: null,
  revoked_at: null,
  created_at: "2026-07-31T10:00:00Z",
};

function wireApi({ invitations = [INVITATION] as unknown[] } = {}) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/admin/tenants") return Promise.resolve(TENANTS);
    if (path === "/admin/invitations" && opts?.method === "POST") {
      return Promise.resolve({
        ...INVITATION,
        id: "i-new",
        token: "aainv_1234abcd_the-secret-tail",
      });
    }
    if (path === "/admin/invitations") return Promise.resolve(invitations);
    if (path === "/admin/invitations/i-1/revoke") {
      return Promise.resolve({
        ...INVITATION,
        status: "revoked",
        revoked_at: "2026-07-31T12:00:00Z",
      });
    }
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <InvitationsPage />
    </QueryClientProvider>,
  );
}

configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

async function issue() {
  await waitFor(() => expect(screen.getByTestId("invitation-email")).toBeTruthy());
  fireEvent.change(screen.getByTestId("invitation-email"), {
    target: { value: "ana@example.com" },
  });
  fireEvent.change(screen.getByTestId("invitation-tenant"), { target: { value: "t-1" } });
  fireEvent.change(screen.getByTestId("invitation-role"), { target: { value: "tenant_admin" } });
  fireEvent.click(screen.getByTestId("invitation-submit"));
}

describe("Invitaciones — administración", () => {
  it("lista las invitaciones con su estado y su prefijo, nunca el token", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("invitation-row-i-1")).toBeTruthy());
    const row = screen.getByTestId("invitation-row-i-1").textContent ?? "";
    expect(row).toContain("ana@example.com");
    expect(row).toContain("aainv_1234abcd");
    expect(row).toContain("Pendiente");
    // El listado del backend no trae `token`; la fila no puede inventarlo.
    expect(row).not.toContain("the-secret-tail");
  });

  it("emitir manda email + tenant + rol y enseña el token UNA vez, con su aviso", async () => {
    wireApi();
    mount();
    await issue();

    await waitFor(() => {
      const post = apiFetchMock.mock.calls.find(
        ([p, o]) => p === "/admin/invitations" && (o as { method?: string })?.method === "POST",
      );
      expect(post).toBeDefined();
      expect((post?.[1] as { body: Record<string, unknown> }).body).toMatchObject({
        email: "ana@example.com",
        tenant_id: "t-1",
        role: "tenant_admin",
      });
    });

    await waitFor(() => expect(screen.getByTestId("issued-token")).toBeTruthy());
    expect((screen.getByTestId("issued-token") as HTMLInputElement).value).toBe(
      "aainv_1234abcd_the-secret-tail",
    );
    // El aviso de "solo se muestra una vez" no es decoración: sin él el admin
    // cierra el diálogo y pierde el único ejemplar del secreto.
    expect(screen.getByTestId("issued-token-warning").textContent).toContain("una vez");
  });

  it("ofrece el enlace de canje ya formado para el invitado", async () => {
    wireApi();
    mount();
    await issue();
    await waitFor(() => expect(screen.getByTestId("issued-link")).toBeTruthy());
    const link = (screen.getByTestId("issued-link") as HTMLInputElement).value;
    expect(link).toContain("/accept-invite?token=aainv_1234abcd_the-secret-tail");
  });

  it("revocar llama al endpoint de revocación", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("invitation-revoke-i-1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("invitation-revoke-i-1"));
    await waitFor(() => {
      const call = apiFetchMock.mock.calls.find(([p]) => p === "/admin/invitations/i-1/revoke");
      expect(call).toBeDefined();
      expect((call?.[1] as { method?: string })?.method).toBe("POST");
    });
  });

  it("una invitación ya canjeada no ofrece revocar", async () => {
    wireApi({
      invitations: [
        { ...INVITATION, id: "i-1", status: "redeemed", redeemed_at: "2026-07-31T11:00:00Z" },
      ],
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("invitation-row-i-1")).toBeTruthy());
    expect(screen.queryByTestId("invitation-revoke-i-1")).toBeNull();
  });

  it("el desplegable de rol ofrece los cuatro roles de membership y ninguno más", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("invitation-role")).toBeTruthy());
    const select = screen.getByTestId("invitation-role") as HTMLSelectElement;
    const values = Array.from(select.options).map((o) => o.value);
    expect(values).toEqual(["tenant_admin", "tenant_user", "plan_approver", "system_operator"]);
    expect(values).not.toContain("system_admin");
  });
});
