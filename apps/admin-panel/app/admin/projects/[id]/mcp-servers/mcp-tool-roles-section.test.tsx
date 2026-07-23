// @vitest-environment jsdom
// ADR 0128 fase 4 — editor OPCIONAL de la política rol→tool de las MCP del
// proyecto (`McpToolRolePolicySection`). Clava:
//   - lista SOLO las tools MCP del catálogo cuyo `<server>` está declarado en
//     el proyecto (excluye builtins/custom y MCP de servers no declarados);
//   - pre-siembra la política existente (`mcp_tool_roles`) en los checkboxes;
//   - marcar/desmarcar un rol ensucia el form y Guardar hace
//     `PUT /projects/{id}` con `{ mcp_tool_roles }` (set completo);
//   - estado vacío cuando el proyecto no tiene tools MCP importadas.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { McpToolRolePolicySection } from "@/app/admin/projects/[id]/mcp-servers/mcp-server-sections";

const MCP_TOOLS = [
  {
    id: "t1",
    name: "github-mcp.create_issue",
    description: "Crea un issue",
    category: "mcp",
    implementation_type: "mcp_tool",
  },
  {
    id: "t2",
    name: "github-mcp.list_prs",
    description: null,
    category: "mcp",
    implementation_type: "mcp_tool",
  },
];

// Ruido que NUNCA debe aparecer en el editor: builtin, y una MCP de un server
// que el proyecto NO declara.
const NOISE_TOOLS = [
  {
    id: "b1",
    name: "read_file",
    description: "Lee un fichero",
    category: "file",
    implementation_type: "builtin",
  },
  {
    id: "x1",
    name: "other-server.foo",
    description: null,
    category: "mcp",
    implementation_type: "mcp_tool",
  },
];

function wireApi({
  toolRoles = {} as Record<string, string[]>,
  tools = [...MCP_TOOLS, ...NOISE_TOOLS],
  servers = [{ name: "github-mcp", transport: "stdio", args: [] }],
}: {
  toolRoles?: Record<string, string[]>;
  tools?: Record<string, unknown>[];
  servers?: Record<string, unknown>[];
} = {}) {
  const project = {
    id: "proj-1",
    name: "Proyecto",
    mcp_servers: servers,
    mcp_tool_roles: toolRoles,
  };
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/projects/proj-1" && opts?.method === "PUT") {
      return Promise.resolve({
        ...project,
        mcp_tool_roles: (opts.body as { mcp_tool_roles: Record<string, string[]> }).mcp_tool_roles,
      });
    }
    if (path === "/projects/proj-1") return Promise.resolve(project);
    if (path === "/tools?limit=500") return Promise.resolve(tools);
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <McpToolRolePolicySection projectId="proj-1" />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("McpToolRolePolicySection (ADR 0128 fase 4)", () => {
  it("lists only the project's MCP tools (excludes builtins + undeclared servers)", async () => {
    wireApi();
    mount();
    await waitFor(() =>
      expect(screen.getByTestId("mcp-tool-roles-tool-github-mcp.create_issue")).toBeTruthy(),
    );
    expect(screen.getByTestId("mcp-tool-roles-tool-github-mcp.list_prs")).toBeTruthy();
    // read_file (builtin) y other-server.foo (server no declarado) NO aparecen.
    expect(screen.queryByTestId("mcp-tool-roles-tool-read_file")).toBeNull();
    expect(screen.queryByTestId("mcp-tool-roles-tool-other-server.foo")).toBeNull();
  });

  it("pre-seeds the existing role policy into the checkboxes", async () => {
    wireApi({ toolRoles: { "github-mcp.create_issue": ["backend_dev"] } });
    mount();
    await waitFor(() =>
      expect(screen.getByTestId("mcp-tool-roles-tool-github-mcp.create_issue")).toBeTruthy(),
    );
    const backendCb = screen.getByTestId(
      "mcp-tool-roles-role-github-mcp.create_issue-backend_dev",
    ) as HTMLInputElement;
    expect(backendCb.checked).toBe(true);
    const qaCb = screen.getByTestId(
      "mcp-tool-roles-role-github-mcp.create_issue-qa",
    ) as HTMLInputElement;
    expect(qaCb.checked).toBe(false);
    // Una tool sin entrada en la política queda "Abierta a todos".
    expect(screen.getByTestId("mcp-tool-roles-open-github-mcp.list_prs")).toBeTruthy();
  });

  it("toggling a role dirties the form and Save PUTs the whole policy", async () => {
    wireApi({ toolRoles: { "github-mcp.create_issue": ["backend_dev"] } });
    mount();
    await waitFor(() =>
      expect(screen.getByTestId("mcp-tool-roles-tool-github-mcp.list_prs")).toBeTruthy(),
    );

    // Save arranca deshabilitado (no dirty).
    expect((screen.getByTestId("mcp-tool-roles-save") as HTMLButtonElement).disabled).toBe(true);

    // Restringe list_prs a qa.
    fireEvent.click(screen.getByTestId("mcp-tool-roles-role-github-mcp.list_prs-qa"));
    expect((screen.getByTestId("mcp-tool-roles-save") as HTMLButtonElement).disabled).toBe(false);

    fireEvent.click(screen.getByTestId("mcp-tool-roles-save"));
    await waitFor(() => {
      const putCall = apiFetchMock.mock.calls.find(
        ([p, o]) => p === "/projects/proj-1" && (o as { method?: string })?.method === "PUT",
      );
      expect(putCall).toBeTruthy();
      const body = putCall?.[1]?.body as { mcp_tool_roles: Record<string, string[]> };
      expect(body.mcp_tool_roles).toEqual({
        "github-mcp.create_issue": ["backend_dev"],
        "github-mcp.list_prs": ["qa"],
      });
    });
    await waitFor(() => expect(screen.getByTestId("mcp-tool-roles-saved")).toBeTruthy());
  });

  it("unchecking the last role drops the tool from the policy (open-to-all)", async () => {
    wireApi({ toolRoles: { "github-mcp.create_issue": ["backend_dev"] } });
    mount();
    await waitFor(() =>
      expect(
        screen.getByTestId("mcp-tool-roles-role-github-mcp.create_issue-backend_dev"),
      ).toBeTruthy(),
    );
    fireEvent.click(screen.getByTestId("mcp-tool-roles-role-github-mcp.create_issue-backend_dev"));
    fireEvent.click(screen.getByTestId("mcp-tool-roles-save"));
    await waitFor(() => {
      const putCall = apiFetchMock.mock.calls.find(
        ([p, o]) => p === "/projects/proj-1" && (o as { method?: string })?.method === "PUT",
      );
      const body = putCall?.[1]?.body as { mcp_tool_roles: Record<string, string[]> };
      expect(body.mcp_tool_roles).toEqual({});
    });
  });

  it("shows the empty state when the project has no imported MCP tools", async () => {
    wireApi({ tools: NOISE_TOOLS });
    mount();
    await waitFor(() => expect(screen.getByTestId("mcp-tool-roles-empty")).toBeTruthy());
    expect(screen.queryByTestId("mcp-tool-roles-list")).toBeNull();
  });
});
