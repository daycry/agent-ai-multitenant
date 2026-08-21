// @vitest-environment jsdom
/**
 * Red de caracterización de la pantalla OIDC (prod-16 `task_prod16_08`).
 *
 * `page.test.tsx` cubría la tarjeta de callback y nada más: la ficha de la
 * config y el diálogo de alta/edición —donde vive la lógica que duele si se
 * rompe— no los afirmaba nadie. Antes de trocear 915 líneas hace falta una red
 * que muerda, no una que acompañe: estos casos se escribieron ANTES de mover
 * código y se comprobaron rompiendo la implementación a propósito.
 *
 * Lo que se fija aquí es COMPORTAMIENTO, no estructura, para que el troceo pase
 * sin tocar ni una aserción:
 *
 *  - el secreto vacío al EDITAR significa «conserva el guardado» (si el body
 *    llevara `client_secret: ""` el backend lo pisaría con vacío);
 *  - activar/desactivar manda el resto de campos intactos (un PUT parcial
 *    borraría scopes y claim_mappings);
 *  - la plantilla rellena issuer/scopes y el parámetro del IdP se sustituye en
 *    el patrón del issuer.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    user: { user_id: "u1", email: "admin@a.test", full_name: "Admin", is_system_admin: false },
    isLoading: false,
    isError: false,
    isSystemAdmin: false,
    isSystemOwner: false,
    isTenantAdmin: true,
    isTenantMember: true,
    roleInActiveTenant: "tenant_admin",
  }),
}));

import SsoConfigPage from "@/app/admin/settings/sso/page";

import type { SecretSource } from "@/app/admin/settings/sso/sso-types";

const CONFIG: {
  id: string;
  provider: string;
  display_name: string | null;
  enabled: boolean;
  issuer: string;
  client_id: string;
  scopes: string[];
  claim_mappings: Record<string, string>;
  has_client_secret: boolean;
  client_secret_source: SecretSource | null;
  created_at: string;
  updated_at: string;
} = {
  id: "cfg-1",
  provider: "oidc",
  display_name: "Acme Entra ID",
  enabled: true,
  issuer: "https://login.microsoftonline.com/acme/v2.0",
  client_id: "client-abc",
  scopes: ["openid", "email", "profile"],
  claim_mappings: { email: "email", full_name: "name", groups: "groups" },
  has_client_secret: true,
  client_secret_source: "encrypted",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

const TEMPLATES = [
  {
    template_id: "azure",
    display_name: "Azure AD",
    issuer_template: "https://login.microsoftonline.com/{tenant}/v2.0",
    default_scopes: ["openid", "profile", "email"],
    claim_mappings: { email: "email", full_name: "name" },
    required_params: ["tenant"],
    notes: "Necesitas el ID de directorio",
  },
];

let configs: (typeof CONFIG)[] = [];

function routeApi(path: string): unknown {
  if (path === "/auth/sso/config") return configs;
  if (path === "/auth/sso/oidc/callback-url") return { callback_url: "https://x.test/cb" };
  if (path === "/auth/sso/oidc/templates") return TEMPLATES;
  if (path === "/auth/sso/public-base-url") {
    return { base_url: "https://x.test", is_override: true, env_default: "http://localhost:8001" };
  }
  if (path === "/auth/sso/api-path-prefix") {
    return { prefix: "", is_override: true, env_default: "" };
  }
  throw new Error(`unexpected endpoint in test: ${path}`);
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SsoConfigPage />
    </QueryClientProvider>,
  );
}

/** Cuerpo del enésimo PUT/POST a /auth/sso/config* que hizo la pantalla. */
function mutationBodies(): Record<string, unknown>[] {
  return apiFetchMock.mock.calls
    .filter(([path, init]) => path.startsWith("/auth/sso/config") && init !== undefined)
    .map(([, init]) => (init as { body: Record<string, unknown> }).body);
}

beforeEach(() => {
  configs = [];
  apiFetchMock.mockReset();
  apiFetchMock.mockImplementation(async (path: string) => routeApi(path));
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("pantalla OIDC sin configuración", () => {
  it("ofrece configurar y no pinta la ficha", async () => {
    renderPage();

    expect(await screen.findByTestId("sso-empty")).toBeTruthy();
    expect(screen.getByTestId("sso-create-button")).toBeTruthy();
    expect(screen.queryByTestId("sso-config-card")).toBeNull();
  });
});

describe("ficha de la configuración OIDC", () => {
  beforeEach(() => {
    configs = [CONFIG];
  });

  it("pinta issuer, client id, scopes y el origen del secreto", async () => {
    renderPage();

    expect((await screen.findByTestId("sso-config-issuer")).textContent).toBe(CONFIG.issuer);
    expect(screen.getByTestId("sso-config-client-id").textContent).toBe(CONFIG.client_id);
    expect(screen.getByTestId("sso-config-scopes").textContent).toBe("openid email profile");
    expect(screen.getByTestId("sso-enabled-badge").textContent).toContain("activo");
    expect(screen.getByTestId("sso-secret-badge").textContent).toContain("cifrado en reposo");
  });

  it("sin secreto guardado lo dice en vez de fingir que está completo", async () => {
    configs = [{ ...CONFIG, has_client_secret: false, client_secret_source: null }];
    renderPage();

    expect(await screen.findByTestId("sso-no-secret-badge")).toBeTruthy();
    expect(screen.queryByTestId("sso-secret-badge")).toBeNull();
  });

  it("desactivar manda el resto de campos INTACTOS, no un PUT a medias", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("sso-toggle-enabled"));

    await waitFor(() => expect(mutationBodies().length).toBe(1));
    expect(mutationBodies()[0]).toEqual({
      display_name: CONFIG.display_name,
      enabled: false,
      issuer: CONFIG.issuer,
      client_id: CONFIG.client_id,
      scopes: CONFIG.scopes,
      claim_mappings: CONFIG.claim_mappings,
    });
  });

  it("borrar pide confirmación antes de llamar al backend", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage();

    fireEvent.click(await screen.findByTestId("sso-delete-button"));

    expect(window.confirm).toHaveBeenCalled();
    const deletes = apiFetchMock.mock.calls.filter(
      ([, init]) => (init as { method?: string } | undefined)?.method === "DELETE",
    );
    expect(deletes).toHaveLength(0);
  });
});

