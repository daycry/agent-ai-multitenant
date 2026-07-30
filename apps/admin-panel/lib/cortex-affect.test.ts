// Córtex F2 (FASE H) — los tres helpers puros del espacio PAD del Panel de Mente.
//
// Por qué existen y por qué se prueban aquí: el panel dibuja un espacio PAD 2D
// con estela, y toda la aritmética de esa proyección (dónde cae un punto, cómo
// se desvanece la estela, de qué color es cada mood) es lógica pura. Si vive
// dentro del componente no hay forma de fijar los invariantes que importan
// —clamp, orientación de los ejes, monotonía del desvanecido— sin montar un
// SVG y leer atributos. Aquí se fijan sin React.
//
// Los `mood_label` NO son inventados: el backend los deriva de un catálogo
// cerrado por cuadrante (cortex/affective.py::derive_mood_label), bilingüe
// ES/EN: alegría|joy, calma|calm, tensión|tension, abatimiento|down, neutral.

import { describe, expect, it } from "vitest";

import {
  MOOD_NEUTRAL_COLOR,
  moodLabelColor,
  padToCanvasXY,
  trailFromSnapshots,
  trailPolyline,
  type AffectSnapshotLike,
} from "./cortex-affect";

// ---------------------------------------------------------------------------
// moodLabelColor
// ---------------------------------------------------------------------------
describe("moodLabelColor", () => {
  it("da un color propio a cada etiqueta del catálogo cerrado, en ES y EN", () => {
    // El mismo cuadrante tiene que pintar IGUAL en los dos idiomas: si no, al
    // cambiar de idioma el espacio PAD cambiaría de colores sin cambiar de
    // estado, que es exactamente el bug que este test impide.
    const pairs: [string, string][] = [
      ["alegría", "joy"],
      ["calma", "calm"],
      ["tensión", "tension"],
      ["abatimiento", "down"],
    ];
    for (const [es, en] of pairs) {
      expect(moodLabelColor(es)).toBe(moodLabelColor(en));
    }
    const colors = pairs.map(([es]) => moodLabelColor(es));
    expect(new Set(colors).size).toBe(pairs.length); // los 4 cuadrantes distinguibles
  });

  it("neutral y lo desconocido caen al color neutro (nunca a un color vacío)", () => {
    expect(moodLabelColor("neutral")).toBe(MOOD_NEUTRAL_COLOR);
    expect(moodLabelColor("")).toBe(MOOD_NEUTRAL_COLOR);
    // Una etiqueta que el backend aún no emite no debe dejar el punto invisible.
    expect(moodLabelColor("melancolía cuántica")).toBe(MOOD_NEUTRAL_COLOR);
  });

  it("tolera mayúsculas, espacios y acentos perdidos por el camino", () => {
    // El label viaja por JSON y por el WS; un `tension` sin tilde (o con
    // mayúscula) sigue siendo el mismo cuadrante.
    expect(moodLabelColor("  Tensión ")).toBe(moodLabelColor("tensión"));
    expect(moodLabelColor("TENSION")).toBe(moodLabelColor("tensión"));
    expect(moodLabelColor("alegria")).toBe(moodLabelColor("alegría"));
  });
});

// ---------------------------------------------------------------------------
// padToCanvasXY
// ---------------------------------------------------------------------------
describe("padToCanvasXY", () => {
  const box = { width: 100, height: 100, padding: 0 };

  it("proyecta valencia al eje X (izquierda negativa) y activación al Y invertido", () => {
    // Convención del espacio: X = valencia (-1 izquierda .. +1 derecha),
    // Y = activación con el 1 ARRIBA (en SVG la Y crece hacia abajo). Sin este
    // test, invertir el eje pasaría inadvertido y el panel mentiría: un córtex
    // excitado se dibujaría abajo, donde el ojo lee "apagado".
    expect(padToCanvasXY(-1, 0, box)).toEqual({ x: 0, y: 100 });
    expect(padToCanvasXY(1, 1, box)).toEqual({ x: 100, y: 0 });
    expect(padToCanvasXY(0, 0.5, box)).toEqual({ x: 50, y: 50 });
  });

  it("clampa fuera de rango: un valor sucio nunca se sale del lienzo", () => {
    expect(padToCanvasXY(5, 9, box)).toEqual({ x: 100, y: 0 });
    expect(padToCanvasXY(-5, -9, box)).toEqual({ x: 0, y: 100 });
  });

  it("respeta el padding: los puntos no se pegan al borde del lienzo", () => {
    const padded = padToCanvasXY(-1, 0, { width: 100, height: 100, padding: 10 });
    expect(padded).toEqual({ x: 10, y: 90 });
    const center = padToCanvasXY(0, 0.5, { width: 100, height: 100, padding: 10 });
    expect(center).toEqual({ x: 50, y: 50 });
  });

  it("sobrevive a NaN/Infinity sin devolver coordenadas inválidas", () => {
    // El frame llega de la red; un `valence: null` mal serializado se convierte
    // en NaN y un NaN en un atributo SVG borra el punto sin error de consola.
    const nan = padToCanvasXY(Number.NaN, Number.NaN, box);
    expect(Number.isFinite(nan.x)).toBe(true);
    expect(Number.isFinite(nan.y)).toBe(true);
    expect(nan).toEqual({ x: 50, y: 50 }); // centro = "no sé nada"
  });
});

