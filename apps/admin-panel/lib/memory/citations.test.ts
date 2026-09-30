import { describe, expect, it } from "vitest";

import { splitCitations } from "./citations";

describe("splitCitations", () => {
  it("un texto sin citas es un único trozo de texto", () => {
    expect(splitCitations("nada que citar")).toEqual([{ type: "text", value: "nada que citar" }]);
    expect(splitCitations("")).toEqual([]);
  });

  it("separa texto y citas conservando el orden", () => {
    const parts = splitCitations("Usé asyncpg [mem:ABCDEF12] y pgvector [mem:0a1b2c3d].");
    expect(parts).toEqual([
      { type: "text", value: "Usé asyncpg " },
      { type: "cite", short: "abcdef12", raw: "[mem:ABCDEF12]" },
      { type: "text", value: " y pgvector " },
      { type: "cite", short: "0a1b2c3d", raw: "[mem:0a1b2c3d]" },
      { type: "text", value: "." },
    ]);
  });

  it("no toma por cita lo que no es un id (demasiado corto o no hexadecimal)", () => {
    const text = "[mem:abc] [mem:zzzzzzzz] [mem:] mem:abcdef12";
    expect(splitCitations(text)).toEqual([{ type: "text", value: text }]);
  });
});
