// @vitest-environment jsdom
// Córtex F2 (FASE H) — el espacio PAD 2D con estela del Panel de Mente.
//
// Por qué existe: el plan pedía "espacio PAD 2D con estela" y el panel sólo
// tenía la línea SVG del mood, que es OTRA cosa (una sola dimensión en el
// tiempo). Aquí se fija el contrato observable de la superficie nueva:
//
//   - el copy honesto (ADR 0075 §6) está SIEMPRE, incluso sin datos;
//   - la estela dibuja un punto por snapshot y la CABEZA es el estado vivo, de
//     modo que un frame del WS mueve el punto sin esperar al polling;
//   - los ejes están rotulados y orientados (activación arriba, valencia a la
//     derecha) — si alguien invierte el eje, el panel miente y este test cae;
//   - sin ningún dato se pinta el estado vacío, no un punto en el centro (que
//     se leería como "el córtex está neutro", que es distinto de "no sé nada").

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { MindPadSpace } from "@/components/cortex/mind-pad-space";
import { moodLabelColor, padToCanvasXY, type AffectSnapshotLike } from "@/lib/cortex-affect";
import { LanguageProvider } from "@/lib/lang-context";

afterEach(cleanup);

function snap(valence: number, arousal: number, mood = "neutral"): AffectSnapshotLike {
  return { valence, arousal, mood_label: mood, created_at: "2026-07-27T10:00:00Z" };
}

/** El `points` del <polyline> de la estela, ya partido en pares numéricos. */
function trailPoints(): [number, number][] {
  const raw = screen.getByTestId("cortex-pad-trail").getAttribute("points") ?? "";
  return raw
    .split(" ")
    .filter(Boolean)
    .map((pair) => {
      const [x, y] = pair.split(",").map(Number);
      return [x, y] as [number, number];
    });
}

function head(): SVGCircleElement {
  return screen.getByTestId("cortex-pad-head") as unknown as SVGCircleElement;
}

describe("MindPadSpace — espacio PAD 2D con estela (F2)", () => {
  it("el copy honesto está incluso sin un solo snapshot", () => {
    render(<MindPadSpace current={null} snapshots={[]} />);
    const panel = screen.getByTestId("cortex-pad-space");
    expect(panel.textContent).toMatch(/no son (emociones|sentimientos) reales/i);
    expect(screen.getByTestId("cortex-pad-empty")).toBeTruthy();
  });

  it("sin datos NO dibuja un punto en el centro (vacío ≠ neutro)", () => {
    render(<MindPadSpace current={null} snapshots={[]} />);
    expect(screen.queryByTestId("cortex-pad-head")).toBeNull();
    expect(screen.queryByTestId("cortex-pad-trail")).toBeNull();
  });

  it("dibuja un punto por snapshot y la cabeza en el estado vivo", () => {
    const snapshots = [snap(-0.8, 0.2), snap(-0.2, 0.5)];
    render(
      <MindPadSpace
        current={{ valence: 0.9, arousal: 0.95, mood_label: "alegría" }}
        snapshots={snapshots}
      />,
    );
    // 2 snapshots + el estado vivo como último punto de la estela.
    expect(trailPoints()).toHaveLength(3);
    const expected = padToCanvasXY(0.9, 0.95);
    expect(Number(head().getAttribute("cx"))).toBeCloseTo(expected.x, 2);
    expect(Number(head().getAttribute("cy"))).toBeCloseTo(expected.y, 2);
    expect(head().getAttribute("fill")).toBe(moodLabelColor("alegría"));
  });

  it("un estado vivo nuevo mueve la cabeza (el WS se ve sin esperar al polling)", () => {
    const { rerender } = render(
      <MindPadSpace
        current={{ valence: -0.9, arousal: 0.1, mood_label: "abatimiento" }}
        snapshots={[]}
      />,
    );
    const sadX = Number(head().getAttribute("cx"));
    const sadY = Number(head().getAttribute("cy"));

    rerender(
      <MindPadSpace
        current={{ valence: 0.9, arousal: 0.9, mood_label: "alegría" }}
        snapshots={[]}
      />,
    );
    expect(Number(head().getAttribute("cx"))).toBeGreaterThan(sadX);
    // Activación alta = ARRIBA, es decir MENOR y en coordenadas SVG.
    expect(Number(head().getAttribute("cy"))).toBeLessThan(sadY);
  });

  it("con sólo histórico (sin estado vivo) la cabeza es el snapshot más reciente", () => {
    render(
      <MindPadSpace current={null} snapshots={[snap(-0.5, 0.2), snap(0.7, 0.8, "alegría")]} />,
    );
    const expected = padToCanvasXY(0.7, 0.8);
    expect(Number(head().getAttribute("cx"))).toBeCloseTo(expected.x, 2);
    expect(Number(head().getAttribute("cy"))).toBeCloseTo(expected.y, 2);
  });

  it("rotula los ejes con su orientación (si se invierte un eje, el panel miente)", () => {
    render(
      <MindPadSpace current={{ valence: 0, arousal: 0.5, mood_label: "neutral" }} snapshots={[]} />,
    );
    const panel = screen.getByTestId("cortex-pad-space");
    expect(panel.textContent).toContain("Valencia");
    expect(panel.textContent).toContain("Activación");
    // La etiqueta del eje Y superior es la activación alta, no la baja.
    expect(screen.getByTestId("cortex-pad-axis-arousal-high").textContent).toMatch(/\+|alta/i);
  });

  it("un error de la serie se distingue del vacío (no miente diciendo que no hay datos)", () => {
    render(<MindPadSpace current={null} snapshots={[]} isError />);
    expect(screen.getByTestId("cortex-pad-error")).toBeTruthy();
    expect(screen.queryByTestId("cortex-pad-empty")).toBeNull();
  });

  it("recorta la estela a los más recientes y no revienta con 500 snapshots", () => {
    const many = Array.from({ length: 500 }, (_, i) => snap(-1 + (i / 499) * 2, i / 499));
    render(<MindPadSpace current={null} snapshots={many} />);
    const points = trailPoints();
    expect(points.length).toBeLessThanOrEqual(40);
    // Y el ÚLTIMO de la entrada sigue siendo la cabeza.
    const expected = padToCanvasXY(many[499].valence, many[499].arousal);
    expect(Number(head().getAttribute("cx"))).toBeCloseTo(expected.x, 2);
  });
});

describe("MindPadSpace — copy en los dos idiomas (CLAUDE.md §12)", () => {
  it("en EN el aviso honesto y los ejes salen en inglés", () => {
    // El resto del Panel de Mente sigue con rótulos ES fijos (deuda previa, la
    // resuelve prod-16 con la capa i18n); esta superficie es nueva y no la
    // agranda: se comprueba que el aviso OBLIGATORIO existe también en EN.
    window.localStorage.setItem("admin-panel.lang", "en");
    render(
      <LanguageProvider>
        <MindPadSpace current={{ valence: 0.2, arousal: 0.5, mood_label: "calm" }} snapshots={[]} />
      </LanguageProvider>,
    );
    const panel = screen.getByTestId("cortex-pad-space");
    expect(panel.textContent).toMatch(/not real emotions/i);
    expect(screen.getByTestId("cortex-pad-axis-arousal-high").textContent).toBe("High arousal");
    expect(panel.textContent).not.toContain("Activación alta");
    window.localStorage.clear();
  });
});
