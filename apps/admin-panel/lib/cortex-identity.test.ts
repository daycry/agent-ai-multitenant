import { describe, expect, it } from "vitest";

import {
  identityDiffSummary,
  identityVersionLabel,
  joinLines,
  needsOnboarding,
  parseLines,
  radarPolygon,
  traitRadarAxes,
  traitToPercent,
  TRAIT_LABELS_ES,
  type CortexTraits,
} from "./cortex-identity";

describe("needsOnboarding", () => {
  it("is true when onboarded_at is null (onboarding pendiente)", () => {
    expect(needsOnboarding({ onboarded_at: null })).toBe(true);
  });

  it("is false once onboarded_at is set", () => {
    expect(needsOnboarding({ onboarded_at: "2026-06-24T10:00:00Z" })).toBe(false);
  });
});

describe("parseLines", () => {
  it("splits, trims, and drops empty lines", () => {
    expect(parseLines("honestidad\n  curiosidad  \n\n  \nrigor")).toEqual([
      "honestidad",
      "curiosidad",
      "rigor",
    ]);
  });

  it("returns an empty list for blank input", () => {
    expect(parseLines("   \n\n")).toEqual([]);
  });

  it("round-trips with joinLines", () => {
    const values = ["honestidad", "curiosidad", "rigor"];
    expect(parseLines(joinLines(values))).toEqual(values);
  });
});

describe("traitToPercent", () => {
  it("maps [0,1] to [0,100]", () => {
    expect(traitToPercent(0)).toBe(0);
    expect(traitToPercent(0.5)).toBe(50);
    expect(traitToPercent(1)).toBe(100);
  });

  it("clamps out-of-range values (never overflows the bar)", () => {
    expect(traitToPercent(-0.3)).toBe(0);
    expect(traitToPercent(1.7)).toBe(100);
  });
});

describe("TRAIT_LABELS_ES", () => {
  it("labels the five Big-Five dimensions in Spanish", () => {
    expect(Object.keys(TRAIT_LABELS_ES)).toEqual([
      "openness",
      "conscientiousness",
      "extraversion",
      "agreeableness",
      "neuroticism",
    ]);
    expect(TRAIT_LABELS_ES.openness).toBe("Apertura");
  });
});

// ---------------------------------------------------------------------------
// identityDiffSummary — el único test que la tarea F3.6 exigía por NOMBRE.
//
// El `diff` que persiste cada versión es `{campo: {before, after}}`
// (cortex/identity.py::compute_diff). El timeline lo enseña, y sin resumen
// legible el owner ve un JSON: "qué cambió esta reflexión" es justo la pregunta
// que el timeline existe para responder.
// ---------------------------------------------------------------------------
describe("identityDiffSummary", () => {
  it("resume un diff multi-campo en un orden ESTABLE, no el del objeto", () => {
    // Las claves de un objeto JSON llegan en el orden que quiera el backend
    // (compute_diff recorre una UNIÓN de sets, que en Python no está ordenada):
    // si el resumen siguiera ese orden, la misma versión se leería distinta en
    // cada refresco.
    const a = identityDiffSummary({
      narrative: { before: "corta", after: "otra narrativa" },
      name: { before: null, after: "Atlas" },
    });
    const b = identityDiffSummary({
      name: { before: null, after: "Atlas" },
      narrative: { before: "corta", after: "otra narrativa" },
    });
    expect(a).toBe(b);
    expect(a.indexOf("nombre")).toBeLessThan(a.indexOf("narrativa"));
    expect(a).toContain("Atlas");
  });

  it("cuenta los rasgos que se movieron en vez de volcar el objeto", () => {
    const summary = identityDiffSummary({
      traits: {
        before: { openness: 0.5, conscientiousness: 0.5, extraversion: 0.5 },
        after: { openness: 0.56, conscientiousness: 0.5, extraversion: 0.44 },
      },
    });
    expect(summary).toContain("rasgos");
    expect(summary).toContain("2");
    expect(summary).not.toContain("openness"); // nada de claves crudas
    expect(summary).not.toContain("{");
  });

  it("resume las listas por su tamaño y la narrativa por el hecho de reescribirse", () => {
    expect(
      identityDiffSummary({ core_values: { before: ["a"], after: ["a", "b", "c"] } }),
    ).toContain("1 → 3");
    expect(identityDiffSummary({ narrative: { before: "x", after: "y" } })).toContain("narrativa");
  });

  it("un diff vacío lo dice (una versión sin cambios visibles existe)", () => {
    // La reflexión puede crear versión sin tocar nada observable; "sin cambios"
    // es información, un resumen en blanco parece un fallo de render.
    expect(identityDiffSummary({})).toBe("sin cambios");
    expect(identityDiffSummary({}, "en")).toBe("no changes");
  });

  it("es bilingüe (ES+EN), que es lo que pide el proyecto", () => {
    const diff = { name: { before: null, after: "Atlas" } };
    expect(identityDiffSummary(diff, "es")).toContain("nombre");
    expect(identityDiffSummary(diff, "en")).toContain("name");
  });

  it("un campo que el frontend no conoce sale con su clave, no se pierde", () => {
    // Peor que un nombre técnico es que un cambio real no aparezca en el timeline.
    expect(identityDiffSummary({ affect_params: { before: 1, after: 2 } })).toContain(
      "affect_params",
    );
  });

  it("tolera un diff sucio sin romper el timeline", () => {
    // El JSONB puede traer cualquier cosa: null, un escalar donde se esperaba
    // objeto… y el timeline NO puede caerse por eso.
    expect(() =>
      identityDiffSummary({
        name: null as unknown as { before: unknown; after: unknown },
        traits: { before: null, after: 3 },
      }),
    ).not.toThrow();
  });
});

