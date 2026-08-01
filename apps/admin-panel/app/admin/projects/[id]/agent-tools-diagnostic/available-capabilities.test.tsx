// @vitest-environment jsdom
//
// El CABLEADO de la puerta 3 en la pestaña de Tools del proyecto (ADR 0142,
// `task_mkt2_08`).
//
// Nota de honestidad: el plan nombraba `app/admin/projects/[id]/tools`, que **no
// existe**. La pestaña de Tools del proyecto es `agent-tools-diagnostic`
// («Tools por agente» en la rejilla de secciones), así que la activación local
// va ahí. Es también la única parte de esa pantalla que escribe, y por eso su
// banner de «solo lectura» quedó acotado al diagnóstico.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "proj-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/admin/projects/proj-1/agent-tools-diagnostic",
  useSearchParams: () => new URLSearchParams(),
}));

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import AgentToolsDiagnosticPage from "@/app/admin/projects/[id]/agent-tools-diagnostic/page";
import { LanguageProvider } from "@/lib/lang-context";

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
    if (path === "/projects/proj-1/agent-tools-diagnostic") {
      return Promise.resolve({ project_id: "proj-1", agents: [], mcp_servers: [] });
    }
    if (path === "/projects/proj-1/marketplace/available") return Promise.resolve(AVAILABLE);
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // La página usa `useLang()` (no la variante opcional) para la taxonomía.
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <AgentToolsDiagnosticPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("pestaña Tools del proyecto — «Disponibles en tu tenant»", () => {
  it("monta la sección y ofrece SÓLO tools y skills disponibles", async () => {
    wireApi();
    mount();
    // Se espera al ITEM, no a la sección: la sección se pinta antes de que la
    // consulta resuelva, así que esperar por ella pasaría con la lista vacía.
    await waitFor(() => expect(screen.getByTestId("available-i2")).toBeTruthy());
    expect(screen.getByTestId("available-capabilities-section")).toBeTruthy();
    // Un servidor MCP se activa desde la pestaña MCP, no desde aquí.
    expect(screen.queryByTestId("available-i1")).toBeNull();
  });
});
