// @vitest-environment jsdom
/**
 * Red de caracterización de la pantalla SAML (prod-16 `task_prod16_08`).
 *
 * Igual que en OIDC, `page.test.tsx` sólo afirmaba la tarjeta de metadatos del
 * SP. Lo que de verdad duele si se rompe al trocear 943 líneas es lo de abajo, y
 * ninguna de estas reglas se ve mirando el render:
 *
 *  - la clave privada del SP vacía al EDITAR significa «conserva la guardada»;
 *    enviarla vacía dejaría al tenant sin poder firmar el AuthnRequest;
 *  - `attribute_mappings` se construye omitiendo lo vacío: mandar `email: ""`
 *    haría que el backend buscase un atributo con nombre vacío en la aserción;
 *  - los cuatro flags de seguridad viajan tal cual, y `want_assertions_signed`
 *    nace en `true` — un alta que lo mandase en `false` aceptaría aserciones sin
 *    firmar sin que nadie lo pidiera;
 *  - «Extraer datos» rellena el formulario con lo que devuelve el backend, sin
 *    pisar con vacíos lo que el operador ya había escrito.
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

import SamlConfigPage from "@/app/admin/settings/sso/saml/page";

const EMAIL_NAME_ID = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress";
const PERSISTENT_NAME_ID = "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent";

const CONFIG = {
  id: "saml-1",
  provider: "saml",
  display_name: "Acme Okta" as string | null,
  enabled: true,
  idp_entity_id: "https://idp.example.com/saml/metadata",
  idp_sso_url: "https://idp.example.com/saml/sso",
  idp_x509_cert: "MIIDcert",
  name_id_format: PERSISTENT_NAME_ID,
  attribute_mappings: { email: "mail", full_name: "displayName" } as Record<string, string>,
  sp_x509_cert: "MIIDsp" as string | null,
  has_sp_private_key: true,
  sp_private_key_source: "encrypted" as "vault" | "encrypted" | null,
  authn_requests_signed: true,
  want_assertions_signed: true,
  want_assertions_encrypted: false,
  want_name_id_encrypted: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

let configs: (typeof CONFIG)[] = [];
let parsed: Record<string, unknown> = {
  entity_id: "https://idp.parsed/meta",
  sso_url: "https://idp.parsed/sso",
  x509_cert: "MIIDparsed",
  name_id_format: null,
};

function routeApi(path: string): unknown {
  if (path === "/auth/sso/saml/config") return configs;
  if (path === "/auth/sso/saml/sp-metadata") {
    return { sp_entity_id: "https://sp.test/meta", acs_url: "https://sp.test/acs" };
  }
  if (path === "/auth/sso/saml/parse-metadata") return parsed;
  throw new Error(`unexpected endpoint in test: ${path}`);
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <SamlConfigPage />
    </QueryClientProvider>,
  );
}

/** Cuerpos de los POST/PUT a /auth/sso/saml/config (no los de parse-metadata). */
function mutationBodies(): Record<string, unknown>[] {
  return apiFetchMock.mock.calls
    .filter(([path, init]) => path.startsWith("/auth/sso/saml/config") && init !== undefined)
    .map(([, init]) => (init as { body: Record<string, unknown> }).body);
}

beforeEach(() => {
  configs = [];
  parsed = {
    entity_id: "https://idp.parsed/meta",
    sso_url: "https://idp.parsed/sso",
    x509_cert: "MIIDparsed",
    name_id_format: null,
  };
  apiFetchMock.mockReset();
  apiFetchMock.mockImplementation(async (path: string) => routeApi(path));
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("ficha de la configuración SAML", () => {
  beforeEach(() => {
    configs = [CONFIG];
  });

  it("pinta entity id, sso url, NameID y el origen de la clave del SP", async () => {
    renderPage();

    expect((await screen.findByTestId("saml-config-entity-id")).textContent).toBe(
      CONFIG.idp_entity_id,
    );
    expect(screen.getByTestId("saml-config-sso-url").textContent).toBe(CONFIG.idp_sso_url);
    expect(screen.getByTestId("saml-config-name-id").textContent).toBe(PERSISTENT_NAME_ID);
    expect(screen.getByTestId("saml-key-badge").textContent).toContain("cifrado en reposo");
    expect(screen.getByTestId("saml-signed-badge")).toBeTruthy();
  });

  it("sin clave del SP lo dice, y sin firma no promete que la haya", async () => {
    configs = [
      {
        ...CONFIG,
        has_sp_private_key: false,
        sp_private_key_source: null,
        authn_requests_signed: false,
      },
    ];
    renderPage();

    expect(await screen.findByTestId("saml-no-key-badge")).toBeTruthy();
    expect(screen.queryByTestId("saml-key-badge")).toBeNull();
    expect(screen.queryByTestId("saml-signed-badge")).toBeNull();
  });

  it("desactivar conserva los cuatro flags de seguridad y el mapeo de atributos", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("saml-toggle-enabled"));

    await waitFor(() => expect(mutationBodies().length).toBe(1));
    expect(mutationBodies()[0]).toMatchObject({
      enabled: false,
      authn_requests_signed: true,
      want_assertions_signed: true,
      want_assertions_encrypted: false,
      want_name_id_encrypted: false,
      attribute_mappings: CONFIG.attribute_mappings,
      name_id_format: PERSISTENT_NAME_ID,
    });
  });
});

