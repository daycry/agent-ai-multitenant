import { describe, expect, it } from "vitest";

import {
  affectFrameToMind,
  avatarStyleFromAffect,
  driveToPercent,
  PAD_RANGES,
  padToPercent,
  parseVoiceAffectFrame,
  type PadDimension,
} from "./cortex";

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
});
