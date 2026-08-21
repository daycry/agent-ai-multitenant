import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import {
  affectFrameToMind,
  avatarStyleFromAffect,
  browseStepSummary,
  driveToPercent,
  getCortexPursuits,
  PAD_RANGES,
  padToPercent,
  parseVoiceAffectFrame,
  type CortexPursuit,
  type PadDimension,
} from "./cortex";

afterEach(() => {
  apiFetchMock.mockReset();
});

describe("browseStepSummary (ADR 0080 — inbox de aprobación)", () => {
  it("describe cada acción del catálogo cerrado en lenguaje del owner", () => {
    expect(browseStepSummary({ action: "goto", url: "https://x.com" })).toBe("ir a https://x.com");
    expect(browseStepSummary({ action: "click", selector: "#b" })).toBe("clicar #b");
    expect(browseStepSummary({ action: "wait_for", selector: ".ok" })).toBe("esperar .ok");
    expect(browseStepSummary({ action: "extract" })).toBe("extraer (página)");
  });

  it("muestra lo que se va a teclear en un fill (es lo que el owner autoriza)", () => {
    // Esto se MUESTRA para decidir; el valor nunca vuelve del runtime (contrato
    // del browser-runtime, no de esta vista).
    expect(browseStepSummary({ action: "fill", selector: "#user", value: "owner" })).toContain(
      "owner",
    );
  });
});

describe("padToPercent", () => {
  it("maps a bipolar dimension's neutral 0 to the centre (50%)", () => {
    expect(padToPercent("valence", 0)).toBe(50);
    expect(padToPercent("dominance", 0)).toBe(50);
  });

  it("maps the extremes of each range to 0% and 100%", () => {
    (Object.keys(PAD_RANGES) as PadDimension[]).forEach((dim) => {
      const { min, max } = PAD_RANGES[dim];
      expect(padToPercent(dim, min)).toBe(0);
      expect(padToPercent(dim, max)).toBe(100);
    });
  });

  it("maps a unipolar [0,1] midpoint to 50%", () => {
    expect(padToPercent("arousal", 0.5)).toBe(50);
    expect(padToPercent("intensity", 0.25)).toBe(25);
  });

  it("clamps out-of-range values into [0,100]", () => {
    expect(padToPercent("valence", 5)).toBe(100);
    expect(padToPercent("valence", -5)).toBe(0);
    expect(padToPercent("arousal", 2)).toBe(100);
    expect(padToPercent("arousal", -1)).toBe(0);
  });
});

describe("driveToPercent", () => {
  it("scales a [0,1] drive to a [0,100] bar width", () => {
    expect(driveToPercent(0)).toBe(0);
    expect(driveToPercent(0.5)).toBe(50);
    expect(driveToPercent(1)).toBe(100);
  });

  it("clamps out-of-range drives", () => {
    expect(driveToPercent(1.5)).toBe(100);
    expect(driveToPercent(-0.2)).toBe(0);
  });
});