describe("diálogo de alta OIDC", () => {
  it("no deja crear sin issuer, client id y secreto", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("sso-create-button"));

    const submit = await screen.findByTestId("sso-form-submit");
    expect(submit).toHaveProperty("disabled", true);

    fireEvent.change(screen.getByTestId("sso-form-issuer"), {
      target: { value: "https://idp.test" },
    });
    fireEvent.change(screen.getByTestId("sso-form-client-id"), { target: { value: "cid" } });
    expect(screen.getByTestId("sso-form-submit")).toHaveProperty("disabled", true);

    fireEvent.change(screen.getByTestId("sso-form-client-secret"), { target: { value: "s3cr3t" } });
    expect(screen.getByTestId("sso-form-submit")).toHaveProperty("disabled", false);
  });

  it("la plantilla rellena issuer y scopes, y el parámetro del IdP se sustituye", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("sso-create-button"));

    const select = await screen.findByTestId("sso-form-template");
    await waitFor(() => expect(select.querySelectorAll("option")).toHaveLength(2));
    fireEvent.change(select, { target: { value: "azure" } });

    // Sin el parámetro aún, el hueco queda vacío.
    await waitFor(() =>
      expect(screen.getByTestId("sso-form-issuer")).toHaveProperty(
        "value",
        "https://login.microsoftonline.com//v2.0",
      ),
    );
    expect(screen.getByTestId("sso-form-scopes")).toHaveProperty("value", "openid profile email");
    expect(screen.getByTestId("sso-form-template-notes").textContent).toContain("directorio");

    fireEvent.change(screen.getByTestId("sso-form-param-tenant"), { target: { value: "acme" } });
    expect(screen.getByTestId("sso-form-issuer")).toHaveProperty(
      "value",
      "https://login.microsoftonline.com/acme/v2.0",
    );
  });

  it("al crear manda el secreto y los scopes partidos por espacios", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("sso-create-button"));

    fireEvent.change(await screen.findByTestId("sso-form-issuer"), {
      target: { value: "  https://idp.test  " },
    });
    fireEvent.change(screen.getByTestId("sso-form-client-id"), { target: { value: " cid " } });
    fireEvent.change(screen.getByTestId("sso-form-client-secret"), { target: { value: "s3cr3t" } });
    fireEvent.change(screen.getByTestId("sso-form-scopes"), {
      target: { value: "openid   email" },
    });
    fireEvent.click(screen.getByTestId("sso-form-submit"));

    await waitFor(() => expect(mutationBodies().length).toBe(1));
    expect(mutationBodies()[0]).toMatchObject({
      issuer: "https://idp.test",
      client_id: "cid",
      client_secret: "s3cr3t",
      scopes: ["openid", "email"],
      display_name: null,
      enabled: false,
    });
  });
});

describe("diálogo de edición OIDC", () => {
  beforeEach(() => {
    configs = [CONFIG];
  });

  it("precarga los valores guardados y NO el secreto", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("sso-edit-button"));

    expect(await screen.findByTestId("sso-form-issuer")).toHaveProperty("value", CONFIG.issuer);
    expect(screen.getByTestId("sso-form-client-id")).toHaveProperty("value", CONFIG.client_id);
    expect(screen.getByTestId("sso-form-scopes")).toHaveProperty("value", "openid email profile");
    expect(screen.getByTestId("sso-form-client-secret")).toHaveProperty("value", "");
  });

  it("secreto vacío = conservar el guardado (el body NO lleva client_secret)", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("sso-edit-button"));
    fireEvent.click(await screen.findByTestId("sso-form-submit"));

    await waitFor(() => expect(mutationBodies().length).toBe(1));
    expect(mutationBodies()[0]).not.toHaveProperty("client_secret");
    // Y los claim_mappings del IdP viajan tal cual, no se pierde el de grupos.
    expect(mutationBodies()[0].claim_mappings).toEqual(CONFIG.claim_mappings);
  });

  it("si el operador escribe un secreto nuevo, ese sí viaja", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("sso-edit-button"));
    fireEvent.change(await screen.findByTestId("sso-form-client-secret"), {
      target: { value: "nuevo" },
    });
    fireEvent.click(screen.getByTestId("sso-form-submit"));

    await waitFor(() => expect(mutationBodies().length).toBe(1));
    expect(mutationBodies()[0].client_secret).toBe("nuevo");
  });
});
