import { describe, expect, it } from "vitest";

import { computeDepState } from "@/lib/task-deps";

describe("computeDepState", () => {
  it("reports no dependencies for a root task", () => {
    expect(computeDepState([], new Map())).toEqual({
      hasDeps: false,
      blocked: false,
      pendingCount: 0,
    });
    // undefined depends_on (field absent) behaves like an empty list.
    expect(computeDepState(undefined, new Map())).toEqual({
      hasDeps: false,
      blocked: false,
      pendingCount: 0,
    });
  });

  it("is unblocked when every dependency is done", () => {
    const byId = new Map([
      ["a", "done"],
      ["b", "done"],
    ]);
    expect(computeDepState(["a", "b"], byId)).toEqual({
      hasDeps: true,
      blocked: false,
      pendingCount: 0,
    });
  });

  it("is blocked and counts the pending ones when some dependency is not done", () => {
    const byId = new Map([
      ["a", "done"],
      ["b", "in_progress"],
      ["c", "backlog"],
    ]);
    expect(computeDepState(["a", "b", "c"], byId)).toEqual({
      hasDeps: true,
      blocked: true,
      pendingCount: 2,
    });
  });

  it("treats an unknown dependency id as pending (safe default)", () => {
    // A dep not present in the loaded task set can't be confirmed done →
    // count it as blocking rather than silently unblocking the card.
    expect(computeDepState(["missing"], new Map())).toEqual({
      hasDeps: true,
      blocked: true,
      pendingCount: 1,
    });
  });
});