describe("affectFrameToMind", () => {
  const frame = {
    type: "affect",
    occurred_at: "2026-06-24T10:00:00Z",
    id: "1-0",
    payload: {
      valence: 0.4,
      arousal: 0.7,
      dominance: -0.2,
      intensity: 0.6,
      mood_valence: 0.3,
      mood_arousal: 0.5,
      mood_dominance: -0.1,
      mood_label: "contento",
      drives: { curiosity: 0.8, bonding: 0.6, coherence: 0.4, competence: 0.5 },
      appraisal_reason: "El owner resolvió el bug.",
    },
  };

  it("normalises a well-formed affect frame", () => {
    const mind = affectFrameToMind(frame);
    expect(mind).not.toBeNull();
    expect(mind?.valence).toBe(0.4);
    expect(mind?.arousal).toBe(0.7);
    expect(mind?.dominance).toBe(-0.2);
    expect(mind?.intensity).toBe(0.6);
    expect(mind?.mood_valence).toBe(0.3);
    expect(mind?.mood_label).toBe("contento");
    expect(mind?.drives).toEqual({
      curiosity: 0.8,
      bonding: 0.6,
      coherence: 0.4,
      competence: 0.5,
    });
  });

  it("coerces stringified numbers (Redis stream values)", () => {
    const mind = affectFrameToMind({
      type: "affect",
      payload: {
        valence: "0.4",
        arousal: "0.7",
        dominance: "0",
        intensity: "0",
        mood_label: "neutro",
        drives: { curiosity: "0.5", bonding: "0.5", coherence: "0.5", competence: "0.5" },
      },
    });
    expect(mind?.valence).toBe(0.4);
    expect(mind?.arousal).toBe(0.7);
    expect(mind?.drives.curiosity).toBe(0.5);
  });

  it("falls back mood_* to emotion when the frame omits them", () => {
    const mind = affectFrameToMind({
      type: "affect",
      payload: {
        valence: 0.2,
        arousal: 0.3,
        dominance: 0.1,
        intensity: 0.4,
        mood_label: "calmo",
        drives: { curiosity: 0.5, bonding: 0.5, coherence: 0.5, competence: 0.5 },
      },
    });
    expect(mind?.mood_valence).toBe(0.2);
    expect(mind?.mood_arousal).toBe(0.3);
    expect(mind?.mood_dominance).toBe(0.1);
  });

  it("rejects non-affect, malformed, or non-object frames", () => {
    expect(affectFrameToMind({ type: "task_status", payload: {} })).toBeNull();
    expect(affectFrameToMind({ type: "affect" })).toBeNull();
    expect(affectFrameToMind(null)).toBeNull();
    expect(affectFrameToMind("nope")).toBeNull();
    expect(affectFrameToMind(42)).toBeNull();
  });
});

describe("parseVoiceAffectFrame", () => {
  // El frame de VOZ es PLANO (campos en raíz), no anidado en `payload`.
  const frame = {
    type: "affect",
    valence: 0.42,
    arousal: 0.61,
    dominance: -0.15,
    intensity: 0.55,
    mood_label: "concentrado",
    drives: { curiosity: 0.82, bonding: 0.4, coherence: 0.66, competence: 0.55 },
  };

  it("parses a well-formed flat voice affect frame", () => {
    const parsed = parseVoiceAffectFrame(frame);
    expect(parsed).not.toBeNull();
    expect(parsed?.valence).toBe(0.42);
    expect(parsed?.arousal).toBe(0.61);
    expect(parsed?.dominance).toBe(-0.15);
    expect(parsed?.intensity).toBe(0.55);
    expect(parsed?.mood_label).toBe("concentrado");
    expect(parsed?.drives).toEqual({
      curiosity: 0.82,
      bonding: 0.4,
      coherence: 0.66,
      competence: 0.55,
    });
  });

  it("coerces stringified numbers", () => {
    const parsed = parseVoiceAffectFrame({
      type: "affect",
      valence: "0.3",
      arousal: "0.5",
      dominance: "0",
      mood_label: "calmo",
      drives: { curiosity: "0.5", bonding: "0.5", coherence: "0.5", competence: "0.5" },
    });
    expect(parsed?.valence).toBe(0.3);
    expect(parsed?.arousal).toBe(0.5);
    expect(parsed?.drives.curiosity).toBe(0.5);
  });

  it("does NOT accept the nested telemetry frame (has a `payload`)", () => {
    // El frame de telemetría de `/mind` anida en `payload`; este parser es del WS
    // de voz (campos planos), así que debe rechazarlo y dejarlo para affectFrameToMind.
    expect(parseVoiceAffectFrame({ type: "affect", payload: { valence: 0.4 } })).toBeNull();
  });

  it("rejects non-affect, null, and non-object frames", () => {
    expect(parseVoiceAffectFrame({ type: "transcript", text: "hola" })).toBeNull();
    expect(parseVoiceAffectFrame(null)).toBeNull();
    expect(parseVoiceAffectFrame("nope")).toBeNull();
    expect(parseVoiceAffectFrame(42)).toBeNull();
  });
});

