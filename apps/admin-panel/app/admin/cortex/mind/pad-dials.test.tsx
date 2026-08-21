// @vitest-environment jsdom

/**
 * El último tramo del Panel de Mente: que el dato de `/mind` LLEGUE a los diales.
 *
 * `mind-panel.test.tsx` afirma que el componente pinta lo que se le pasa. Eso no
 * basta, y este repo tiene el patrón documentado de por qué
 * (docs/03-guides/verificar-antes-de-implementar.md §5, «mecanismo entregado,
 * cero llamantes»): el componente puede estar perfecto y la pantalla pasarle
 * `null`, o dejar de pasarle nada en el siguiente refactor, y ningún test se
 * entera. Hasta hoy los `pad-*` sólo se miraban en la e2e de Playwright, que
 * está «written, not run» en este entorno.
 *
 * Así que aquí se monta la PANTALLA con `/mind` mockeado y se comprueba que el
 * número mockeado sale por el dial — y que el aviso honesto viene con él.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/ws", () => ({
  useWebSocket: () => {},
  wsUrl: (p: string) => `ws://test${p}`,
}));

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: true,
    isSystemOwner: true,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import CortexMindPage from "@/app/admin/cortex/mind/page";

/** Valores deliberadamente distintos entre sí y ninguno el neutro. */
const MIND = {
  valence: 0.5,
  arousal: 0.25,
  dominance: -0.4,
  intensity: 0.9,
  mood_valence: 0.5,
  mood_arousal: 0.25,
  mood_dominance: -0.4,
  mood_label: "concentrado",
  drives: { curiosity: 0.81, bonding: 0.44, coherence: 0.33, competence: 0.66 },
  honesty: { note_es: "Afecto simulado.", note_en: "Simulated affect." },
};

function wireApi() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/owner/cortex/mind") return Promise.resolve(MIND);
    if (path.startsWith("/owner/cortex/autonomy")) {
      return Promise.resolve({
        autonomy_enabled: false,
        web_enabled: false,
        browser_enabled: false,
        curiosity_drive_threshold: 0.7,
        circuit_breaker_open: false,
        budget: { searches_today: 0, searches_cap: 10 },
        note_es: "",
        note_en: "",
      });
    }
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CortexMindPage />
    </QueryClientProvider>,
  );
}

configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("Panel de Mente — el estado de `/mind` llega a los diales", () => {
  it("los cuatro diales PAD pintan el valor que devolvió el endpoint", async () => {
    wireApi();
    mount();

    await waitFor(() => expect(screen.getByTestId("pad-valence")).toBeTruthy());
    expect(screen.getByTestId("pad-valence").textContent).toContain("0.50");
    expect(screen.getByTestId("pad-arousal").textContent).toContain("0.25");
    expect(screen.getByTestId("pad-dominance").textContent).toContain("-0.40");
    expect(screen.getByTestId("pad-intensity").textContent).toContain("0.90");
    expect(screen.getByTestId("mood-label").textContent).toContain("concentrado");
  });

  it("las sensaciones (drives) también, y con la nota honesta del backend delante", async () => {
    wireApi();
    mount();

    await waitFor(() => expect(screen.getByTestId("drive-curiosity")).toBeTruthy());
    expect(screen.getByTestId("drive-curiosity").textContent).toContain("0.81");
    expect(screen.getByTestId("drive-competence").textContent).toContain("0.66");
    // El aviso honesto viaja con los diales, no en otra parte de la pantalla.
    expect(screen.getByTestId("cortex-mind-honesty").textContent).toContain("Afecto simulado.");
  });
});
