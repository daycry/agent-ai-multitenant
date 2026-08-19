// @vitest-environment jsdom

/**
 * `IdentityCard` — la tarjeta de identidad del córtex (Córtex F3.6, ADR 0074/0077).
 *
 * La casilla del plan pedía «radar Big-Five + narrativa Markdown + copy honesto
 * (ES+EN)» montado en la SEGUNDA COLUMNA de la página de F1. Lo que había el
 * 2026-07-30 era otra cosa: una ruta hermana `/admin/cortex/identity` con el
 * formulario de edición, su copy honesto cableado en castellano
 * (`HONESTY_NOTE`), y NINGÚN test de render de la tarjeta — sólo del radar y del
 * timeline por separado.
 *
 * Este fichero clava lo que la casilla pedía de verdad:
 *
 *   - la tarjeta pinta la identidad (nombre, valores, objetivos, narrativa) y el
 *     radar de rasgos con el dato que devuelve el endpoint;
 *   - el **copy honesto se pinta SIEMPRE y en los dos idiomas** — es la regla de
 *     producto de la fase (la identidad es un modelo computacional, no un «yo»),
 *     y es justo lo que estaba a medias;
 *   - la narrativa se renderiza como Markdown, no como texto plano con asteriscos;
 *   - un fallo de carga NO se confunde con «no hay identidad todavía»: son dos
 *     estados distintos y decirle al owner que su córtex no tiene identidad
 *     cuando lo que pasó es un 500 es mentirle.
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

import { ApiError } from "@/lib/api";
import { IdentityCard } from "@/components/cortex/identity-card";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

const IDENTITY = {
  name: "Atlas",
  core_values: ["honestidad", "rigor"],
  narrative: "Soy **Atlas**, el córtex de este despliegue.",
  language: "es",
  learning_goals: ["entender los proyectos del owner"],
  traits: {
    openness: 0.8,
    conscientiousness: 0.6,
    extraversion: 0.4,
    agreeableness: 0.7,
    neuroticism: 0.2,
  },
  mood_baseline: { valence: 0.1, arousal: 0.4, dominance: 0.2 },
  relationship_model: {},
  version: 7,
  updated_by: "reflection",
  onboarded_at: "2026-07-01T10:00:00Z",
};

function mount(lang: "es" | "en" = "es") {
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <IdentityCard />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("IdentityCard — copy honesto en ES y EN (regla de producto de la fase)", () => {
  it("en castellano avisa de que la identidad es un modelo computacional", async () => {
    apiFetchMock.mockResolvedValue(IDENTITY);
    mount("es");
    await waitFor(() => expect(screen.getByTestId("cortex-identity-card-honesty")).toBeTruthy());
    const note = screen.getByTestId("cortex-identity-card-honesty").textContent ?? "";
    expect(note).toContain("modelo computacional");
    expect(note).toContain("no es consciencia");
  });

  it("en inglés lo dice traducido, sin castellano suelto", async () => {
    apiFetchMock.mockResolvedValue(IDENTITY);
    mount("en");
    await waitFor(() =>
      expect(screen.getByTestId("cortex-identity-card-honesty").textContent).toContain(
        "computational model",
      ),
    );
    const note = screen.getByTestId("cortex-identity-card-honesty").textContent ?? "";
    expect(note).toContain("not consciousness");
    expect(note).not.toContain("modelo computacional");
  });

  it("en inglés tampoco quedan rótulos en castellano dentro del radar", async () => {
    // `traitRadarAxes` rotula con `TRAIT_LABELS_ES`, castellano fijo: con el
    // panel en inglés dejaba cinco palabras sin traducir DENTRO del gráfico.
    apiFetchMock.mockResolvedValue(IDENTITY);
    mount("en");
    const radar = await waitFor(() => screen.getByTestId("cortex-trait-radar"));
    await waitFor(() => expect(radar.textContent).toContain("Openness"));
    expect(radar.textContent).toContain("Conscientiousness");
    for (const spanish of ["Apertura", "Responsabilidad", "Extraversión", "Neuroticismo"]) {
      expect(radar.textContent).not.toContain(spanish);
    }
  });

  it("el aviso está aunque la identidad no haya cargado (no hay tarjeta sin aviso)", async () => {
    apiFetchMock.mockRejectedValue(new ApiError(500, "boom"));
    mount("es");
    await waitFor(() => expect(screen.getByTestId("cortex-identity-card-error")).toBeTruthy());
    expect(screen.getByTestId("cortex-identity-card-honesty")).toBeTruthy();
  });
});

describe("IdentityCard — pinta la identidad que devuelve el endpoint", () => {
  it("nombre, valores y objetivos de aprendizaje", async () => {
    apiFetchMock.mockResolvedValue(IDENTITY);
    mount();
    await waitFor(() => expect(screen.getByTestId("cortex-identity-card-name")).toBeTruthy());
    const card = screen.getByTestId("cortex-identity-card");
    expect(card.textContent).toContain("Atlas");
    expect(card.textContent).toContain("honestidad");
    expect(card.textContent).toContain("rigor");
    expect(card.textContent).toContain("entender los proyectos del owner");
    // La versión sitúa lo que se está viendo en el histórico del timeline.
    expect(card.textContent).toContain("7");
  });

  it("el radar Big-Five con el valor de cada rasgo", async () => {
    apiFetchMock.mockResolvedValue(IDENTITY);
    mount();
    await waitFor(() => expect(screen.getByTestId("cortex-trait-radar")).toBeTruthy());
    expect(screen.getByTestId("cortex-trait-value-openness").textContent).toContain("0.80");
    expect(screen.getByTestId("cortex-trait-value-neuroticism").textContent).toContain("0.20");
  });

  it("la narrativa se renderiza como Markdown, no como texto crudo", async () => {
    apiFetchMock.mockResolvedValue(IDENTITY);
    mount();
    const narrative = await waitFor(() => screen.getByTestId("cortex-identity-card-narrative"));
    expect(narrative.querySelector("strong")?.textContent).toBe("Atlas");
    expect(narrative.textContent).not.toContain("**");
  });

  it("pega al endpoint de identidad del owner", async () => {
    apiFetchMock.mockResolvedValue(IDENTITY);
    mount();
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalled());
    expect(apiFetchMock.mock.calls[0][0]).toBe("/owner/cortex/identity");
  });
});

describe("IdentityCard — estados que NO se pueden confundir", () => {
  it("sin onboarding invita a ponerle nombre, y no dice que falle nada", async () => {
    apiFetchMock.mockResolvedValue({
      ...IDENTITY,
      name: null,
      core_values: [],
      narrative: "",
      onboarded_at: null,
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("cortex-identity-card-onboarding")).toBeTruthy());
    expect(screen.queryByTestId("cortex-identity-card-error")).toBeNull();
  });

  it("un error de carga se dice como error, no como identidad vacía", async () => {
    apiFetchMock.mockRejectedValue(new ApiError(500, "boom"));
    mount();
    await waitFor(() => expect(screen.getByTestId("cortex-identity-card-error")).toBeTruthy());
    expect(screen.queryByTestId("cortex-identity-card-onboarding")).toBeNull();
  });

  it("lleva al editor de identidad, que sigue viviendo en su ruta hermana", async () => {
    apiFetchMock.mockResolvedValue(IDENTITY);
    mount();
    const link = await waitFor(() => screen.getByTestId("cortex-identity-card-edit"));
    expect(link.getAttribute("href")).toBe("/admin/cortex/identity");
  });
});
