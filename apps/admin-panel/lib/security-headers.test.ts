import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { assertPublicApiUrl, buildSecurityHeaders, type SecurityHeader } from "./security-headers";

// `next.config.js` es CJS; vitest lo importa igual (named exports estáticos).
import nextConfig from "../next.config.js";

function byKey(headers: SecurityHeader[], key: string): string | undefined {
  return headers.find((h) => h.key.toLowerCase() === key.toLowerCase())?.value;
}

const PROD = { nodeEnv: "production", apiUrl: "/api" } as const;

describe("buildSecurityHeaders — cabeceras simples", () => {
  it("emite nosniff, Referrer-Policy y X-Frame-Options DENY", () => {
    const headers = buildSecurityHeaders(PROD);

    expect(byKey(headers, "X-Content-Type-Options")).toBe("nosniff");
    expect(byKey(headers, "Referrer-Policy")).toBe("strict-origin-when-cross-origin");
    expect(byKey(headers, "X-Frame-Options")).toBe("DENY");
  });

  it("no repite ninguna clave (una cabecera duplicada es indefinida)", () => {
    const keys = buildSecurityHeaders(PROD).map((h) => h.key.toLowerCase());
    expect(new Set(keys).size).toBe(keys.length);
  });
});

describe("buildSecurityHeaders — CSP en vigor (baseline)", () => {
  it("aplica frame-ancestors 'none' de verdad, no en report-only", () => {
    const csp = byKey(buildSecurityHeaders(PROD), "Content-Security-Policy");
    expect(csp).toBeDefined();
    expect(csp).toContain("frame-ancestors 'none'");
  });

  it("incluye base-uri, object-src y form-action en la baseline", () => {
    const csp = byKey(buildSecurityHeaders(PROD), "Content-Security-Policy") ?? "";
    expect(csp).toContain("base-uri 'self'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("form-action 'self'");
  });

  it("la baseline NO restringe script/style/connect: no puede dejar el panel en blanco", () => {
    const csp = byKey(buildSecurityHeaders(PROD), "Content-Security-Policy") ?? "";
    for (const directive of ["default-src", "script-src", "style-src", "connect-src", "img-src"]) {
      expect(csp).not.toContain(directive);
    }
  });
});

describe("buildSecurityHeaders — CSP completa en report-only", () => {
  it("acompaña la baseline con la política completa en Report-Only", () => {
    const headers = buildSecurityHeaders(PROD);
    const report = byKey(headers, "Content-Security-Policy-Report-Only") ?? "";

    expect(report).toContain("default-src 'self'");
    expect(report).toContain("frame-ancestors 'none'");
    expect(report).toContain("object-src 'none'");
  });

  it("style-src permite inline (Tailwind y los SVG de mermaid lo exigen)", () => {
    const report = byKey(buildSecurityHeaders(PROD), "Content-Security-Policy-Report-Only") ?? "";
    expect(report).toMatch(/style-src [^;]*'unsafe-inline'/);
  });

  it("media-src admite blob: — el TTS del asistente reproduce un Blob de audio", () => {
    const report = byKey(buildSecurityHeaders(PROD), "Content-Security-Policy-Report-Only") ?? "";
    expect(report).toMatch(/media-src [^;]*blob:/);
  });

  it("en producción NO concede 'unsafe-eval'", () => {
    const report = byKey(buildSecurityHeaders(PROD), "Content-Security-Policy-Report-Only") ?? "";
    expect(report).not.toContain("'unsafe-eval'");
  });

  it("en desarrollo concede 'unsafe-eval' y ws: (HMR de Next)", () => {
    const headers = buildSecurityHeaders({ nodeEnv: "development", apiUrl: "" });
    const report = byKey(headers, "Content-Security-Policy-Report-Only") ?? "";

    expect(report).toMatch(/script-src [^;]*'unsafe-eval'/);
    expect(report).toMatch(/connect-src [^;]*ws:/);
  });
});

describe("buildSecurityHeaders — connect-src y el origen de la API", () => {
  it("con base absoluta añade el origen http y su equivalente ws", () => {
    const headers = buildSecurityHeaders({
      nodeEnv: "production",
      apiUrl: "https://api.example.test",
    });
    const report = byKey(headers, "Content-Security-Policy-Report-Only") ?? "";
    const connect = /connect-src ([^;]*)/.exec(report)?.[1] ?? "";

    expect(connect).toContain("https://api.example.test");
    expect(connect).toContain("wss://api.example.test");
  });

  it("con base relativa (single-origin tras Caddy) se queda en 'self'", () => {
    const report = byKey(buildSecurityHeaders(PROD), "Content-Security-Policy-Report-Only") ?? "";
    const connect = /connect-src ([^;]*)/.exec(report)?.[1] ?? "";

    expect(connect).toContain("'self'");
    // Nada de pegar "/api" como si fuera un origen.
    expect(connect).not.toContain("/api");
  });

  it("con base http:// deriva ws:// (no wss://)", () => {
    const headers = buildSecurityHeaders({
      nodeEnv: "production",
      apiUrl: "http://localhost:8001",
    });
    const connect =
      /connect-src ([^;]*)/.exec(
        byKey(headers, "Content-Security-Policy-Report-Only") ?? "",
      )?.[1] ?? "";

    expect(connect).toContain("http://localhost:8001");
    expect(connect).toContain("ws://localhost:8001");
    expect(connect).not.toContain("wss://localhost:8001");
  });
});

describe("buildSecurityHeaders — promoción de la CSP completa", () => {
  it("con enforceCsp la política completa pasa a Content-Security-Policy y desaparece el report-only", () => {
    const headers = buildSecurityHeaders({ ...PROD, enforceCsp: true });

    const csp = byKey(headers, "Content-Security-Policy") ?? "";
    expect(csp).toContain("default-src 'self'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(byKey(headers, "Content-Security-Policy-Report-Only")).toBeUndefined();
  });
});

describe("assertPublicApiUrl", () => {
  it("revienta el build de producción cuando la variable falta", () => {
    expect(() => assertPublicApiUrl({ nodeEnv: "production" })).toThrow(/NEXT_PUBLIC_API_URL/);
  });

  it("revienta también con cadena vacía (el ARG del Dockerfile la deja así)", () => {
    expect(() => assertPublicApiUrl({ nodeEnv: "production", apiUrl: "" })).toThrow(
      /NEXT_PUBLIC_API_URL/,
    );
    expect(() => assertPublicApiUrl({ nodeEnv: "production", apiUrl: "   " })).toThrow(
      /NEXT_PUBLIC_API_URL/,
    );
  });

  it("no molesta en desarrollo (el fallback a :8001 es legítimo ahí)", () => {
    expect(() => assertPublicApiUrl({ nodeEnv: "development" })).not.toThrow();
  });

  it("pasa cuando está puesta", () => {
    expect(() => assertPublicApiUrl({ nodeEnv: "production", apiUrl: "/api" })).not.toThrow();
  });
});

// El patrón dominante de esta base es "mecanismo entregado, cero llamantes"
// (docs/03-guides/verificar-antes-de-implementar.md §5). Estos tests comprueban
// que next.config.js REALMENTE sirve las cabeceras, no solo que la función exista.
describe("next.config.js — cableado real", () => {
  beforeEach(() => {
    // El build de producción es el caso que importa: allí el assert está activo
    // y la CSP no debe llevar 'unsafe-eval'.
    vi.stubEnv("NODE_ENV", "production");
    vi.stubEnv("NEXT_PUBLIC_API_URL", "/api");
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("declara headers() y lo aplica a todas las rutas", async () => {
    expect(typeof nextConfig.headers).toBe("function");

    const rules = await nextConfig.headers();
    expect(rules).toHaveLength(1);
    expect(rules[0].source).toBe("/(.*)");
  });

  it("las cabeceras servidas incluyen las cuatro de la tarea", async () => {
    const rules = await nextConfig.headers();
    const keys = rules[0].headers.map((h) => h.key.toLowerCase());

    expect(keys).toContain("x-content-type-options");
    expect(keys).toContain("referrer-policy");
    expect(keys).toContain("x-frame-options");
    expect(keys).toContain("content-security-policy");
  });

  it("el build de producción NO cuela 'unsafe-eval' por la config real", async () => {
    const rules = await nextConfig.headers();
    const values = rules[0].headers.map((h) => h.value).join(" | ");

    expect(values).not.toContain("'unsafe-eval'");
  });

  it("headers() revienta el build de producción si falta NEXT_PUBLIC_API_URL", async () => {
    vi.stubEnv("NEXT_PUBLIC_API_URL", "");

    await expect(nextConfig.headers()).rejects.toThrow(/NEXT_PUBLIC_API_URL/);
  });

  it("sigue desactivando el X-Powered-By de Next", () => {
    expect(nextConfig.poweredByHeader).toBe(false);
  });
});