describe("avatarStyleFromAffect", () => {
  it("maps negative valence to a red-ish hue and positive to green-ish", () => {
    const sad = avatarStyleFromAffect({ valence: -1, arousal: 0.5 });
    const happy = avatarStyleFromAffect({ valence: 1, arousal: 0.5 });
    expect(sad.hue).toBe(0); // rojo
    expect(happy.hue).toBe(130); // verde
    // La neutra (0) cae en un ámbar intermedio.
    expect(avatarStyleFromAffect({ valence: 0, arousal: 0.5 }).hue).toBe(65);
  });

  it("raises saturation and speeds up the sway with arousal", () => {
    const calm = avatarStyleFromAffect({ valence: 0, arousal: 0 });
    const excited = avatarStyleFromAffect({ valence: 0, arousal: 1 });
    expect(excited.saturation).toBeGreaterThan(calm.saturation);
    expect(excited.intensity).toBeGreaterThan(calm.intensity);
    // Más activación → ciclo de sway más corto (más rápido).
    expect(excited.swayDurationSec).toBeLessThan(calm.swayDurationSec);
  });

  it("clamps out-of-range valence and arousal", () => {
    const over = avatarStyleFromAffect({ valence: 5, arousal: 9 });
    const under = avatarStyleFromAffect({ valence: -5, arousal: -9 });
    expect(over.hue).toBe(130);
    expect(over.intensity).toBe(1);
    expect(under.hue).toBe(0);
    expect(under.intensity).toBe(0);
  });

  // -------------------------------------------------------------------------
  // Hueco C1 de la auditoría 2026-07-27: al retorno le faltaban `blinkRate`
  // (parpadeo gobernado por la activación — hasta ahora era un intervalo
  // aleatorio fijo, así que un córtex excitado parpadeaba igual que uno
  // apagado), `mouthBias` (la curvatura de la boca en reposo, que el avatar
  // recalculaba inline) y `label` (qué etiqueta pinta el avatar).
  // -------------------------------------------------------------------------
  it("el parpadeo se acelera con la activación (y nunca se detiene)", () => {
    const calm = avatarStyleFromAffect({ valence: 0, arousal: 0 });
    const excited = avatarStyleFromAffect({ valence: 0, arousal: 1 });
    expect(excited.blinkRate).toBeGreaterThan(calm.blinkRate);
    // Un blinkRate 0 dejaría los ojos abiertos para siempre (o dividiría por
    // cero al calcular el intervalo): el suelo tiene que ser humano.
    expect(calm.blinkRate).toBeGreaterThan(0);
    expect(excited.blinkRate).toBeLessThan(120); // ni un tic nervioso imposible
  });

  it("el sesgo de la boca sigue la valencia: +1 sonríe, -1 hace mueca", () => {
    expect(avatarStyleFromAffect({ valence: 1, arousal: 0.3 }).mouthBias).toBe(1);
    expect(avatarStyleFromAffect({ valence: -1, arousal: 0.3 }).mouthBias).toBe(-1);
    expect(avatarStyleFromAffect({ valence: 0, arousal: 0.3 }).mouthBias).toBe(0);
    // Clampado: un valence 5 no dobla la boca fuera de la cara.
    expect(avatarStyleFromAffect({ valence: 5, arousal: 0.3 }).mouthBias).toBe(1);
    expect(avatarStyleFromAffect({ valence: -5, arousal: 0.3 }).mouthBias).toBe(-1);
  });

  it("`label` es la etiqueta que pinta el avatar: la del backend, ya recortada", () => {
    // NO se deriva del PAD: la etiqueta bilingüe es del backend
    // (derive_mood_label) y duplicar ese mapeo aquí crearía dos verdades.
    expect(
      avatarStyleFromAffect({ valence: 0.5, arousal: 0.5, mood_label: " alegría " }).label,
    ).toBe("alegría");
  });

  it("sin `mood_label` el label queda VACÍO (no se inventa un estado de ánimo)", () => {
    // El defecto que atrapa: pintar "neutral" cuando el servidor aún no ha
    // mandado ningún frame afectivo. Vacío ⇒ el avatar no enseña chip.
    expect(avatarStyleFromAffect({ valence: 0, arousal: 0.3 }).label).toBe("");
    expect(avatarStyleFromAffect({ valence: 0, arousal: 0.3, mood_label: "   " }).label).toBe("");
  });
});