describe("diálogo de alta SAML", () => {
  it("no deja crear sin entity id, sso url y certificado del IdP", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("saml-create-button"));

    expect(await screen.findByTestId("saml-form-submit")).toHaveProperty("disabled", true);

    fireEvent.change(screen.getByTestId("saml-form-entity-id"), { target: { value: "e" } });
    fireEvent.change(screen.getByTestId("saml-form-sso-url"), { target: { value: "u" } });
    expect(screen.getByTestId("saml-form-submit")).toHaveProperty("disabled", true);

    fireEvent.change(screen.getByTestId("saml-form-cert"), { target: { value: "MIID" } });
    expect(screen.getByTestId("saml-form-submit")).toHaveProperty("disabled", false);
  });

  it("«Extraer datos» rellena el formulario con lo que devuelve el backend", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("saml-create-button"));

    const parseButton = await screen.findByTestId("saml-form-metadata-parse");
    expect(parseButton).toHaveProperty("disabled", true); // sin XML no hay nada que extraer

    fireEvent.change(screen.getByTestId("saml-form-metadata"), {
      target: { value: "<EntityDescriptor/>" },
    });
    fireEvent.click(screen.getByTestId("saml-form-metadata-parse"));

    await waitFor(() =>
      expect(screen.getByTestId("saml-form-entity-id")).toHaveProperty(
        "value",
        "https://idp.parsed/meta",
      ),
    );
    expect(screen.getByTestId("saml-form-sso-url")).toHaveProperty(
      "value",
      "https://idp.parsed/sso",
    );
    expect(screen.getByTestId("saml-form-cert")).toHaveProperty("value", "MIIDparsed");
    // El IdP no devolvió NameID: se conserva el por defecto, no se vacía.
    expect(screen.getByTestId("saml-form-name-id")).toHaveProperty("value", EMAIL_NAME_ID);
  });

  it("un alta nace exigiendo aserciones firmadas y omite los atributos vacíos", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("saml-create-button"));

    fireEvent.change(await screen.findByTestId("saml-form-entity-id"), {
      target: { value: " https://idp.test/meta " },
    });
    fireEvent.change(screen.getByTestId("saml-form-sso-url"), {
      target: { value: "https://idp.test/sso" },
    });
    fireEvent.change(screen.getByTestId("saml-form-cert"), { target: { value: "MIID" } });
    fireEvent.change(screen.getByTestId("saml-form-attr-email"), { target: { value: " mail " } });
    fireEvent.click(screen.getByTestId("saml-form-submit"));

    await waitFor(() => expect(mutationBodies().length).toBe(1));
    expect(mutationBodies()[0]).toMatchObject({
      idp_entity_id: "https://idp.test/meta",
      want_assertions_signed: true,
      authn_requests_signed: false,
      name_id_format: EMAIL_NAME_ID,
      attribute_mappings: { email: "mail" },
      sp_x509_cert: null,
      display_name: null,
    });
    // Sin clave escrita, el body NO la lleva.
    expect(mutationBodies()[0]).not.toHaveProperty("sp_private_key");
  });
});

describe("diálogo de edición SAML", () => {
  beforeEach(() => {
    configs = [CONFIG];
  });

  it("precarga lo guardado y NO la clave privada", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("saml-edit-button"));

    expect(await screen.findByTestId("saml-form-entity-id")).toHaveProperty(
      "value",
      CONFIG.idp_entity_id,
    );
    expect(screen.getByTestId("saml-form-attr-email")).toHaveProperty("value", "mail");
    expect(screen.getByTestId("saml-form-attr-full-name")).toHaveProperty("value", "displayName");
    expect(screen.getByTestId("saml-form-name-id")).toHaveProperty("value", PERSISTENT_NAME_ID);
    expect(screen.getByTestId("saml-form-sp-key")).toHaveProperty("value", "");
    expect(screen.getByTestId("saml-form-authn-signed")).toHaveProperty("checked", true);
  });

  it("clave vacía = conservar la guardada (el body NO lleva sp_private_key)", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("saml-edit-button"));
    fireEvent.click(await screen.findByTestId("saml-form-submit"));

    await waitFor(() => expect(mutationBodies().length).toBe(1));
    expect(mutationBodies()[0]).not.toHaveProperty("sp_private_key");
  });

  it("si el operador pega una clave nueva, esa sí viaja", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("saml-edit-button"));
    // Marcador opaco en vez de una cabecera PEM literal: lo que este test
    // comprueba es que el valor VIAJA, y para eso da igual su forma. Con la
    // cabecera de verdad, el hook `detect-private-key` bloquea el commit — y la
    // salida cómoda (desactivar el hook, o partir la cadena para esquivarlo)
    // desarma una guarda de seguridad para siempre por la comodidad de un test.
    const CLAVE_NUEVA = "clave-de-prueba-que-el-operador-acaba-de-pegar";
    fireEvent.change(await screen.findByTestId("saml-form-sp-key"), {
      target: { value: CLAVE_NUEVA },
    });
    fireEvent.click(screen.getByTestId("saml-form-submit"));

    await waitFor(() => expect(mutationBodies().length).toBe(1));
    expect(mutationBodies()[0].sp_private_key).toBe(CLAVE_NUEVA);
  });

  it("desmarcar «exigir aserciones firmadas» viaja como false, no se pierde", async () => {
    renderPage();
    fireEvent.click(await screen.findByTestId("saml-edit-button"));
    fireEvent.click(await screen.findByTestId("saml-form-want-assertions-signed"));
    fireEvent.click(screen.getByTestId("saml-form-submit"));

    await waitFor(() => expect(mutationBodies().length).toBe(1));
    expect(mutationBodies()[0].want_assertions_signed).toBe(false);
  });
});
