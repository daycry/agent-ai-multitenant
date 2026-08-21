// @vitest-environment jsdom
//
// «Disponibles en tu tenant» — activación local desde las pestañas del proyecto
// (ADR 0142, `task_mkt2_08`). Es la puerta 3 de las tres.
//
// Lo que se clava aquí:
//
//   * lee `GET /projects/{id}/marketplace/available` y filtra por `kind`, para
//     que la pestaña MCP no ofrezca skills ni la de Tools servidores MCP;
//   * «Activar» abre EL MISMO formulario que las otras dos puertas y postea al
//     MISMO endpoint — que es lo que impide que las dos vías de D4 diverjan;
//   * lo que YA está desplegado aquí se enseña con su origen y enlace a la
//     ficha. Se deriva de que `available` es, por definición, «lo instalado y
//     habilitado del tenant MENOS lo que ya tiene despliegue activo aquí».

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { AvailableCapabilitiesSection } from "./available-capabilities-section";

const PROJECT_ID = "proj-1";

const AVAILABLE = [
  {
    installation_id: "i1",
    listing_id: "l1",
    kind: "mcp_server",
    name: "Jira MCP",
    version: "1.2.0",
    description: "Issues",
    trust_level: "verified",
    config_schema: {
      properties: { base_url: { type: "string", title: "Base URL", default: null } },
      required: [],
    },
    targets: ["backend_dev"],
  },
  {
    installation_id: "i2",
    listing_id: "l2",
    kind: "skill",
    name: "Revisor CI4",
    version: "0.3.0",
    description: null,
    trust_level: "community",
    config_schema: null,
    targets: [],
  },
];

/** Lo instalado del tenant: i3 no está en `available` ⇒ ya desplegado aquí. */
const INSTALLATIONS = [
  { id: "i1", listing_id: "l1", version: "1.2.0", status: "enabled" },
  { id: "i2", listing_id: "l2", version: "0.3.0", status: "enabled" },
  { id: "i3", listing_id: "l3", version: "2.0.0", status: "enabled" },
];

const LISTINGS = [
  {
    id: "l1",
    kind: "mcp_server",
    name: "Jira MCP",
    version: "1.2.0",
    description: null,
    trust_level: "verified",
    manifest: {},
  },
  {
    id: "l2",
    kind: "skill",
    name: "Revisor CI4",
    version: "0.3.0",
    description: null,
    trust_level: "community",
    manifest: {},
  },
  {
    id: "l3",
    kind: "mcp_server",
    name: "GitHub MCP",
    version: "2.0.0",
    description: null,
    trust_level: "verified",
    manifest: {},
  },
];

function wireApi({ available = AVAILABLE as Record<string, unknown>[], deployFails = false } = {}) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/me") {
      return Promise.resolve({
        user_id: "u1",
        email: null,
        full_name: null,
        is_system_admin: true,
        memberships: [],
        active_tenant_id: null,
      });
    }
    if (path.includes("/deployments") && opts?.method === "POST") {
      if (deployFails) return Promise.reject(new Error("boom"));
      return Promise.resolve({
        deployment: { id: "dep-1" },
        already_deployed: false,
        warnings: [],
        oauth_pending: false,
      });
    }
    if (path === `/projects/${PROJECT_ID}/marketplace/available`) return Promise.resolve(available);
    if (path.startsWith("/marketplace/installations")) return Promise.resolve(INSTALLATIONS);
    if (path.startsWith("/marketplace/listings")) return Promise.resolve(LISTINGS);
    return Promise.resolve([]);
  });
}

function mount(kinds: string[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <AvailableCapabilitiesSection projectId={PROJECT_ID} kinds={kinds} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("AvailableCapabilitiesSection", () => {
  it("filtra por kind: la pestaña MCP no ofrece skills", async () => {
    wireApi();
    mount(["mcp_server"]);
    await waitFor(() => expect(screen.getByTestId("available-i1")).toBeTruthy());
    expect(screen.queryByTestId("available-i2")).toBeNull();
  });

  it("filtra por kind: la pestaña Tools no ofrece servidores MCP", async () => {
    wireApi();
    mount(["tool", "skill"]);
    await waitFor(() => expect(screen.getByTestId("available-i2")).toBeTruthy());
    expect(screen.queryByTestId("available-i1")).toBeNull();
  });

  it("«Activar» abre el MISMO formulario y postea al MISMO endpoint", async () => {
    wireApi();
    mount(["mcp_server"]);
    await waitFor(() => expect(screen.getByTestId("available-activate-i1")).toBeTruthy());

    fireEvent.click(screen.getByTestId("available-activate-i1"));
    await waitFor(() => expect(screen.getByTestId("available-i1-form")).toBeTruthy());
    expect((screen.getByTestId("available-i1-role-backend_dev") as HTMLInputElement).checked).toBe(
      true,
    );

    fireEvent.change(screen.getByTestId("available-i1-field-base_url"), {
      target: { value: "https://jira.example" },
    });
    fireEvent.click(screen.getByTestId("available-submit-i1"));

    await waitFor(() => {
      const post = apiFetchMock.mock.calls.find(
        ([p, o]) =>
          p === "/marketplace/installations/i1/deployments" &&
          (o as { method?: string })?.method === "POST",
      );
      expect(post).toBeTruthy();
      expect(post?.[1]?.body).toEqual({
        project_id: PROJECT_ID,
        config: { base_url: "https://jira.example" },
        role_map: ["backend_dev"],
      });
    });
  });

  it("enseña lo que YA está desplegado aquí, con su origen y enlace a la ficha", async () => {
    wireApi();
    mount(["mcp_server"]);
    await waitFor(() => expect(screen.getByTestId("deployed-here-i3")).toBeTruthy());
    expect(screen.getByTestId("deployed-here-i3").textContent).toContain("GitHub MCP");
    const link = screen
      .getByTestId("deployed-here-i3")
      .querySelector("a") as HTMLAnchorElement | null;
    expect(link?.getAttribute("href")).toBe("/admin/marketplace/installations/i3");
  });

  it("sin nada por activar lo dice, en vez de dejar un hueco", async () => {
    wireApi({ available: [] });
    mount(["mcp_server"]);
    await waitFor(() => expect(screen.getByTestId("available-empty")).toBeTruthy());
    expect(screen.queryByTestId("available-list")).toBeNull();
  });

  it("un fallo al activar se enseña y no se traga", async () => {
    wireApi({ deployFails: true });
    mount(["mcp_server"]);
    await waitFor(() => expect(screen.getByTestId("available-activate-i1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("available-activate-i1"));
    fireEvent.click(screen.getByTestId("available-submit-i1"));
    await waitFor(() => expect(screen.getByTestId("available-error-i1")).toBeTruthy());
  });
});
