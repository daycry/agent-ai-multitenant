// @vitest-environment jsdom

/**
 * `marketplace`, migrado al diccionario (plan prod-16, `task_prod16_04`).
 *
 * El módulo llegaba a esta pasada **a medias**, y de una forma que ninguna de
 * las dos guardas podía contar: siete de sus diez ficheros ya usaban `useT()`
 * —los de despliegue y actualización— y los tres restantes (el catálogo, el
 * marketplace privado y la pantalla de consentimiento) tenían la pantalla
 * entera cableada en castellano. Con el toggle en EN, un tenant veía el banner
 * de actualización traducido encima de un catálogo que decía «Instaladas».
 *
 * Aquí se rinden las tres en los DOS idiomas, incluida la ayuda de formato del
 * manifest —que es donde vive la mitad del texto de `private/`— y la pantalla
 * de permisos con su ayuda por tipo.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: false,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "inst-1" }),
}));

import MarketplaceAdminPage from "@/app/admin/marketplace/page";
import PrivateMarketplacePage from "@/app/admin/marketplace/private/page";
import InstallationPermissionsPage from "@/app/admin/marketplace/installations/[id]/permissions/page";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

const LISTING = {
  id: "listing-1",
  source_id: "src-1",
  tenant_id: null,
  kind: "tool",
  name: "acme-checker",
  version: "1.3.0",
  description: null,
  author: "Acme",
  trust_level: "verified",
  review_status: "published",
  rejection_reason: null,
  requested_permissions: [],
  is_signed: true,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const INSTALLATION = {
  id: "inst-1",
  listing_id: "listing-1",
  version: "1.3.0",
  status: "enabled",
};

const PERMISSIONS = {
  installation_id: "inst-1",
  listing_id: "listing-1",
  status: "disabled",
  consent_required: true,
  all_granted: false,
  permissions: [
    {
      type: "network_policy",
      descriptor: { value: "restricted" },
      state: "pending" as const,
    },
  ],
};

function wireApi(listings: unknown[] = [LISTING], installations: unknown[] = [INSTALLATION]) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path.startsWith("/marketplace/listings")) return Promise.resolve(listings);
    if (path.startsWith("/marketplace/installations/inst-1/permissions"))
      return Promise.resolve(PERMISSIONS);
    if (path.startsWith("/marketplace/installations")) return Promise.resolve(installations);
    if (path.startsWith("/marketplace/shares")) return Promise.resolve([]);
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

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("marketplace en castellano", () => {
  it("rinde cabecera, pestañas y el reclamo de publicar", async () => {
    wireApi();
    renderIn("es", <MarketplaceAdminPage />);

    expect(await screen.findByTestId("marketplace-tab-catalog")).toBeDefined();
    expect(screen.getByTestId("marketplace-tab-catalog").textContent).toBe("Catálogo");
    expect(screen.getByTestId("marketplace-tab-installed").textContent).toBe("Instaladas");
    expect(screen.getByTestId("marketplace-tab-shares").textContent).toBe("Compartir");
    // El reclamo vive DENTRO de la pestaña de catálogo, que no se pinta hasta
    // que la query resuelve.
    await waitFor(() => expect(screen.getByTestId("catalog-publish-callout")).toBeTruthy());
    expect(screen.getByText("¿Tienes una skill o tool interna?")).toBeDefined();
    expect(screen.getByTestId("catalog-publish-cta").textContent).toContain(
      "Publicar en el marketplace",
    );
  });
});

describe("marketplace en inglés", () => {
  it("traduce cabecera, pestañas y el reclamo de publicar", async () => {
    wireApi();
    renderIn("en", <MarketplaceAdminPage />);

    expect(await screen.findByTestId("marketplace-tab-catalog")).toBeDefined();
    expect(screen.getByTestId("marketplace-tab-catalog").textContent).toBe("Catalog");
    expect(screen.getByTestId("marketplace-tab-installed").textContent).toBe("Installed");
    expect(screen.getByTestId("marketplace-tab-shares").textContent).toBe("Sharing");
    await waitFor(() => expect(screen.getByTestId("catalog-publish-callout")).toBeTruthy());
    expect(screen.getByText("Do you have an internal skill or tool?")).toBeDefined();
    expect(screen.getByTestId("catalog-publish-cta").textContent).toContain(
      "Publish to the marketplace",
    );
    expect(screen.getByTestId("marketplace-private-link").textContent).toContain("Private");

    expect(screen.queryByText("Catálogo")).toBeNull();
    expect(screen.queryByText("¿Tienes una skill o tool interna?")).toBeNull();
  });

  it("traduce las etiquetas de la tarjeta del catálogo pero NO el enum del backend", async () => {
    wireApi();
    renderIn("en", <MarketplaceAdminPage />);

    await waitFor(() => expect(screen.getByTestId("catalog-listing-listing-1")).toBeTruthy());
    const card = within(screen.getByTestId("catalog-listing-listing-1"));
    expect(card.getByText("global")).toBeDefined();
    // `kind` y `trust_level` salen crudos: son el enum del backend.
    expect(screen.getByTestId("catalog-kind-listing-1").textContent).toBe("tool");
    expect(screen.getByTestId("catalog-trust-listing-1").textContent).toBe("verified");
  });

  it("traduce el catálogo vacío", async () => {
    wireApi([]);
    renderIn("en", <MarketplaceAdminPage />);

    const empty = await screen.findByTestId("catalog-empty");
    expect(empty.textContent).toContain("The catalog is empty");
  });

  it("traduce la pestaña de instaladas, incluido el estado de la instalación", async () => {
    wireApi();
    renderIn("en", <MarketplaceAdminPage />);

    fireEvent.click(await screen.findByTestId("marketplace-tab-installed"));
    await waitFor(() => expect(screen.getByTestId("installed-inst-1")).toBeTruthy());

    expect(screen.getByTestId("installed-status-inst-1").textContent).toBe("Enabled");
    expect(screen.getByTestId("installed-consent-inst-1").textContent).toContain("Permissions");
    expect(screen.getByTestId("installed-revoke-inst-1").textContent).toContain("Revoke");
    expect(screen.getByTestId("installed-uninstall-inst-1").textContent).toContain("Uninstall");

    expect(screen.queryByText("Habilitada")).toBeNull();
  });

  it("traduce la pestaña de compartir entera", async () => {
    wireApi();
    renderIn("en", <MarketplaceAdminPage />);

    fireEvent.click(await screen.findByTestId("marketplace-tab-shares"));
    await waitFor(() => expect(screen.getByTestId("share-create-card")).toBeTruthy());

    expect(screen.getByText("Share a private listing with another tenant")).toBeDefined();
    expect(screen.getByTestId("share-explainer").textContent).toContain(
      "Sharing is opt-in and explicit",
    );
    expect(screen.getByText("Private listing")).toBeDefined();
    expect(screen.getByText("Target tenant (UUID)")).toBeDefined();
    expect(screen.getByTestId("share-submit").textContent).toBe("Share");
    expect(screen.getByText("Active grants created by your tenant")).toBeDefined();
    const empty = await screen.findByTestId("shares-empty");
    expect(empty.textContent).toContain("By default you share nothing");

    expect(screen.queryByText("Compartir un listing privado con otro tenant")).toBeNull();
  });
});

describe("marketplace privado en los dos idiomas", () => {
  it("rinde el formulario y la ayuda de formato en castellano", async () => {
    wireApi([]);
    renderIn("es", <PrivateMarketplacePage />);

    expect(await screen.findByText("Marketplace privado")).toBeDefined();
    expect(screen.getByText("Publicar listing privado")).toBeDefined();
    expect(screen.getByTestId("private-format-help-summary").textContent).toContain(
      "Un SKILL.md es Markdown",
    );
    expect(screen.getByText("Campos obligatorios")).toBeDefined();
    expect(screen.getByTestId("private-use-example").textContent).toContain("Usar ejemplo");
  });

  it("traduce el formulario, la ayuda de formato y el vacío", async () => {
    wireApi([]);
    renderIn("en", <PrivateMarketplacePage />);

    expect(await screen.findByText("Private marketplace")).toBeDefined();
    expect(screen.getByTestId("private-back-to-catalog").textContent).toContain(
      "Back to the catalog",
    );
    expect(screen.getByText("Publish a private listing")).toBeDefined();
    expect(screen.getByText("Kind")).toBeDefined();
    expect(screen.getByTestId("private-format-help-summary").textContent).toContain(
      "A SKILL.md is Markdown",
    );
    expect(screen.getByText("Required fields")).toBeDefined();
    expect(screen.getByText("Optional")).toBeDefined();
    // La glosa se traduce; el nombre del campo del manifest NO.
    expect(screen.getByText("version (semver, e.g. 1.0.0)")).toBeDefined();
    expect(screen.getByText("name")).toBeDefined();
    expect(screen.getByText("Author (optional)")).toBeDefined();
    expect(screen.getByTestId("private-author").getAttribute("placeholder")).toBe("Platform Team");
    expect(screen.getByTestId("private-use-example").textContent).toContain("Use example");
    expect(screen.getByTestId("private-example-hint").textContent).toContain(
      "Press “Use example” to insert a valid skill manifest",
    );
    expect(screen.getByTestId("private-publish-submit").textContent).toBe("Publish");
    const empty = await screen.findByTestId("private-empty");
    expect(empty.textContent).toContain("has not published any private listing yet");

    expect(screen.queryByText("Marketplace privado")).toBeNull();
    expect(screen.queryByText("Campos obligatorios")).toBeNull();
  });

  it("cambia la ayuda de formato al cambiar de tipo, y en inglés", async () => {
    wireApi([]);
    renderIn("en", <PrivateMarketplacePage />);

    await screen.findByText("Private marketplace");
    fireEvent.change(screen.getByTestId("private-kind-select"), { target: { value: "tool" } });

    expect(screen.getByTestId("private-format-help-summary").textContent).toBe(
      "A tool is a flat YAML document (no Markdown body).",
    );
    expect(screen.getByText("entrypoint (module:function)")).toBeDefined();
  });
});

describe("consentimiento de permisos en los dos idiomas", () => {
  it("rinde la pantalla en castellano", async () => {
    wireApi();
    renderIn("es", <InstallationPermissionsPage />);

    expect(await screen.findByText("Consentimiento de permisos")).toBeDefined();
    await waitFor(() =>
      expect(screen.getByTestId("consent-permission-network_policy")).toBeTruthy(),
    );
    expect(screen.getByTestId("consent-install-status").textContent).toBe(
      "Deshabilitada (pendiente de consentimiento)",
    );
    expect(screen.getByText("Política de red")).toBeDefined();
    expect(screen.getByTestId("consent-grant-network_policy").textContent).toContain("Aprobar");
  });

  it("traduce cabecera, permiso, su ayuda y las acciones", async () => {
    wireApi();
    renderIn("en", <InstallationPermissionsPage />);

    expect(await screen.findByText("Permission consent")).toBeDefined();
    await waitFor(() =>
      expect(screen.getByTestId("consent-permission-network_policy")).toBeTruthy(),
    );
    expect(screen.getByTestId("consent-install-status").textContent).toBe(
      "Disabled (awaiting consent)",
    );
    expect(screen.getByText("Network policy")).toBeDefined();
    expect(screen.getByTestId("consent-state-network_policy").textContent).toBe("Pending");
    expect(screen.getByTestId("consent-help-network_policy").textContent).toContain(
      "no network. restricted = internal network with no egress",
    );
    // El VALOR del permiso es el enum que se guarda: no se traduce.
    expect(screen.getByTestId("consent-value-network_policy").textContent).toBe("restricted");
    expect(screen.getByTestId("consent-grant-network_policy").textContent).toContain("Approve");
    expect(screen.getByTestId("consent-deny-network_policy").textContent).toContain("Deny");
    expect(screen.getByTestId("consent-submit").textContent).toBe("Save decisions");

    expect(screen.queryByText("Política de red")).toBeNull();
    expect(screen.queryByText("Pendiente")).toBeNull();
  });

  it("traduce el contador de decisiones sin guardar, que sólo aparece al decidir", async () => {
    wireApi();
    renderIn("en", <InstallationPermissionsPage />);

    const hint = await screen.findByTestId("consent-pending-hint");
    expect(hint.textContent).toBe("Choose Approve/Deny on each permission and save the decisions.");

    fireEvent.click(screen.getByTestId("consent-grant-network_policy"));
    await waitFor(() =>
      expect(screen.getByTestId("consent-pending-hint").textContent).toBe(
        "1 unsaved decision(s) (marked with *).",
      ),
    );
    expect(screen.getByTestId("consent-state-network_policy").textContent).toBe("Granted *");
  });
});
