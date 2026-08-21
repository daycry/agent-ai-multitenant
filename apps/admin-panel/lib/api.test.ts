// @vitest-environment jsdom
/**
 * `apiFetch` after the cookie migration (ADR 0133 — tasks prod09_08 + prod09_10).
 *
 * Three behaviours, and each one is a bug the panel actually had:
 *
 *   1. the session must ride as a COOKIE (`credentials: "include"`) and NEVER
 *      as an `Authorization` header built from a token the page can read;
 *   2. every MUTATION must carry the double-submit `X-CSRF-Token` — forget it
 *      and the api-server answers 403 to every write;
 *   3. a 401 must clear the session and bounce to `/login?next=<route>`
 *      (frontend-3: the panel used to paint the raw 401 body and leave the user
 *      stuck on a dead screen).
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const setTenant = (id: string | null) => {
  document.cookie = "agentic_csrf=csrf-token-1; path=/";
  if (id) window.localStorage.setItem("admin-panel.tenant-id", id);
};

let fetchMock: ReturnType<typeof vi.fn>;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  vi.resetModules();
  window.localStorage.clear();
  document.cookie = "agentic_csrf=; path=/; max-age=0";
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

async function load() {
  return import("@/lib/api");
}

function lastInit(): RequestInit & { headers: Headers } {
  const init = fetchMock.mock.calls.at(-1)?.[1] as RequestInit;
  return { ...init, headers: init.headers as Headers };
}

describe("credential transport", () => {
  it("sends the session cookie and no Authorization header", async () => {
    setTenant(null);
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    const { apiFetch } = await load();

    await apiFetch("/auth/me");

    const init = lastInit();
    expect(init.credentials).toBe("include");
    expect(init.headers.has("Authorization")).toBe(false);
  });

  it("still forwards the acting tenant header", async () => {
    setTenant("11111111-1111-1111-1111-111111111111");
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    const { apiFetch } = await load();

    await apiFetch("/projects");

    expect(lastInit().headers.get("X-Tenant-Id")).toBe("11111111-1111-1111-1111-111111111111");
  });
});

describe("CSRF double-submit", () => {
  it("attaches X-CSRF-Token to a mutation", async () => {
    setTenant(null);
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    const { apiFetch } = await load();

    await apiFetch("/projects", { method: "POST", body: { name: "x" } });

    expect(lastInit().headers.get("X-CSRF-Token")).toBe("csrf-token-1");
  });

  it("does NOT attach it to a read", async () => {
    setTenant(null);
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    const { apiFetch } = await load();

    await apiFetch("/projects");

    expect(lastInit().headers.has("X-CSRF-Token")).toBe(false);
  });

  it("does not invent a header when there is no CSRF cookie", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ ok: true }));
    const { apiFetch } = await load();

    await apiFetch("/projects", { method: "DELETE" });

    expect(lastInit().headers.has("X-CSRF-Token")).toBe(false);
  });
});

describe("global 401 handling (frontend-3)", () => {
  it("clears the session and redirects to /login preserving the route", async () => {
    setTenant("22222222-2222-2222-2222-222222222222");
    fetchMock.mockResolvedValue(new Response("session has been revoked", { status: 401 }));
    const { ApiError, apiFetch, setUnauthorizedHandler } = await load();

    const seen: string[] = [];
    setUnauthorizedHandler((next) => seen.push(next));

    await expect(apiFetch("/projects")).rejects.toBeInstanceOf(ApiError);

    expect(seen).toHaveLength(1);
    // The handler is told WHERE the user was, so login can send them back.
    expect(seen[0]).toMatch(/^\//);
    // Session + tenant are dropped: a stale tenant would otherwise be
    // re-sent as the acting tenant of the NEXT user in the same tab.
    expect(document.cookie).not.toContain("agentic_csrf=csrf-token-1");
    expect(window.localStorage.getItem("admin-panel.tenant-id")).toBeNull();
  });

  it("does NOT fire on the login endpoint itself", async () => {
    // A wrong password is a 401 too. Bouncing to /login from /login is a
    // reload loop that eats the error message the user needs to see.
    fetchMock.mockResolvedValue(new Response("invalid email or password", { status: 401 }));
    const { ApiError, apiFetch, setUnauthorizedHandler } = await load();

    let fired = 0;
    setUnauthorizedHandler(() => {
      fired += 1;
    });

    await expect(apiFetch("/auth/login", { method: "POST", body: {} })).rejects.toBeInstanceOf(
      ApiError,
    );
    expect(fired).toBe(0);
  });

  it("does NOT fire when the 401 is about a THIRD-PARTY credential (MCP)", async () => {
    // `POST /projects/{id}/mcp/test-connection` contesta 401 con
    // `error_code: AUTH_ERROR` cuando el `auth_ref` del SERVIDOR MCP no se
    // resuelve. Ese 401 no habla de quien llama: habla de la credencial que el
    // operador acaba de teclear. Tratarlo como sesion muerta le vacia la sesion,
    // le borra el tenant y le tira a /login con el formulario a medio llenar —
    // y sin llegar a leer que credencial falla, que es a lo que fue.
    setTenant("22222222-2222-2222-2222-222222222222");
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: { error_code: "AUTH_ERROR" } }), { status: 401 }),
    );
    const { ApiError, apiFetch, setUnauthorizedHandler } = await load();

    let fired = 0;
    setUnauthorizedHandler(() => {
      fired += 1;
    });

    await expect(
      apiFetch("/projects/11111111-0000-0000-0000-000000000001/mcp/test-connection", {
        method: "POST",
        body: {},
      }),
    ).rejects.toBeInstanceOf(ApiError);

    expect(fired).toBe(0);
    // Y la sesion sigue en pie: el operador no ha perdido nada.
    expect(window.localStorage.getItem("admin-panel.tenant-id")).toBe(
      "22222222-2222-2222-2222-222222222222",
    );
  });

  it("does not fire on a 403", async () => {
    fetchMock.mockResolvedValue(new Response("forbidden", { status: 403 }));
    const { apiFetch, setUnauthorizedHandler } = await load();

    let fired = 0;
    setUnauthorizedHandler(() => {
      fired += 1;
    });

    await expect(apiFetch("/projects")).rejects.toThrow();
    expect(fired).toBe(0);
  });
});
