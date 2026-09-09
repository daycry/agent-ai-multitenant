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
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
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

  // task_mk_11 (UI-02): la puerta de despliegue del proyecto enseña lo que pasó,
  // igual que la ficha de la instalación — antes descartaba `warnings` y
  // `oauth_pending`, y nadie pintaba `created_refs`.
  it("tras activar, enseña avisos, OAuth pendiente y qué se escribió en el proyecto", async () => {
    wireApi();
    const base = apiFetchMock.getMockImplementation()!;
    apiFetchMock.mockImplementation((path: string, opts?: { method?: string }) => {
      if (path === "/marketplace/installations/i1/deployments" && opts?.method === "POST") {
        return Promise.resolve({
          deployment: {
            id: "dep-1",
            created_refs: {
              mcp_servers: ["jira"],
              mcp_import: { server: "jira", queue: "marketplace", publish: "after_commit" },
            },
          },
          already_deployed: false,
          warnings: ["las tools de 'jira' se importarán al catálogo en segundo plano"],
          oauth_pending: true,
        });
      }
      return base(path, opts);
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("available-activate-i1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("available-activate-i1"));
    await waitFor(() => expect(screen.getByTestId("available-submit-i1")).toBeTruthy());
    fireEvent.click(screen.getByTestId("available-submit-i1"));

    await waitFor(() => expect(screen.getByTestId("available-last-result")).toBeTruthy());
    expect(screen.getByTestId("deploy-warnings-last").textContent).toContain("segundo plano");
    expect(screen.getByTestId("deploy-oauth-last")).toBeTruthy();
    const refs = screen.getByTestId("deploy-created-refs-last").textContent ?? "";
    expect(refs).toContain("Servidores MCP declarados");
    expect(refs).toContain("jira");
    expect(refs).toContain("Import de tools encolado");
  });
});
