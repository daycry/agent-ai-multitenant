// @vitest-environment jsdom

/**
 * «Instaladas» del marketplace (`task_ui_32`): nombre del listing, capacidad de la
 * instalación (`task_mk_10`), dónde está desplegada y «Desplegar en un proyecto», que
 * lleva a Capacidades del proyecto elegido.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock }),
}));

import { LanguageProvider } from "@/lib/lang-context";
import { InstalledTab } from "./installed-tab";

const BASE = {
  tenant_id: "t1",
  project_id: null,
  version: "1.0.0",
  granted_permissions: [],
  denied_permissions: [],
  installed_by: null,
  installed_at: "2026-08-01T00:00:00Z",
  revoked_at: null,
  revoked_by: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
  listing_kind: "mcp_server",
};
const DESPLEGADA = {
  ...BASE,
  id: "inst-1",
  listing_id: "l-1",
  listing_name: "acme-mcp",
  status: "enabled",
  capability: "on_deploy",
  capability_reason: null,
  deployments: [
    { id: "d-1", project_id: "p-1", project_name: "Web", status: "active" },
    { id: "d-2", project_id: "p-2", project_name: "API", status: "disabled" },
  ],
};
const SIN_DESPLEGAR = {
  ...BASE,
  id: "inst-2",
  listing_id: "l-2",
  listing_name: "otro-mcp",
  status: "enabled",
  capability: "on_deploy",
  capability_reason: null,
  deployments: [],
};

function mount(installations: unknown[]) {
  apiFetchMock.mockImplementation((path: string) => {
    if (String(path).startsWith("/marketplace/installations")) {
      return Promise.resolve(installations);
    }
    if (path === "/projects") {
      return Promise.resolve([
        { id: "p-1", name: "Web" },
        { id: "p-2", name: "API" },
      ]);
    }
    return Promise.resolve([]);
  });
  window.localStorage.setItem("admin-panel.lang", "es");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <InstalledTab />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  pushMock.mockReset();
  window.localStorage.clear();
});

describe("Instaladas", () => {
  it("enseña el nombre, la capacidad y los proyectos donde está desplegada", async () => {
    mount([DESPLEGADA]);
    expect((await screen.findByTestId("installed-listing-name-inst-1")).textContent).toBe(
      "acme-mcp",
    );
    expect(screen.getByTestId("installed-capability-inst-1").textContent).toContain(
      "Se crea al desplegar",
    );
    const web = screen.getByTestId("installed-deployment-inst-1-p-1");
    expect(web.textContent).toContain("Web");
    expect(web.getAttribute("href")).toBe("/admin/projects/p-1/capabilities");
    expect(screen.getByTestId("installed-deployment-inst-1-p-2").textContent).toContain("API");
  });

  it("un proyecto con un despliegue activo y otro desactivado se muestra UNA vez, sin claves duplicadas", async () => {
    const errors = vi.spyOn(console, "error").mockImplementation(() => {});
    mount([
      {
        ...DESPLEGADA,
        deployments: [
          { id: "d-1", project_id: "p-1", project_name: "Web", status: "active" },
          { id: "d-3", project_id: "p-1", project_name: "Web", status: "disabled" },
        ],
      },
    ]);
    const link = await screen.findByTestId("installed-deployment-inst-1-p-1");
    expect(screen.getAllByTestId("installed-deployment-inst-1-p-1")).toHaveLength(1);
    expect(link.textContent).toContain("Web");
    expect(link.textContent).toContain("deshabilitada");
    const dupKey = errors.mock.calls.some(([m]) => String(m).includes("same key"));
    errors.mockRestore();
    expect(dupKey).toBe(false);
  });

  it("sin despliegues lo dice, y «Desplegar en un proyecto» lleva a Capacidades", async () => {
    mount([SIN_DESPLEGAR]);
    expect(await screen.findByTestId("installed-no-deployments-inst-2")).toBeTruthy();
    fireEvent.change(await screen.findByTestId("installed-deploy-project-inst-2"), {
      target: { value: "p-2" },
    });
    fireEvent.click(screen.getByTestId("installed-deploy-inst-2"));
    expect(pushMock).toHaveBeenCalledWith("/admin/projects/p-2/capabilities");
  });

  it("una instalación de tipo diferido conserva «Autorizada, sin capacidad»", async () => {
    mount([{ ...SIN_DESPLEGAR, capability: "deferred", capability_reason: "código sin sandbox" }]);
    expect((await screen.findByTestId("installed-status-inst-2")).textContent).toContain(
      "sin capacidad",
    );
    expect(screen.queryByTestId("installed-deploy-inst-2")).toBeNull();
  });
});
