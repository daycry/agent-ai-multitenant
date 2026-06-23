import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { isSafeHref, renderPlanDraft } from "./plan-draft-md";

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
    expect(isSafeHref("javascript:alert(1)")).toBe(false); // bell (control) embebido
  });
});

describe("renderPlanDraft — las tablas anchas no desbordan la página", () => {
  const TABLE_MD = [
    "| Tarea | Modelo | Coste mínimo estimado | Coste máximo estimado |",
    "| --- | --- | --- | --- |",
    "| Implementar el endpoint de reservas | claude-opus-4-8 | 0,12 € | 0,48 € |",
  ].join("\n");

  it("envuelve la tabla en un contenedor con scroll horizontal propio (overflow-x-auto)", () => {
    const html = renderToStaticMarkup(renderPlanDraft(TABLE_MD));
    // El contenedor scrolleable debe aparecer ANTES de la <table>, de modo
    // que una tabla ancha haga scroll dentro de su caja en lugar de empujar
    // el ancho de la página entera.
    const wrapperIdx = html.indexOf("overflow-x-auto");
    const tableIdx = html.indexOf("<table");
    expect(wrapperIdx).toBeGreaterThanOrEqual(0);
    expect(tableIdx).toBeGreaterThanOrEqual(0);
    expect(wrapperIdx).toBeLessThan(tableIdx);
  });
});

describe("renderPlanDraft — los tokens largos sin espacios no desbordan la página", () => {
  it("aplica break-words al párrafo (una URL/identificador larguísimo se parte en vez de desbordar)", () => {
    // El contenido de planning es salida de un LLM: puede traer una URL o un
    // identificador larguísimo SIN espacios. Sin overflow-wrap, ese token
    // empuja el ancho del Card (que no tiene scroll propio) y aparece scroll
    // horizontal de PÁGINA. `break-words` lo parte en su caja.
    const longToken = `https://example.com/${"segmento-larguisimo".repeat(20)}`;
    const html = renderToStaticMarkup(renderPlanDraft(longToken));
    expect(html).toContain("<p");
    expect(html).toContain("break-words");
  });

  it("aplica break-words al `código en línea` (tokens monoespaciados largos también se parten)", () => {
    const html = renderToStaticMarkup(renderPlanDraft("Ruta: `" + "a".repeat(200) + "`"));
    const codeIdx = html.indexOf("<code");
    expect(codeIdx).toBeGreaterThanOrEqual(0);
    // La clase break-words debe estar en el propio <code>.
    expect(html.slice(codeIdx, codeIdx + 200)).toContain("break-words");
  });
});
