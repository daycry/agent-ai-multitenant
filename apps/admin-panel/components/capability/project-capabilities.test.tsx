// @vitest-environment jsdom
//
// `task_ui_20` — la pestaña Capacidades del proyecto: MCP · Tools · Skills con la
// procedencia, la conexión, «N tools importadas» y el egress que devuelve el endpoint,
// y la sección «Capacidades disponibles» del marketplace fundida dentro.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "proj-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/admin/projects/proj-1/capabilities",
  useSearchParams: () => new URLSearchParams(),
}));

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { ProjectCapabilities } from "@/components/capability/project-capabilities";
import { type McpOrigin, parseTab, provenanceOf } from "@/lib/capability/project-capabilities";

const DECLARED: McpOrigin = {
  kind: "declared",
  deployment_id: null,
  listing_id: null,
  listing_name: null,
  version: null,
};
const DEPLOYED: McpOrigin = {
  kind: "marketplace",
  deployment_id: "d1",
  listing_id: "l1",
  listing_name: "Docs MCP",
  version: "1.2.0",
};

const MCP = [
  {
    name: "atlassian",
    transport: "streamable_http",
    url: "https://mcp.atlassian.com/v1/mcp",
    origin: DECLARED,
    oauth: { required: true, connected: false },
    imported_tools: 0,
    egress: { blocked: false, host: null, code: null },
  },
  {
    name: "docs",
    transport: "http",
    url: "https://docs.example.com/mcp",
    origin: DEPLOYED,
    oauth: { required: false, connected: null },
    imported_tools: 3,
    egress: { blocked: true, host: "docs.example.com", code: "EGRESS_HOST_NOT_ALLOWLISTED" },
  },
];

const CAPABILITIES = {
  entity_type: "project",
  mcp: MCP,
  tools: [
    { deployment_id: "d2", listing_id: "l2", name: "Status checker", version: "2.0.0", agents: 2 },
  ],
  skills: [
    { deployment_id: "d3", listing_id: "l3", name: "Doc contracts", version: "0.3.0", agents: 1 },
  ],
};

const PROJECT = {
  id: "proj-1",
  name: "P",
  mcp_servers: MCP.map((s) => ({
    name: s.name,
    transport: s.transport,
    command: null,
    args: [],
    env: {},
    url: s.url,
    headers: {},
    auth_ref: null,
    timeout_s: 30,
  })),
};

function wireApi(overrides: Record<string, unknown> = {}) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path in overrides) return Promise.resolve(overrides[path]);
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
    if (path === "/projects/proj-1/capabilities") return Promise.resolve(CAPABILITIES);
    if (path === "/projects/proj-1") return Promise.resolve(PROJECT);
    if (path === "/projects/proj-1/marketplace/available") return Promise.resolve([]);
    if (path.startsWith("/projects/proj-1/mcp-servers/")) {
      return Promise.resolve({ connected: false, expires_at: null });
    }
    return Promise.resolve([]);
  });
}

function mount(initialTab?: "mcp" | "tools" | "skills") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ProjectCapabilities projectId="proj-1" initialTab={initialTab} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("helpers puros", () => {
  it("parseTab cae en MCP con cualquier valor desconocido", () => {
    expect(parseTab("skills")).toBe("skills");
    expect(parseTab("tools")).toBe("tools");
    expect(parseTab("otra")).toBe("mcp");
    expect(parseTab(null)).toBe("mcp");
  });

  it("provenanceOf distingue declarado de desplegado y no enseña una procedencia a medias", () => {
    expect(provenanceOf(DECLARED)).toEqual({ kind: "declared" });
    expect(provenanceOf(DEPLOYED)).toEqual({
      kind: "marketplace",
      listing: "Docs MCP",
      version: "1.2.0",
    });
    expect(provenanceOf({ ...DEPLOYED, listing_name: null })).toEqual({ kind: "declared" });
  });
});

