import { describe, expect, it } from "vitest";

import {
  affectFrameToMind,
  driveToPercent,
  PAD_RANGES,
  padToPercent,
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
