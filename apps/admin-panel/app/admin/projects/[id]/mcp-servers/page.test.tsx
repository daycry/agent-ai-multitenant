// @vitest-environment jsdom
// Caracterización de MCP servers por proyecto (tramo #9, auditoría 2026-07-10):
// red de tests ANTES de modularizar el monolito de 1105 líneas. Clava:
//   - una card por server configurado y el estado vacío;
//   - el dialog: stdio muestra `command`, cambiar a sse muestra `url`, y el
//     submit hace PUT del array `mcp_servers` ENTERO (contrato del backend);
//   - «Probar conexión»: POST /mcp/test-connection y el panel con las tools
//     descubiertas (server, versión, contador y filas).

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "proj-1" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/admin/projects/proj-1/mcp-servers",
}));

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import ProjectMcpServersPage from "@/app/admin/projects/[id]/mcp-servers/page";

function server(overrides: Record<string, unknown> = {}) {
  return {
    name: "files",
    transport: "stdio",
    command: "npx mcp-files",
    args: [],
    env: {},
    url: null,
    headers: {},
    auth_ref: null,
    timeout_s: 30,
    ...overrides,
  };
}

function project(servers: Record<string, unknown>[]) {
  return { id: "proj-1", name: "Proyecto", mcp_servers: servers };
}

function wireApi(servers: Record<string, unknown>[]) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/projects/proj-1" && opts?.method === "PUT") {
      return Promise.resolve(project((opts.body as { mcp_servers: [] }).mcp_servers));
    }
    if (path === "/projects/proj-1") return Promise.resolve(project(servers));
    if (path === "/mcp-catalog") return Promise.resolve([]);
    if (path === "/projects/proj-1/mcp/test-connection" && opts?.method === "POST") {
      return Promise.resolve({
        server_name: "files-server",
        server_version: "1.2.0",
        server_instructions: null,
        tools: [
          { name: "read_file", description: "Lee un fichero" },
          { name: "list_dir", description: null },
        ],
      });
    }
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

describe("MCP servers del proyecto (caracterización tramo #9)", () => {
  it("renders one card per configured server", async () => {
    wireApi([
      server(),
      server({ name: "jira", transport: "sse", url: "https://x", command: null }),
    ]);
    mount();
    await waitFor(() => expect(screen.getByTestId("mcp-server-card-files")).toBeTruthy());
    expect(screen.getByTestId("mcp-server-card-jira")).toBeTruthy();
    expect(screen.queryByTestId("project-mcp-empty")).toBeNull();
  });

  it("shows the empty state when the project has no servers", async () => {
    wireApi([]);
    mount();
    await waitFor(() => expect(screen.getByTestId("project-mcp-empty")).toBeTruthy());
  });

  it("add dialog switches transport fields and PUTs the whole array on submit", async () => {
    wireApi([server()]);
    mount();
    await waitFor(() => expect(screen.getByTestId("mcp-add-button")).toBeTruthy());
    fireEvent.click(screen.getByTestId("mcp-add-button"));
    await waitFor(() => expect(screen.getByTestId("mcp-server-dialog")).toBeTruthy());

    // stdio (default) → command visible, url no.
    expect(screen.getByTestId("mcp-form-command")).toBeTruthy();
    expect(screen.queryByTestId("mcp-form-url")).toBeNull();

    fireEvent.change(screen.getByTestId("mcp-form-name"), { target: { value: "brave" } });
    fireEvent.change(screen.getByTestId("mcp-form-transport"), { target: { value: "sse" } });
    await waitFor(() => expect(screen.getByTestId("mcp-form-url")).toBeTruthy());
    expect(screen.queryByTestId("mcp-form-command")).toBeNull();
    fireEvent.change(screen.getByTestId("mcp-form-url"), {
      target: { value: "https://mcp.brave.com/sse" },
    });

    fireEvent.click(screen.getByTestId("mcp-form-submit"));
    await waitFor(() => {
      const putCall = apiFetchMock.mock.calls.find(
        ([p, o]) => p === "/projects/proj-1" && (o as { method?: string })?.method === "PUT",
      );
      expect(putCall).toBeTruthy();
      const body = putCall?.[1]?.body as { mcp_servers: Record<string, unknown>[] };
      // El array viaja ENTERO: el server existente + el nuevo.
      expect(body.mcp_servers).toHaveLength(2);
      expect(body.mcp_servers[1]).toMatchObject({
        name: "brave",
        transport: "sse",
        url: "https://mcp.brave.com/sse",
        command: null,
      });
    });
  });

  it("test connection renders the discovered tools panel", async () => {
    wireApi([]);
    mount();
    await waitFor(() => expect(screen.getByTestId("mcp-add-button")).toBeTruthy());
    fireEvent.click(screen.getByTestId("mcp-add-button"));
    await waitFor(() => expect(screen.getByTestId("mcp-server-dialog")).toBeTruthy());
    fireEvent.change(screen.getByTestId("mcp-form-name"), { target: { value: "files" } });
    fireEvent.change(screen.getByTestId("mcp-form-command"), {
      target: { value: "npx mcp-files" },
    });
    fireEvent.click(screen.getByTestId("mcp-form-test"));
    await waitFor(() => expect(screen.getByTestId("mcp-form-test-result")).toBeTruthy());
    expect(screen.getByTestId("mcp-form-test-server-name").textContent).toBe("files-server");
    expect(screen.getByTestId("mcp-form-test-server-version").textContent).toBe("1.2.0");
    expect(screen.getByTestId("mcp-form-test-tool-count").textContent).toBe("2");
    expect(screen.getByTestId("mcp-form-test-tool-read_file")).toBeTruthy();
  });
});
