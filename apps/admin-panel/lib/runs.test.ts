import { describe, expect, it } from "vitest";

import { runsQuery } from "@/lib/runs";

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
