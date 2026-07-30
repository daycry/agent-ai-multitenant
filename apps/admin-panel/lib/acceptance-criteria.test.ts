import { describe, expect, it } from "vitest";

import {
  MAX_ACCEPTANCE_CRITERIA,
  MAX_CRITERION_LEN,
  cleanCriteria,
  criterionText,
} from "@/lib/acceptance-criteria";

describe("criterionText", () => {
  it("returns a plain string criterion verbatim", () => {
    expect(criterionText("composer audit limpio")).toBe("composer audit limpio");
  });

  it("flattens a structured criterion to its description/text/criterion/name", () => {
    expect(criterionText({ description: "endpoint responde 200" })).toBe("endpoint responde 200");
    expect(criterionText({ text: "lock fija versiones" })).toBe("lock fija versiones");
    expect(criterionText({ criterion: "sin vulnerabilidades" })).toBe("sin vulnerabilidades");
    expect(criterionText({ name: "tests en verde" })).toBe("tests en verde");
  });

  it("degrades an unknown-shaped object to JSON rather than throwing", () => {
    expect(criterionText({ weird: 42 })).toContain("42");
  });
});

describe("cleanCriteria", () => {
  it("trims each criterion and drops empty / whitespace-only rows", () => {
    const out = cleanCriteria([
      { text: "  un criterio  ", original: null },
      { text: "   ", original: null },
      { text: "", original: null },
      { text: "otro", original: null },
    ]);
    expect(out).toEqual(["un criterio", "otro"]);
  });

  it("emits plain strings for new rows and string-backed rows", () => {
    const out = cleanCriteria([
      { text: "nuevo", original: null },
      { text: "editado", original: "viejo" },
    ]);
    expect(out).toEqual(["nuevo", "editado"]);
  });

  it("preserves a structured (dict) criterion, overwriting only its description text", () => {
    const out = cleanCriteria([
      {
        text: "texto editado",
        original: { id: "c1", kind: "manual", check_type: "descriptive", description: "viejo" },
      },
    ]);
    expect(out).toEqual([
      { id: "c1", kind: "manual", check_type: "descriptive", description: "texto editado" },
    ]);
  });

  it("drops a structured criterion whose text was cleared", () => {
    const out = cleanCriteria([{ text: "   ", original: { id: "c1", description: "viejo" } }]);
    expect(out).toEqual([]);
  });

  it("caps the number of criteria", () => {
    const drafts = Array.from({ length: MAX_ACCEPTANCE_CRITERIA + 3 }, (_, i) => ({
      text: `criterio ${i}`,
      original: null,
    }));
    expect(cleanCriteria(drafts)).toHaveLength(MAX_ACCEPTANCE_CRITERIA);
  });

  it("caps the length of each criterion", () => {
    const long = "x".repeat(MAX_CRITERION_LEN + 50);
    const out = cleanCriteria([{ text: long, original: null }]);
    expect(out).toEqual([long.slice(0, MAX_CRITERION_LEN)]);
  });

  it("treats an array original as not-structured (emits a string)", () => {
    // An array is an object in JS but is not a structured criterion; never wrap.
    const out = cleanCriteria([{ text: "txt", original: ["a", "b"] }]);
    expect(out).toEqual(["txt"]);
  });
});
