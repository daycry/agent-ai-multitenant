import { describe, expect, it } from "vitest";

import { summarizeDocuments, type KbDocument } from "./kb-types";

function d(status: KbDocument["status"], updated_at: string): KbDocument {
  return { id: updated_at + status, status, updated_at } as KbDocument;
}

describe("summarizeDocuments", () => {
  it("sin documentos la base está vacía y no tiene fecha", () => {
    expect(summarizeDocuments([])).toMatchObject({ count: 0, state: "empty", lastUpdate: null });
  });
  it("un documento fallido gana a los indexados, y en curso gana a indexado", () => {
    expect(summarizeDocuments([d("indexed", "a"), d("failed", "b")]).state).toBe("failed");
    expect(summarizeDocuments([d("indexed", "a"), d("processing", "b")]).state).toBe("indexing");
    expect(summarizeDocuments([d("indexed", "a")]).state).toBe("indexed");
  });
  it("la última actualización es la más reciente", () => {
    expect(
      summarizeDocuments([d("indexed", "2026-01-01"), d("indexed", "2026-03-01")]).lastUpdate,
    ).toBe("2026-03-01");
  });
});
