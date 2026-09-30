import { describe, expect, it } from "vitest";

import { memoryListPath } from "./list-query";

describe("memoryListPath", () => {
  it("sin filtros es /memories a secas", () => {
    expect(memoryListPath({ view: "all", neverRecalled: false, offset: 0 })).toBe("/memories");
  });

  it("una pestaña de ámbito filtra por scope", () => {
    expect(memoryListPath({ view: "team_shared", neverRecalled: false, offset: 0 })).toBe(
      "/memories?scope=team_shared",
    );
  });

  it("la pestaña Disputadas pide disputed=true y no fija ámbito", () => {
    expect(memoryListPath({ view: "disputed", neverRecalled: false, offset: 0 })).toBe(
      "/memories?disputed=true",
    );
  });

  it("«nunca recuperadas» es recall_count = 0, combinable con el ámbito y la página", () => {
    expect(memoryListPath({ view: "global", neverRecalled: true, offset: 50 })).toBe(
      "/memories?scope=global&max_recall_count=0&offset=50",
    );
  });
});
