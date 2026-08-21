// @vitest-environment jsdom
//
// El CABLEADO de la puerta 3 en la pestaña MCP (ADR 0142, `task_mkt2_08`).
//
// La sección tiene su propio test de comportamiento
// (`components/marketplace/available-capabilities-section.test.tsx`); lo que
// este fichero protege es lo otro, que es lo que de verdad se pudre: que la
// pestaña la MONTE y con el filtro correcto. Un componente entregado y no
// montado es el patrón «mecanismo entregado, cero llamantes» que este repo
// arrastra, y no lo caza ningún test del componente.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "proj-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/admin/projects/proj-1/mcp-servers",
  useSearchParams: () => new URLSearchParams(),
}));

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import ProjectMcpServersPage from "@/app/admin/projects/[id]/mcp-servers/page";

const AVAILABLE = [
  {
    installation_id: "i1",
    listing_id: "l1",
    kind: "mcp_server",
    name: "Jira MCP",
    version: "1.2.0",
    description: null,
    trust_level: "verified",
    config_schema: null,
    targets: [],
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

function wireApi() {
  apiFetchMock.mockImplementation((path: string) => {
    // «Activar» va bajo <RoleGuard min="tenant_admin">, y RoleGuard revienta
    // el árbol entero si `/me` no responde con la forma real.
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
    if (path === "/projects/proj-1") {
      return Promise.resolve({ id: "proj-1", name: "Proyecto", mcp_servers: [] });
    }
    if (path === "/projects/proj-1/marketplace/available") return Promise.resolve(AVAILABLE);
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ProjectMcpServersPage />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("pestaña MCP del proyecto — «Disponibles en tu tenant»", () => {
  it("monta la sección y ofrece SÓLO los servidores MCP disponibles", async () => {
    wireApi();
    mount();
    // Se espera al ITEM, no a la sección: la sección se pinta antes de que la
    // consulta resuelva, así que esperar por ella pasaría con la lista vacía.
    await waitFor(() => expect(screen.getByTestId("available-i1")).toBeTruthy());
    expect(screen.getByTestId("available-capabilities-section")).toBeTruthy();
    // Una skill no se activa desde la pestaña MCP.
    expect(screen.queryByTestId("available-i2")).toBeNull();
  });
});
