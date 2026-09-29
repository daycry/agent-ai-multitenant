import { describe, expect, it } from "vitest";

import { isTerminal, summarizeStats } from "./kb-types";

const stats = (by_status: Record<string, number>, last: string | null = "2026-03-01") => ({
  total: Object.values(by_status).reduce((a, b) => a + b, 0),
  by_status,
  last_updated_at: last,
});

describe("summarizeStats", () => {
  it("sin agregados la base está vacía y no tiene fecha", () => {
    expect(summarizeStats(undefined)).toMatchObject({ count: 0, state: "empty", lastUpdate: null });
    expect(summarizeStats(stats({}, null))).toMatchObject({ count: 0, state: "empty" });
  });
  it("un documento fallido gana a los indexados, y en curso gana a indexado", () => {
    expect(summarizeStats(stats({ indexed: 3, failed: 1 })).state).toBe("failed");
    expect(summarizeStats(stats({ indexed: 3, processing: 1 })).state).toBe("indexing");
    expect(summarizeStats(stats({ indexed: 3 })).state).toBe("indexed");
  });
  it("cuenta el total real (más allá de una página) y indexed_empty como indexado", () => {
    const s = summarizeStats(stats({ indexed: 2990, indexed_empty: 5, failed: 5 }));
    expect(s).toMatchObject({ count: 3000, indexed: 2995, failed: 5, state: "failed" });
    expect(summarizeStats(stats({ indexed_empty: 2 })).state).toBe("indexed");
  });
  it("la última actualización es la que trae el listado", () => {
    expect(summarizeStats(stats({ indexed: 1 }, "2026-03-01")).lastUpdate).toBe("2026-03-01");
  });
});

describe("isTerminal", () => {
  it("indexed_empty es terminal; pending y processing no", () => {
    expect(isTerminal("indexed_empty")).toBe(true);
    expect(isTerminal("failed")).toBe(true);
    expect(isTerminal("processing")).toBe(false);
    expect(isTerminal("pending")).toBe(false);
  });
});
