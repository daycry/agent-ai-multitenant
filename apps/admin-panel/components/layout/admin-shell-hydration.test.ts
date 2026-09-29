import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

describe("el shell publica la señal de hidratación", () => {
  it("AdminShell renderiza <HydrationMarker />", () => {
    const src = readFileSync(join(__dirname, "admin-shell.tsx"), "utf-8");
    expect(src).toContain("<HydrationMarker />");
  });
});
