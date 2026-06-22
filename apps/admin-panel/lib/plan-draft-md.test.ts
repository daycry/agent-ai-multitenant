import { describe, expect, it } from "vitest";

import { isSafeHref } from "./plan-draft-md";

describe("isSafeHref — allowlist de esquemas para enlaces de contenido de agente", () => {
  it("permite http/https/mailto y URLs relativas", () => {
    expect(isSafeHref("https://example.com")).toBe(true);
    expect(isSafeHref("http://example.com/path?q=1")).toBe(true);
    expect(isSafeHref("mailto:alguien@example.com")).toBe(true);
    expect(isSafeHref("/admin/projects/1")).toBe(true); // relativa
    expect(isSafeHref("#seccion")).toBe(true); // ancla
    expect(isSafeHref("./pagina")).toBe(true);
  });

  it("bloquea javascript:, data:, vbscript: y file:", () => {
    expect(isSafeHref("javascript:alert(1)")).toBe(false);
    expect(isSafeHref("data:text/html;base64,PHNjcmlwdD4=")).toBe(false);
    expect(isSafeHref("vbscript:msgbox(1)")).toBe(false);
    expect(isSafeHref("file:///etc/passwd")).toBe(false);
  });

  it("bloquea trucos de espacios/control/mayúsculas en el esquema", () => {
    expect(isSafeHref("  javascript:alert(1)")).toBe(false); // espacios delante
    expect(isSafeHref("JavaScript:alert(1)")).toBe(false); // mayúsculas
    expect(isSafeHref("java\tscript:alert(1)")).toBe(false); // tab embebido
    expect(isSafeHref("java\nscript:alert(1)")).toBe(false); // newline embebido
    expect(isSafeHref("javascript:alert(1)")).toBe(false); // control char
  });
});
