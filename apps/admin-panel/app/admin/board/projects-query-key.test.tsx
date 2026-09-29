// @vitest-environment jsdom
// Una clave de TanStack Query por FORMA de dato: el portfolio guarda
// `{items, truncated}` (fetchAllPages) y «Instaladas» un array. Con la misma
// clave, según el orden de navegación uno leía la forma del otro (TypeError).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});
vi.mock("@/lib/ws", () => ({ useWebSocket: () => {}, wsUrl: (p: string) => `ws://test${p}` }));
vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: false,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

import BoardPage from "@/app/admin/board/page";
import { InstalledTab } from "@/app/admin/marketplace/installed-tab";
import { LanguageProvider } from "@/lib/lang-context";

const PROJECTS = [{ id: "p-1", name: "Web" }];
const PLANS = [{ id: "pl-1", title: "Plan", status: "in_progress", project_id: "p-1" }];
const INSTALL = {
  id: "inst-1",
  tenant_id: "t",
  listing_id: "l",
  listing_name: "acme",
  listing_kind: "mcp_server",
  project_id: null,
  version: "1",
  status: "enabled",
  capability: "on_deploy",
  capability_reason: null,
  granted_permissions: [],
  denied_permissions: [],
  deployments: [],
};

function wire() {
  apiFetchMock.mockImplementation((path: string) => {
    const p = String(path);
    if (p.startsWith("/marketplace/installations")) return Promise.resolve([INSTALL]);
    if (p.startsWith("/projects")) return Promise.resolve(PROJECTS);
    if (p.startsWith("/plans?")) return Promise.resolve(PLANS);
    return Promise.resolve([]);
  });
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("clave de caché de proyectos", () => {
  it("portfolio e «Instaladas» comparten QueryClient sin pisarse (en los dos órdenes)", async () => {
    window.localStorage.setItem("admin-panel.lang", "es");
    wire();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrap = (ui: React.ReactNode) => (
      <QueryClientProvider client={client}>
        <LanguageProvider>{ui}</LanguageProvider>
      </QueryClientProvider>
    );

    const first = render(wrap(<BoardPage />));
    expect((await screen.findByTestId("portfolio-project-pl-1")).textContent).toBe("Web");
    first.unmount();

    // La clave `["projects","tenant"]` guarda un ARRAY (forma de los demás
    // consumidores); el portfolio guarda su `{items, truncated}` en otra.
    const shared = client.getQueryData(["projects", "tenant"]);
    expect(shared === undefined || Array.isArray(shared)).toBe(true);

    // Con el listado de proyectos aún sin resolver, «Instaladas» lee la caché
    // que dejó el portfolio: no puede recibir su forma.
    apiFetchMock.mockImplementation((path: string) =>
      String(path).startsWith("/projects")
        ? new Promise(() => {})
        : Promise.resolve(String(path).startsWith("/marketplace") ? [INSTALL] : []),
    );
    render(wrap(<InstalledTab />));
    expect(await screen.findByTestId("installed-listing-name-inst-1")).toBeTruthy();
    expect(screen.queryByTestId("route-error")).toBeNull();
  });
});
