// @vitest-environment jsdom

/**
 * «Skills y tools» — una pantalla, dos pestañas (`task_ui_30`).
 * Tools por defecto; `?tab=skills` abre Skills. Los campos de Skills son los que
 * `GET /skills` devuelve (nada de uso o actividad inventados).
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});
vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({ isTenantAdmin: true, isLoading: false }),
}));

let tabParam: string | null = null;
vi.mock("next/navigation", () => ({
  useSearchParams: () => ({ get: (k: string) => (k === "tab" ? tabParam : null) }),
}));

import ToolsCatalogPage from "@/app/admin/tools/page";

const TOOL = {
  id: "tool-1",
  tenant_id: "t1",
  name: "read_file",
  description: "Lee un fichero",
  category: "file",
  implementation_type: "builtin",
  implementation_ref: null,
  security_level: "safe",
  is_builtin: true,
  is_runtime_wired: true,
};
const SKILLS = [
  {
    id: "sk-1",
    tenant_id: "plat",
    name: "python-review",
    category: "backend",
    description: "Revisa código Python",
    prompt_fragment: "Eres un revisor",
    required_tools: ["tool-1", "tool-2"],
    is_builtin: true,
  },
  {
    id: "sk-2",
    tenant_id: "t1",
    name: "release-notes",
    category: "docs",
    description: null,
    prompt_fragment: "Redacta notas",
    required_tools: [],
    is_builtin: false,
  },
];

function renderPage() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path.startsWith("/skills")) return Promise.resolve(SKILLS);
    if (path.startsWith("/tools")) return Promise.resolve([TOOL]);
    return Promise.resolve([]);
  });
  window.localStorage.setItem("admin-panel.lang", "es");
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <ToolsCatalogPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
  tabParam = null;
});

describe("Skills y tools — pestañas", () => {
  it("abre Tools por defecto y ofrece las dos pestañas", async () => {
    renderPage();
    expect(screen.getByTestId("tools-page-tab-tools")).toBeDefined();
    expect(screen.getByTestId("tools-page-tab-skills")).toBeDefined();
    expect(await screen.findByTestId("tool-row-tool-1")).toBeDefined();
    expect(screen.queryByTestId("skills-tab")).toBeNull();
  });

  it("?tab=skills abre Skills", async () => {
    tabParam = "skills";
    renderPage();
    expect(await screen.findByTestId("skill-card-sk-1")).toBeDefined();
    expect(screen.queryByTestId("tools-tab")).toBeNull();
  });

  it("un ?tab= desconocido cae en Tools", async () => {
    tabParam = "otra";
    renderPage();
    expect(await screen.findByTestId("tool-row-tool-1")).toBeDefined();
  });

  it("pulsar la pestaña Skills muestra las tarjetas con contador, procedencia y herramientas requeridas", async () => {
    renderPage();
    fireEvent.click(screen.getByTestId("tools-page-tab-skills"));
    const card = within(await screen.findByTestId("skill-card-sk-1"));
    expect(card.getByText("python-review")).toBeDefined();
    expect(card.getByText("Plataforma")).toBeDefined();
    expect(card.getByText("2 tools requeridas")).toBeDefined();
    expect(screen.getByTestId("resource-count").textContent).toContain("2 skills");
    const own = within(screen.getByTestId("skill-card-sk-2"));
    expect(own.getByText("Propio del tenant")).toBeDefined();
    // Sin tools requeridas no se pinta un «0»; y /skills no da actividad.
    expect(own.queryByTestId("resource-field-usage")).toBeNull();
    expect(screen.queryByTestId("resource-field-lastActivity")).toBeNull();
  });

  it("la búsqueda filtra y el panel lateral muestra el resumen", async () => {
    tabParam = "skills";
    renderPage();
    await screen.findByTestId("skill-card-sk-1");
    fireEvent.change(screen.getByTestId("skills-search"), { target: { value: "release" } });
    expect(screen.queryByTestId("skill-card-sk-1")).toBeNull();
    fireEvent.click(screen.getByTestId("resource-select-skill-card-sk-2"));
    const panel = within(await screen.findByTestId("resource-panel"));
    expect(panel.getByText("release-notes")).toBeDefined();
    expect(panel.getByText("Redacta notas")).toBeDefined();
  });
});