describe("pestaña Capacidades del proyecto", () => {
  it("enseña las tres pestañas y arranca en MCP", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("capability-card-docs")).toBeTruthy());
    for (const tab of ["mcp", "tools", "skills"]) {
      expect(screen.getByTestId(`capabilities-tab-${tab}`)).toBeTruthy();
    }
    expect(screen.getByTestId("capabilities-tab-mcp").getAttribute("aria-selected")).toBe("true");
  });

  it("dice la procedencia: declarado, o desplegado desde el listing y su versión", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("capability-origin-docs")).toBeTruthy());
    expect(screen.getByTestId("capability-origin-atlassian").textContent).toContain("Declarado");
    const docs = screen.getByTestId("capability-origin-docs").textContent ?? "";
    expect(docs).toContain("Docs MCP");
    expect(docs).toContain("1.2.0");
  });

  it("enseña la conexión OAuth, las tools importadas y el egress de cada servidor", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("capability-card-docs")).toBeTruthy());
    expect(screen.getByTestId("capability-oauth-atlassian").textContent).toContain("sin conectar");
    expect(screen.queryByTestId("capability-oauth-docs")).toBeNull();
    expect(screen.getByTestId("mcp-server-imported-docs").textContent).toContain("3");
    expect(screen.getByTestId("mcp-server-not-imported-atlassian")).toBeTruthy();
    expect(screen.getByTestId("capability-egress-docs").textContent).toContain("docs.example.com");
    expect(screen.queryByTestId("capability-egress-atlassian")).toBeNull();
  });

  it("Probar prueba el servidor tal como está guardado, sin salir de la pestaña", async () => {
    wireApi({
      "/projects/proj-1/mcp/test-connection": { server_name: "docs", tools: [{ name: "a" }] },
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("mcp-server-test-docs")).toBeTruthy());
    fireEvent.click(screen.getByTestId("mcp-server-test-docs"));
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(
        "/projects/proj-1/mcp/test-connection",
        expect.objectContaining({ method: "POST" }),
      ),
    );
  });

  it("Tools y Skills listan lo desplegado con versión y agentes", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("capability-card-docs")).toBeTruthy());
    fireEvent.click(screen.getByTestId("capabilities-tab-tools"));
    const tool = await screen.findByTestId("deployed-d2");
    expect(tool.textContent).toContain("Status checker");
    expect(tool.textContent).toContain("2.0.0");
    expect(screen.queryByTestId("deployed-d3")).toBeNull();
    fireEvent.click(screen.getByTestId("capabilities-tab-skills"));
    const skill = await screen.findByTestId("deployed-d3");
    expect(skill.textContent).toContain("Doc contracts");
  });

  it("funde «Capacidades disponibles» del marketplace en cada pestaña, con su kind", async () => {
    const item = (id: string, kind: string, name: string) => ({
      installation_id: id,
      listing_id: `l-${id}`,
      kind,
      name,
      version: "1.0.0",
      description: null,
      trust_level: "verified",
      config_schema: null,
      targets: [],
    });
    wireApi({
      "/projects/proj-1/marketplace/available": [
        item("i1", "mcp_server", "Jira MCP"),
        item("i2", "skill", "Revisor"),
      ],
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("available-i1")).toBeTruthy());
    expect(screen.queryByTestId("available-i2")).toBeNull();
    fireEvent.click(screen.getByTestId("capabilities-tab-skills"));
    await waitFor(() => expect(screen.getByTestId("available-i2")).toBeTruthy());
    expect(screen.queryByTestId("available-i1")).toBeNull();
  });

  it("un proyecto sin capacidades lo dice en cada pestaña", async () => {
    wireApi({
      "/projects/proj-1/capabilities": { entity_type: "project", mcp: [], tools: [], skills: [] },
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("capabilities-empty-mcp")).toBeTruthy());
    fireEvent.click(screen.getByTestId("capabilities-tab-tools"));
    expect(await screen.findByTestId("capabilities-empty-tools")).toBeTruthy();
  });

  it("arranca en la pestaña pedida", async () => {
    wireApi();
    mount("skills");
    expect(await screen.findByTestId("deployed-d3")).toBeTruthy();
  });
});