// ---------------------------------------------------------------------------
// trailFromSnapshots
// ---------------------------------------------------------------------------
function snap(
  valence: number,
  arousal: number,
  overrides: Partial<AffectSnapshotLike> = {},
): AffectSnapshotLike {
  return {
    valence,
    arousal,
    mood_label: "neutral",
    created_at: "2026-07-27T10:00:00Z",
    ...overrides,
  };
}

describe("trailFromSnapshots", () => {
  const box = { width: 100, height: 100, padding: 0 };

  it("convierte los snapshots en puntos proyectados, del más viejo al más nuevo", () => {
    // El endpoint devuelve orden ASC (cronológico); la estela debe respetarlo:
    // el ÚLTIMO punto es el estado actual y es el que lleva la cabeza.
    const trail = trailFromSnapshots([snap(-1, 0), snap(0, 0.5), snap(1, 1)], { box });
    expect(trail).toHaveLength(3);
    expect(trail[0]).toMatchObject({ x: 0, y: 100, isHead: false });
    expect(trail[2]).toMatchObject({ x: 100, y: 0, isHead: true });
    expect(trail.filter((p) => p.isHead)).toHaveLength(1);
  });

  it("desvanece la estela: cuanto más viejo el punto, menos opaco", () => {
    const trail = trailFromSnapshots([snap(0, 0.1), snap(0, 0.2), snap(0, 0.3)], { box });
    const opacities = trail.map((p) => p.opacity);
    expect(opacities[0]).toBeLessThan(opacities[1]);
    expect(opacities[1]).toBeLessThan(opacities[2]);
    for (const o of opacities) {
      expect(o).toBeGreaterThan(0);
      expect(o).toBeLessThanOrEqual(1);
    }
  });

  it("la cabeza es más grande que la estela (el ojo encuentra el ahora)", () => {
    const trail = trailFromSnapshots([snap(0, 0.1), snap(0, 0.9)], { box });
    expect(trail[1].radius).toBeGreaterThan(trail[0].radius);
  });

  it("recorta a los N más RECIENTES, no a los N primeros", () => {
    // El bug clásico del `slice(0, max)`: con 500 snapshots la estela mostraría
    // el humor de hace una semana y el "ahora" no aparecería nunca.
    const many = Array.from({ length: 10 }, (_, i) => snap(-1 + i * 0.2, i / 10));
    const trail = trailFromSnapshots(many, { box, max: 3 });
    expect(trail).toHaveLength(3);
    expect(trail[2].isHead).toBe(true);
    // El último de la entrada sobrevive; el primero, no.
    expect(trail[2].x).toBeCloseTo(padToCanvasXY(many[9].valence, many[9].arousal, box).x, 5);
    expect(trail.some((p) => p.x === 0)).toBe(false);
  });

  it("un solo snapshot es cabeza y estela a la vez, con opacidad plena", () => {
    const trail = trailFromSnapshots([snap(0.5, 0.5)], { box });
    expect(trail).toHaveLength(1);
    expect(trail[0].isHead).toBe(true);
    expect(trail[0].opacity).toBe(1);
  });

  it("lista vacía → estela vacía (el panel pinta su estado vacío, no un punto en el centro)", () => {
    expect(trailFromSnapshots([], { box })).toEqual([]);
  });

  it("arrastra el color del mood y el sello temporal de cada punto", () => {
    const trail = trailFromSnapshots(
      [snap(0.8, 0.9, { mood_label: "alegría", created_at: "2026-07-27T09:00:00Z" })],
      { box },
    );
    expect(trail[0].color).toBe(moodLabelColor("alegría"));
    expect(trail[0].createdAt).toBe("2026-07-27T09:00:00Z");
  });
});

describe("trailPolyline", () => {
  it("serializa la estela al atributo `points` de un <polyline> SVG", () => {
    const trail = trailFromSnapshots([snap(-1, 0), snap(1, 1)], {
      box: { width: 100, height: 100, padding: 0 },
    });
    expect(trailPolyline(trail)).toBe("0,100 100,0");
  });

  it("una estela vacía no produce una cadena basura", () => {
    expect(trailPolyline([])).toBe("");
  });
});
