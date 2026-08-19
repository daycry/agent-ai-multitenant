// @vitest-environment jsdom

/**
 * `MindPanel` — el Panel de Mente como COMPONENTE (Córtex F2, ADR 0075 §6).
 *
 * La casilla del plan («Componente `MindPanel` montado en `app/admin/cortex`»)
 * pedía dos cosas que hasta hoy no estaban:
 *
 *   1. un test de render del panel COMPLETO con datos mockeados — el vitest que
 *      había cubría el espacio PAD 2D (`mind-pad-space.test.tsx`) y la tarjeta de
 *      curiosidad (`mind/page.test.tsx`), pero NADIE afirmaba que los diales
 *      pintaran el dato. Los `pad-*` sólo se miraban en la e2e de Playwright,
 *      que en este repo está «written, not run»;
 *   2. ES+EN. El panel estaba cableado en castellano salvo el aviso honesto.
 *
 * Los dos invariantes que este fichero clava, y que se rompen si alguien deshace
 * el trabajo:
 *
 *   - **el aviso honesto se pinta SIEMPRE**, con datos o sin ellos: es la regla
 *     de producto del ADR 0075 §6 (no se venden emociones reales), y sin test se
 *     cae en el primer refactor de la pantalla;
 *   - **el dial refleja el dato**, número Y anchura de la barra. Un dial que
 *     siempre pinta lo mismo es peor que no tener dial: miente sobre el estado
 *     del córtex.
 */

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { MindPanel } from "@/components/cortex/mind-panel";
import { padToPercent, type CortexMind } from "@/lib/cortex";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

/** Estado mockeado: cuatro valores DISTINTOS entre sí y ninguno el default. */
const MIND: CortexMind = {
  valence: 0.2,
  arousal: 0.4,
  dominance: -0.6,
  intensity: 0.35,
  mood_valence: 0.2,
  mood_arousal: 0.4,
  mood_dominance: 0.1,
  mood_label: "sereno",
  drives: { curiosity: 0.7, bonding: 0.5, coherence: 0.62, competence: 0.25 },
  honesty: { note_es: "Simulación determinista.", note_en: "Deterministic simulation." },
};

function renderPanel(mind: CortexMind | null, lang: "es" | "en" = "es") {
  window.localStorage.setItem(STORAGE_KEY, lang);
  return render(
    <LanguageProvider>
      <MindPanel mind={mind} />
    </LanguageProvider>,
  );
}

/** Anchura de la barra de un dial, en %, tal y como la escribe el estilo. */
function barWidth(testid: string): string {
  const bar = screen.getByTestId(`${testid}-bar`) as HTMLElement;
  return bar.style.width;
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("MindPanel — copy honesto no removible (ADR 0075 §6)", () => {
  it("pinta el aviso honesto aunque NO haya estado que enseñar", () => {
    renderPanel(null);
    expect(screen.getByTestId("cortex-mind-honesty")).toBeTruthy();
    // Y sin datos no se inventa un dial: eso sería enseñar afecto falso.
    expect(screen.queryByTestId("pad-valence")).toBeNull();
  });

  it("usa la nota bilingüe del backend en el idioma activo", async () => {
    renderPanel(MIND, "en");
    await waitFor(() =>
      expect(screen.getByTestId("cortex-mind-honesty").textContent).toContain(
        "Deterministic simulation.",
      ),
    );
    expect(screen.getByTestId("cortex-mind-honesty").textContent).not.toContain(
      "Simulación determinista.",
    );
  });

  it("cae al respaldo del diccionario si el backend no manda nota, en cada idioma", async () => {
    const mute = { ...MIND, honesty: { note_es: "", note_en: "" } };
    renderPanel(mute, "en");
    await waitFor(() =>
      expect(screen.getByTestId("cortex-mind-honesty").textContent).toContain(
        "Computational model of affect, not real feelings.",
      ),
    );
    cleanup();
    renderPanel(mute, "es");
    await waitFor(() =>
      expect(screen.getByTestId("cortex-mind-honesty").textContent).toContain(
        "Modelo computacional de afecto, no sentimientos reales.",
      ),
    );
  });
});

describe("MindPanel — los diales PAD reflejan el dato mockeado", () => {
  it("cada dimensión enseña su número", () => {
    renderPanel(MIND);
    expect(screen.getByTestId("pad-valence").textContent).toContain("0.20");
    expect(screen.getByTestId("pad-arousal").textContent).toContain("0.40");
    expect(screen.getByTestId("pad-dominance").textContent).toContain("-0.60");
    expect(screen.getByTestId("pad-intensity").textContent).toContain("0.35");
  });

  it("cada barra mide lo que dice la proyección pura, no un valor fijo", () => {
    renderPanel(MIND);
    // valence ∈ [-1,1] -> 60 %; arousal ∈ [0,1] -> 40 %; dominance -0.6 -> 20 %.
    expect(barWidth("pad-valence")).toBe(`${padToPercent("valence", 0.2)}%`);
    expect(barWidth("pad-arousal")).toBe(`${padToPercent("arousal", 0.4)}%`);
    expect(barWidth("pad-dominance")).toBe(`${padToPercent("dominance", -0.6)}%`);
    expect(barWidth("pad-intensity")).toBe(`${padToPercent("intensity", 0.35)}%`);
    // Las cuatro barras son distintas: descarta el dial que pinta siempre igual.
    const widths = new Set(
      ["pad-valence", "pad-arousal", "pad-dominance", "pad-intensity"].map(barWidth),
    );
    expect(widths.size).toBe(4);
  });

  it("destaca la etiqueta de mood que viene del backend", () => {
    renderPanel(MIND);
    expect(screen.getByTestId("mood-label").textContent).toContain("sereno");
  });

  it("las sensaciones (drives) también pintan su dato", () => {
    renderPanel(MIND);
    expect(screen.getByTestId("drive-curiosity").textContent).toContain("0.70");
    expect(screen.getByTestId("drive-competence").textContent).toContain("0.25");
    expect(barWidth("drive-curiosity")).toBe("70%");
    expect(barWidth("drive-competence")).toBe("25%");
  });
});

describe("MindPanel — ES + EN (principio rector 12)", () => {
  it("en castellano rotula las dimensiones en castellano", async () => {
    renderPanel(MIND, "es");
    const panel = screen.getByTestId("cortex-mind-panel");
    await waitFor(() => expect(panel.textContent).toContain("Valencia"));
    expect(panel.textContent).toContain("Activación");
    expect(panel.textContent).toContain("Curiosidad");
  });

  it("en inglés NO queda castellano suelto en las etiquetas", async () => {
    renderPanel(MIND, "en");
    const panel = screen.getByTestId("cortex-mind-panel");
    await waitFor(() => expect(panel.textContent).toContain("Valence"));
    expect(panel.textContent).toContain("Arousal");
    expect(panel.textContent).toContain("Curiosity");
    for (const spanish of ["Valencia", "Activación", "Dominancia", "Curiosidad", "Sensaciones"]) {
      expect(panel.textContent).not.toContain(spanish);
    }
  });
});
