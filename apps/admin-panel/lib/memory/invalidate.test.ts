import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { invalidateMemoryViews } from "./invalidate";

function seeded() {
  const qc = new QueryClient();
  const keys: Record<string, unknown[]> = {
    list: ["memories", "list", "all", false],
    counts: ["memories", "counts"],
    card: ["memories", "card", "m1"],
    otherCard: ["memories", "card", "m2"],
    compactions: ["memories", "compactions"],
  };
  for (const key of Object.values(keys)) qc.setQueryData(key, []);
  const stale = (name: string) => qc.getQueryState(keys[name])?.isInvalidated;
  return { qc, stale };
}

describe("invalidateMemoryViews", () => {
  it("invalida el listado, los contadores y la ficha de ESA memoria, no la cola ni otras fichas", () => {
    const { qc, stale } = seeded();
    invalidateMemoryViews(qc, "m1");
    expect(stale("list")).toBe(true);
    expect(stale("counts")).toBe(true);
    expect(stale("card")).toBe(true);
    expect(stale("otherCard")).toBe(false);
    expect(stale("compactions")).toBe(false);
  });
});
