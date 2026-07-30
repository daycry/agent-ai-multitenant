import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/auth", () => ({ getToken: vi.fn() }));
vi.mock("@/lib/tenant-storage", () => ({ getTenantId: vi.fn() }));

import { getToken } from "@/lib/auth";
import { getTenantId } from "@/lib/tenant-storage";
import { reconnectDelayMs, resolveWsBase, wsUrl } from "@/lib/ws";

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

describe("wsUrl", () => {
  // API_URL defaults to the absolute http://localhost:8001, so resolveWsBase
  // gives ws://localhost:8001 without needing a browser `window`.
  beforeEach(() => {
    vi.mocked(getToken).mockReturnValue("tok");
    vi.mocked(getTenantId).mockReturnValue(null);
  });

  it("appends the acting-as tenant_id when one is selected (WS mirror of X-Tenant-Id)", () => {
    vi.mocked(getTenantId).mockReturnValue("ten-1");
    expect(wsUrl("/ws/conversation/c1")).toBe(
      "ws://localhost:8001/ws/conversation/c1?token=tok&tenant_id=ten-1",
    );
  });

  it("omits tenant_id when no tenant is selected", () => {
    vi.mocked(getTenantId).mockReturnValue(null);
    expect(wsUrl("/ws/conversation/c1")).toBe("ws://localhost:8001/ws/conversation/c1?token=tok");
  });
});

describe("reconnectDelayMs", () => {
  it("backs off exponentially from a small base", () => {
    expect(reconnectDelayMs(0)).toBe(500);
    expect(reconnectDelayMs(1)).toBe(1000);
    expect(reconnectDelayMs(2)).toBe(2000);
  });

  it("caps the delay so reconnects never wait too long", () => {
    expect(reconnectDelayMs(10)).toBe(10_000);
    expect(reconnectDelayMs(100)).toBe(10_000);
  });

  it("treats negative attempts as the first attempt", () => {
    expect(reconnectDelayMs(-5)).toBe(500);
  });
});
