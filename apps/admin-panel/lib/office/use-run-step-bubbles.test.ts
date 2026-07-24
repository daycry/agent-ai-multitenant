// @vitest-environment node
// Parseo del summary del step del WS de ejecución (La Oficina v2, ADR 0118).

import { describe, expect, it } from "vitest";

import { parseStepSummary } from "@/lib/office/use-run-step-bubbles";

describe("parseStepSummary", () => {
  it("pela el wrapper F47 payload.step.summary", () => {
    expect(
      parseStepSummary({ payload: { step: { index: 3, summary: "Escribiendo tests" } } }),
    ).toBe("Escribiendo tests");
  });

  it("acepta el payload directo (frames antiguos/crudos)", () => {
    expect(parseStepSummary({ payload: { index: 1, summary: "Leyendo Routes.php" } })).toBe(
      "Leyendo Routes.php",
    );
  });

  it("null cuando no hay summary o el frame es terminal/otro", () => {
    expect(parseStepSummary({ type: "execution.finished", payload: { result: {} } })).toBeNull();
    expect(parseStepSummary({ payload: { step: { index: 2 } } })).toBeNull();
    expect(parseStepSummary({ payload: { step: { summary: "   " } } })).toBeNull();
    expect(parseStepSummary(null)).toBeNull();
    expect(parseStepSummary("nope")).toBeNull();
  });

  it("recorta a 120 chars", () => {
    const long = "x".repeat(200);
    const out = parseStepSummary({ payload: { step: { summary: long } } });
    expect(out).not.toBeNull();
    expect(out!.length).toBe(120);
  });
});
