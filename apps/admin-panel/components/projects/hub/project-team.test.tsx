// @vitest-environment jsdom
/**
 * La pestaña Equipo del proyecto (`task_ui_12`): equipo asignado, agentes por rol,
 * agentes humanos y huecos de rol con enlace a la ficha del agente.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  apiFetch: (...args: unknown[]) => apiFetchMock(...args),
}));

import { ProjectTeam } from "@/components/projects/hub/project-team";
import { LanguageProvider } from "@/lib/lang-context";
import type { ProjectTeamData } from "@/lib/projects/project-hub";

configure({ asyncUtilTimeout: 5000 });
afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

const PID = "11111111-2222-3333-4444-555555555555";

const DATA: ProjectTeamData = {
  team: { id: "tm1", name: "Equipo Web" },
  members: [
    {
      agent_id: "a1",
      name: "Ana",
      role: "backend_dev",
      agent_type: "ai",
      is_team_leader: true,
      url_path: "/admin/agents/a1",
    },
    {
      agent_id: "a2",
      name: "Beto",
      role: "backend_dev",
      agent_type: "ai",
      is_team_leader: false,
      url_path: "/admin/agents/a2",
    },
    {
      agent_id: "a3",
      name: "Hugo Humano",
      role: "reviewer",
      agent_type: "human",
      is_team_leader: false,
      url_path: "/admin/agents/a3",
    },
  ],
  role_gaps: [
    {
      role: "qa",
      plans: ["Migrar login"],
      candidates: [{ agent_id: "a9", name: "Quique QA", url_path: "/admin/agents/a9" }],
    },
    { role: "devops", plans: ["Migrar login"], candidates: [] },
  ],
};

function renderTeam(data: ProjectTeamData) {
  apiFetchMock.mockResolvedValue(data);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <ProjectTeam projectId={PID} />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

describe("ProjectTeam", () => {
  it("pide el equipo de ESE proyecto y enseña el equipo asignado", async () => {
    renderTeam(DATA);
    await screen.findByTestId("team-assigned");
    expect(apiFetchMock.mock.calls[0]?.[0]).toBe(`/projects/${PID}/team`);
    expect(screen.getByTestId("team-assigned").textContent).toContain("Equipo Web");
  });

  it("agrupa los agentes de IA por rol y aparta a los humanos", async () => {
    renderTeam(DATA);
    const ai = await screen.findByTestId("team-ai");
    expect(ai.textContent).toContain("Ana");
    expect(ai.textContent).toContain("Beto");
    expect(ai.textContent).not.toContain("Hugo Humano");
    expect(screen.getByTestId("team-role-backend_dev").textContent).toContain("Ana");
    const humans = screen.getByTestId("team-humans");
    expect(humans.textContent).toContain("Hugo Humano");
    expect(screen.getByTestId("team-member-a1").getAttribute("href")).toBe("/admin/agents/a1");
  });

  it("enseña los huecos de rol con enlace a la ficha del agente candidato", async () => {
    renderTeam(DATA);
    const gap = await screen.findByTestId("team-gap-qa");
    expect(gap.textContent).toContain("Migrar login");
    expect(screen.getByTestId("team-gap-candidate-a9").getAttribute("href")).toBe(
      "/admin/agents/a9",
    );
    expect(screen.getByTestId("team-gap-devops").textContent).toContain(
      "No hay ningún agente con ese rol",
    );
  });

  it("sin huecos lo dice; sin equipo avisa", async () => {
    renderTeam({ team: null, members: [], role_gaps: [] });
    expect(await screen.findByTestId("team-none")).toBeTruthy();
    expect(screen.getByTestId("team-gaps-none")).toBeTruthy();
  });
});
