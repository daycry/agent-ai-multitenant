// @vitest-environment jsdom

/**
 * Adoptar «para un proyecto» cambia ese proyecto — H9b del recorrido E2E
 * (`docs/roadmap/2026-08-29-hallazgos-e2e-hello-world-v2.md`).
 *
 * El arreglo de H9b vive en el backend (`POST /teams/{id}/adopt` repunta
 * `projects.team_id` al equipo adoptado cuando el destino es un proyecto). Eso
 * deja al panel con una caché que MIENTE: `["projects", …]` sigue guardando el
 * proyecto con su equipo anterior, así que el operador adopta, vuelve a la
 * ficha y ve el built-in del que venía huyendo. La lista de equipos ya
 * invalidaba `["teams", "list"]` — los proyectos no, porque hasta ahora la
 * adopción no los tocaba.
 *
 * El assert mira el ESTADO DE LA CACHÉ (`isInvalidated`) y no la llamada a
 * `invalidateQueries`: lo que importa es que el dato quede marcado como viejo,
 * no por qué vía.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("next/navigation", () => ({
  useParams: () => ({ team_id: "team-b" }),
  useRouter: () => ({ push: vi.fn(), replace: vi.fn(), prefetch: vi.fn() }),
  usePathname: () => "/admin/teams",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: true,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import TeamsListPage from "@/app/admin/teams/page";
import { LanguageProvider } from "@/lib/lang-context";

const BUILTIN_TEAM = {
  id: "team-b",
  tenant_id: "t1",
  name: "CodeIgniter 4",
  description: null,
  members: [],
  is_builtin: true,
};

const PROJECT = { id: "proj-1", name: "hello-world v2", is_template: false, team_id: "team-b" };

/** La clave que la ficha y el listado de proyectos comparten. */
const PROJECTS_KEY = ["projects", "tenant"] as const;

function mount() {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string }) => {
    if (path.startsWith("/teams/team-b/adopt") && opts?.method === "POST") {
      return Promise.resolve({ id: "team-forked", name: "CodeIgniter 4 (copia)" });
    }
    if (path === "/teams") return Promise.resolve([BUILTIN_TEAM]);
    if (path === "/projects") return Promise.resolve([PROJECT]);
    return Promise.resolve([]);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // El proyecto YA está en caché con su equipo anterior: es justo el dato que la
  // adopción acaba de dejar obsoleto en el servidor.
  client.setQueryData([...PROJECTS_KEY], [PROJECT]);
  const view = render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <TeamsListPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
  return { client, view };
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("H9b — adoptar para un proyecto no puede dejar la ficha mintiendo", () => {
  it("invalida la caché de proyectos tras adoptar con destino «un proyecto»", async () => {
    const { client } = mount();

    expect(client.getQueryState([...PROJECTS_KEY])?.isInvalidated).toBe(false);

    fireEvent.click(await screen.findByTestId("team-adopt-team-b"));
    fireEvent.click(await screen.findByTestId("adopt-target-project"));
    const projectSelect = await screen.findByTestId("adopt-team-project");
    await screen.findByRole("option", { name: "hello-world v2" });
    fireEvent.change(projectSelect, { target: { value: "proj-1" } });
    fireEvent.click(screen.getByTestId("adopt-team-submit"));

    await waitFor(() => expect(client.getQueryState([...PROJECTS_KEY])?.isInvalidated).toBe(true));
  });
});
