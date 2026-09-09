// @vitest-environment jsdom
// task_mk_13 (UI-04): la sección de skills del agente enseña DE DÓNDE viene una
// skill cuando la materializó el marketplace (ADR 0100): listing + versión. Una
// skill nativa no lleva insignia.
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
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

import { LanguageProvider } from "@/lib/lang-context";
import { AgentSkillsSection } from "@/app/admin/agents/[id]/agent-skills-section";

const CATALOG = [
  {
    id: "s1",
    name: "doc-contracts",
    category: "docs",
    description: "Documenta contratos",
    prompt_fragment: "Documenta SIEMPRE los contratos públicos.",
    is_builtin: false,
  },
  {
    id: "s2",
    name: "pytest-style",
    category: "qa",
    description: "Tests con pytest",
    prompt_fragment: "Un assert por comportamiento.",
    is_builtin: true,
  },
];

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <AgentSkillsSection agentId="agent-1" isReadOnly={false} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("AgentSkillsSection — procedencia del marketplace (task_mk_13)", () => {
  it("shows listing + version on a materialised skill and nothing on a native one", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/skills?limit=500") return Promise.resolve(CATALOG);
      if (path === "/agents/agent-1/skills")
        return Promise.resolve([
          {
            skill_id: "s1",
            name: "doc-contracts",
            category: "docs",
            description: "Documenta contratos",
            prompt_fragment: "Documenta SIEMPRE los contratos públicos.",
            is_builtin: false,
            source_installation_id: "inst-9",
            source_listing_name: "doc-contracts",
            source_version: "2.0.0",
          },
          {
            skill_id: "s2",
            name: "pytest-style",
            category: "qa",
            description: "Tests con pytest",
            prompt_fragment: "Un assert por comportamiento.",
            is_builtin: true,
            source_installation_id: null,
            source_listing_name: null,
            source_version: null,
          },
        ]);
      return Promise.resolve([]);
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("agent-skills-list")).toBeTruthy());

    const badge = await screen.findByTestId("agent-skill-provenance-badge-s1");
    expect(badge.textContent).toContain("Marketplace");
    expect(badge.textContent).toContain("doc-contracts");
    expect(badge.textContent).toContain("v2.0.0");
    expect(badge.getAttribute("title")).toContain("doc-contracts");

    expect(screen.getByTestId("agent-skill-row-s2")).toBeTruthy();
    expect(screen.queryByTestId("agent-skill-provenance-badge-s2")).toBeNull();
  });
});