describe("identityVersionLabel", () => {
  it("etiqueta la versión sin depender del locale del navegador", () => {
    expect(identityVersionLabel(3)).toBe("versión 3");
    expect(identityVersionLabel(3, "en")).toBe("version 3");
  });
});

// ---------------------------------------------------------------------------
// traitRadarAxes / radarPolygon — la geometría del radar Big-Five.
//
// El plan pedía RADAR y la UI pintaba barras. La geometría es pura: si vive
// dentro del SVG, invertir un eje o dejar un valor 0 en el borde (en vez del
// centro) no lo detecta nadie.
// ---------------------------------------------------------------------------
function traits(overrides: Partial<CortexTraits> = {}): CortexTraits {
  return {
    openness: 0.5,
    conscientiousness: 0.5,
    extraversion: 0.5,
    agreeableness: 0.5,
    neuroticism: 0.5,
    ...overrides,
  };
}

describe("traitRadarAxes", () => {
  const box = { cx: 50, cy: 50, radius: 40 };

  it("da los cinco ejes en el orden canónico Big-Five", () => {
    expect(traitRadarAxes(traits(), box).map((a) => a.key)).toEqual([
      "openness",
      "conscientiousness",
      "extraversion",
      "agreeableness",
      "neuroticism",
    ]);
  });

  it("el primer eje apunta ARRIBA (12 en punto), para que el radar se lea igual siempre", () => {
    const [first] = traitRadarAxes(traits({ openness: 1 }), box);
    expect(first.x).toBeCloseTo(50, 5);
    expect(first.y).toBeCloseTo(10, 5); // arriba = y menor
    expect(first.axisX).toBeCloseTo(50, 5);
    expect(first.axisY).toBeCloseTo(10, 5);
  });

  it("un rasgo a 0 cae en el CENTRO y a 1 en el extremo del eje", () => {
    const axes = traitRadarAxes(traits({ openness: 0, conscientiousness: 1 }), box);
    expect(axes[0].x).toBeCloseTo(50, 5);
    expect(axes[0].y).toBeCloseTo(50, 5);
    const second = axes[1];
    expect(Math.hypot(second.x - 50, second.y - 50)).toBeCloseTo(40, 5);
  });

  it("los ejes de referencia NO se mueven con el valor (son la rejilla)", () => {
    const low = traitRadarAxes(traits({ openness: 0 }), box)[0];
    const high = traitRadarAxes(traits({ openness: 1 }), box)[0];
    expect(low.axisX).toBeCloseTo(high.axisX, 5);
    expect(low.axisY).toBeCloseTo(high.axisY, 5);
    expect(low.y).not.toBeCloseTo(high.y, 5);
  });

  it("clampa valores sucios: el polígono nunca se sale de la rejilla", () => {
    const axes = traitRadarAxes(traits({ openness: 4, neuroticism: -2 }), box);
    for (const axis of axes) {
      expect(Math.hypot(axis.x - 50, axis.y - 50)).toBeLessThanOrEqual(40.0001);
      expect(axis.value).toBeGreaterThanOrEqual(0);
      expect(axis.value).toBeLessThanOrEqual(1);
    }
  });

  it("arrastra la etiqueta ES de cada rasgo (el radar es accesible por texto)", () => {
    expect(traitRadarAxes(traits(), box)[0].label).toBe(TRAIT_LABELS_ES.openness);
  });
});

describe("radarPolygon", () => {
  it("serializa los cinco vértices al atributo `points` del <polygon>", () => {
    const points = radarPolygon(
      traitRadarAxes(traits({ openness: 1 }), { cx: 50, cy: 50, radius: 40 }),
    );
    expect(points.split(" ")).toHaveLength(5);
    expect(points.startsWith("50,10")).toBe(true);
  });

  it("sin ejes no produce basura", () => {
    expect(radarPolygon([])).toBe("");
  });
});
