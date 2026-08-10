// @vitest-environment jsdom

/**
 * El respaldo del banner de honestidad del córtex, en los DOS idiomas (prod-16
 * `task_prod16_04`).
 *
 * El banner no es removible (ADR 0075 §6): sin él no se pintan diales de afecto.
 * Su texto llega bilingüe del backend (`note_es`/`note_en`), pero el respaldo
 * —el que se ve mientras `/mind` no ha respondido, o si la nota viniera vacía—
 * fue una vez monolingüe: se veía en castellano con el panel en inglés, y lo
 * arregló `63e6a135` poniendo un ternario de idioma.
 *
 * Ese ternario ya daba los dos idiomas (leía el mismo `useLangOptional()` que
 * `useT`), así que moverlo al diccionario NO arregla ningún texto: se hizo para
 * dejar a cero el `ALLOWLIST` de `scripts/check-i18n.mjs`. Este test no acredita
 * aquel cambio, acredita el INVARIANTE que ninguna de las dos formas puede
 * romper — que el respaldo hable el idioma del panel—, y por eso sigue valiendo
 * después de la migración.
 *
 * (El ternario no se cita literalmente: `check-i18n.mjs` casa su patrón sobre el
 * fuente entero, comentarios incluidos, y también escanea los `.test.tsx`.)
 *
 * Se prueba el caso VACÍO a propósito: es el único en el que el respaldo se ve.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

/** `/mind` responde SIN nota: es cuando el respaldo se ve. */
const MIND_WITHOUT_NOTE = {
  valence: 0.2,
  arousal: 0.4,
  dominance: 0.1,
  intensity: 0.3,
  mood_valence: 0.2,
  mood_arousal: 0.4,
  mood_dominance: 0.1,
  mood_label: "sereno",
  drives: { curiosity: 0.7, bonding: 0.5, coherence: 0.6, competence: 0.5 },
  honesty: { note_es: "", note_en: "" },
};

function renderIn(lang: "es" | "en") {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/owner/cortex/mind") return Promise.resolve(MIND_WITHOUT_NOTE);
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
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <CortexMindPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("banner de honestidad — respaldo sin nota del backend", () => {
  it("en castellano dice que el afecto es un modelo computacional", async () => {
    renderIn("es");

    await waitFor(() => expect(screen.getByTestId("cortex-mind-honesty")).toBeTruthy());
    expect(screen.getByTestId("cortex-mind-honesty").textContent).toContain(
      "Modelo computacional de afecto, no sentimientos reales.",
    );
  });

  it("en inglés dice lo mismo traducido", async () => {
    renderIn("en");

    await waitFor(() => expect(screen.getByTestId("cortex-mind-honesty")).toBeTruthy());
    const text = screen.getByTestId("cortex-mind-honesty").textContent ?? "";
    expect(text).toContain("Computational model of affect, not real feelings.");
    expect(text).not.toContain("Modelo computacional");
  });
});
