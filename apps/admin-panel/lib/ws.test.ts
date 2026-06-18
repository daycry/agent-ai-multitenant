import { describe, expect, it } from "vitest";

import { resolveWsBase } from "@/lib/ws";

/**
 * prod-01 / prod-09 — single-origin behind the Caddy reverse proxy. The
 * frontend is built with NEXT_PUBLIC_API_URL=/api (relative), so REST calls go
 * to the same origin and Caddy routes /api/* to the api-server. WebSockets must
 * still be ABSOLUTE ws(s):// URLs (the browser WebSocket API rejects relative
 * ones), so the ws base is derived from the page location when the API base is
 * relative. These pin that contract.
 */
describe("resolveWsBase", () => {
  const loc = { protocol: "https:", host: "panel.example.com" };

  it("swaps http→ws for an absolute base (dev: api-server on its own host)", () => {
    expect(resolveWsBase("http://localhost:8001", loc)).toBe("ws://localhost:8001");
  });

  it("swaps https→wss for an absolute base", () => {
    expect(resolveWsBase("https://api.example.com", loc)).toBe("wss://api.example.com");
  });

  it("derives an ABSOLUTE wss origin from location for a relative /api base", () => {
    expect(resolveWsBase("/api", loc)).toBe("wss://panel.example.com/api");
  });

  it("derives ws (not wss) when the page is served over http", () => {
    expect(resolveWsBase("/api", { protocol: "http:", host: "localhost:3000" })).toBe(
      "ws://localhost:3000/api",
    );
  });

  it("normalises a relative base without a leading slash", () => {
    expect(resolveWsBase("api", { protocol: "http:", host: "h" })).toBe("ws://h/api");
  });
});
