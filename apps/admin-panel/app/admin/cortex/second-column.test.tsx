// @vitest-environment jsdom

/**
 * La SEGUNDA COLUMNA del córtex: Panel de Mente + tarjeta de identidad.
 *
 * Es lo que pedían por escrito las dos casillas de UI —F2 («Componente
 * `MindPanel` montado en `app/admin/cortex`») y F3.6 («integrar en la página de
 * F1, segunda columna»)— y lo que hasta hoy no estaba: las dos vivían sólo como
 * rutas hermanas, así que el owner no veía ni el estado afectivo ni con quién
 * hablaba mientras hablaba.
 *
 * Este test es el que se rompe si alguien deshace el montaje. Y comprueba las
 * tres cosas que importan, no sólo que el componente exista:
 *
 *   - el estado de `/mind` llega a los diales de la columna;
 *   - la identidad de `/identity` llega a la tarjeta;
 *   - el copy honesto de LAS DOS va con ellas — una columna que enseñe afecto o
 *     identidad sin decir que son un modelo computacional incumple la regla de
 *     producto de las dos fases (ADR 0075 §6, ADR 0074).
 *
 * Un no-owner no ve nada de esto: la página corta antes (`cortex-no-access`).
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

const currentUser = {
  isSystemAdmin: true,
  isSystemOwner: true,
  isTenantAdmin: true,
  isTenantMember: true,
  isLoading: false,
};
vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => currentUser,
}));

import CortexChatPage from "@/app/admin/cortex/page";

const MIND = {
  valence: 0.45,
  arousal: 0.15,
  dominance: -0.3,
  intensity: 0.72,
  mood_valence: 0.45,
  mood_arousal: 0.15,
  mood_dominance: -0.3,
  mood_label: "curioso",
  drives: { curiosity: 0.91, bonding: 0.4, coherence: 0.55, competence: 0.6 },
  honesty: { note_es: "Afecto simulado, no real.", note_en: "Simulated affect, not real." },
};

const IDENTITY = {
  name: "Atlas",
  core_values: ["honestidad"],
  narrative: "Soy Atlas.",
  language: "es",
  learning_goals: [],
  traits: {
    openness: 0.8,
    conscientiousness: 0.6,
    extraversion: 0.4,
    agreeableness: 0.7,
    neuroticism: 0.2,
  },
  mood_baseline: { valence: 0.1, arousal: 0.4, dominance: 0.2 },
  relationship_model: {},
  version: 4,
  updated_by: "reflection",
  onboarded_at: "2026-07-01T10:00:00Z",
};

function wireApi() {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/owner/cortex/mind") return Promise.resolve(MIND);
    if (path === "/owner/cortex/identity") return Promise.resolve(IDENTITY);
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CortexChatPage />
    </QueryClientProvider>,
  );
}

configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  currentUser.isSystemOwner = true;
});

describe("Córtex — segunda columna junto al hilo", () => {
  it("monta el Panel de Mente con el estado que devuelve /mind", async () => {
    wireApi();
    mount();

    const column = await waitFor(() => screen.getByTestId("cortex-second-column"));
    await waitFor(() => expect(screen.getByTestId("pad-valence")).toBeTruthy());
    expect(column.contains(screen.getByTestId("cortex-mind-panel"))).toBe(true);
    expect(screen.getByTestId("pad-valence").textContent).toContain("0.45");
    expect(screen.getByTestId("drive-curiosity").textContent).toContain("0.91");
    expect(screen.getByTestId("mood-label").textContent).toContain("curioso");
  });

  it("monta la tarjeta de identidad con lo que devuelve /identity", async () => {
    wireApi();
    mount();

    const column = await waitFor(() => screen.getByTestId("cortex-second-column"));
    await waitFor(() => expect(screen.getByTestId("cortex-identity-card-name")).toBeTruthy());
    expect(column.contains(screen.getByTestId("cortex-identity-card"))).toBe(true);
    expect(screen.getByTestId("cortex-identity-card-name").textContent).toContain("Atlas");
    // El radar viaja con la tarjeta: es la forma del perfil, no cinco barras.
    expect(screen.getByTestId("cortex-trait-value-openness").textContent).toContain("0.80");
  });

  it("los dos avisos honestos están en la columna, no sólo en las rutas hermanas", async () => {
    wireApi();
    mount();

    // Antes de que `/mind` responda el aviso ya está, con el respaldo del
    // diccionario; cuando responde, manda la nota del backend. Las dos caras son
    // el mismo invariante: nunca hay diales sin aviso.
    await waitFor(() => expect(screen.getByTestId("cortex-mind-honesty")).toBeTruthy());
    await waitFor(() =>
      expect(screen.getByTestId("cortex-mind-honesty").textContent).toContain(
        "Afecto simulado, no real.",
      ),
    );
    await waitFor(() => expect(screen.getByTestId("cortex-identity-card-honesty")).toBeTruthy());
    expect(screen.getByTestId("cortex-identity-card-honesty").textContent).toContain(
      "modelo computacional",
    );
  });

  it("el chat sigue estando: la columna se añade, no sustituye a nada", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("cortex-input")).toBeTruthy());
    expect(screen.getByTestId("cortex-chat")).toBeTruthy();
    expect(screen.getByTestId("cortex-history-bar")).toBeTruthy();
  });

  it("un no-owner no ve la columna (ni sus diales ni su identidad)", async () => {
    // La barrera real es el backend (`require_system_owner`, ADR 0074); esto es
    // la UX que la refleja. Que el gate ya existiera no lo hace menos digno de
    // test: la columna es contenido owner-only NUEVO en esta pantalla.
    currentUser.isSystemOwner = false;
    wireApi();
    mount();

    await waitFor(() => expect(screen.getByTestId("cortex-no-access")).toBeTruthy());
    expect(screen.queryByTestId("cortex-second-column")).toBeNull();
    expect(screen.queryByTestId("pad-valence")).toBeNull();
    expect(screen.queryByTestId("cortex-identity-card")).toBeNull();
  });
});
