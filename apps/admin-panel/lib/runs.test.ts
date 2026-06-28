import { describe, expect, it } from "vitest";

import { runsQuery, runStatusLabel, runStatusVariant } from "@/lib/runs";

describe("runsQuery", () => {
  it("is empty with no filters", () => {
    expect(runsQuery()).toBe("");
    expect(runsQuery({})).toBe("");
  });

  it("includes set string filters", () => {
    expect(runsQuery({ task_id: "t1" })).toBe("?task_id=t1");
  });

  it("drops undefined / null / blank values", () => {
    expect(runsQuery({ task_id: "", verdict: undefined, model: "   " })).toBe("");
  });

  it("serializes numbers", () => {
    const qs = runsQuery({ limit: 50, min_cost: 0.5 });
    expect(qs).toContain("limit=50");
    expect(qs).toContain("min_cost=0.5");
  });

  it("keeps only the non-empty filters", () => {
    expect(runsQuery({ task_id: "abc", plan_id: "" })).toBe("?task_id=abc");
  });
});

describe("runStatusVariant", () => {
  it("maps the human-attention states to warning, not the muted fallback (F49/F50)", () => {
    // The bug: these statuses were absent from the map → silently `muted`.
    expect(runStatusVariant("needs_human_review")).toBe("warning");
    expect(runStatusVariant("awaiting_human_approval")).toBe("warning");
  });

  it("maps the terminal states", () => {
    expect(runStatusVariant("done")).toBe("success");
    expect(runStatusVariant("failed")).toBe("danger");
    expect(runStatusVariant("aborted")).toBe("warning");
    expect(runStatusVariant("cancelled")).toBe("muted");
    expect(runStatusVariant("running")).toBe("info");
  });

  it("falls back to muted for an unknown status", () => {
    expect(runStatusVariant("totally_unknown")).toBe("muted");
  });
});

describe("runStatusLabel", () => {
  it("returns readable Spanish labels for the persistible statuses", () => {
    expect(runStatusLabel("done")).toBe("Completado");
    expect(runStatusLabel("running")).toBe("En curso");
    expect(runStatusLabel("awaiting_human_approval")).toBe("Esperando aprobación");
    expect(runStatusLabel("needs_human_review")).toBe("Revisión humana");
    expect(runStatusLabel("cancelled")).toBe("Cancelado");
  });

  it("falls back to the raw status when unmapped", () => {
    expect(runStatusLabel("weird")).toBe("weird");
  });
});
