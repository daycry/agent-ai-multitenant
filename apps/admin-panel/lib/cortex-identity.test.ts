import { describe, expect, it } from "vitest";

import {
  joinLines,
  needsOnboarding,
  parseLines,
  traitToPercent,
  TRAIT_LABELS_ES,
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