// ---------------------------------------------------------------------------
// getCortexPursuits — el fetcher de "Lo que está aprendiendo" (ADR 0078).
//
// La tarjeta del Panel de Mente lee este historial de curiosidad. El endpoint
// vive bajo el router `/owner/cortex` (gated por `require_system_owner`), así
// que lo que hay que clavar es (a) que el prefijo del router NO se pierde —un
// `/curiosity/pursuits` sin prefijo devuelve 404 y la tarjeta se queda vacía
// sin decir por qué— y (b) que los filtros opcionales solo viajan cuando se
// piden (un `?status=undefined` es un 422 del backend, no un "sin filtro").
// ---------------------------------------------------------------------------

function pursuit(overrides: Partial<CortexPursuit> = {}): CortexPursuit {
  return {
    id: "p1",
    topic: "compilación incremental en Rust",
    status: "digested",
    created_at: "2026-07-20T10:00:00Z",
    surfaced_at: null,
    learning_memory_id: null,
    search_count: 3,
    ...overrides,
  };
}

describe("getCortexPursuits (ADR 0078 — historial de curiosidad)", () => {
  it("pega al endpoint del router del córtex, con su prefijo owner-only", async () => {
    apiFetchMock.mockResolvedValue([pursuit()]);
    const rows = await getCortexPursuits();
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect(apiFetchMock.mock.calls[0][0]).toBe("/owner/cortex/curiosity/pursuits");
    expect(rows).toHaveLength(1);
    expect(rows[0].topic).toBe("compilación incremental en Rust");
  });

  it("no añade query-string cuando no se pide ningún filtro", async () => {
    apiFetchMock.mockResolvedValue([]);
    await getCortexPursuits();
    // Un `?status=undefined&limit=undefined` NO es "sin filtro": el backend lo
    // rechaza con 422 y la tarjeta se quedaría vacía.
    expect(apiFetchMock.mock.calls[0][0]).not.toContain("?");
  });

  it("propaga status y limit como query-params cuando se piden", async () => {
    apiFetchMock.mockResolvedValue([]);
    await getCortexPursuits({ status: "surfaced", limit: 20 });
    const path = apiFetchMock.mock.calls[0][0] as string;
    expect(path.startsWith("/owner/cortex/curiosity/pursuits?")).toBe(true);
    const qs = new URLSearchParams(path.split("?")[1]);
    expect(qs.get("status")).toBe("surfaced");
    expect(qs.get("limit")).toBe("20");
  });

  it("manda limit=0 como filtro real y no lo confunde con 'sin límite'", async () => {
    // `if (opts.limit)` habría tragado el 0 (falsy) y pedido la lista entera;
    // el helper compara contra undefined a propósito.
    apiFetchMock.mockResolvedValue([]);
    await getCortexPursuits({ limit: 0 });
    expect(apiFetchMock.mock.calls[0][0]).toContain("limit=0");
  });

  it("deja subir el error de la API en vez de devolver una lista vacía", async () => {
    // Un 403 (dejaste de ser owner) debe distinguirse de "no hay temas": la
    // tarjeta pinta un mensaje de error, no el estado vacío.
    apiFetchMock.mockRejectedValue(new Error("api 403: forbidden"));
    await expect(getCortexPursuits()).rejects.toThrow("403");
  });
});
