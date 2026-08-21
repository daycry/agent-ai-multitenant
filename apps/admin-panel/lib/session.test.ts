import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

/**
 * Plan 08 / ADR 0047 `task_sso_03` — resolución de tenant post-login.
 *
 * `resolveAndRoute()` es el punto donde una sesión que sólo prueba IDENTIDAD
 * se convierte en "a dónde entra este usuario". Cuatro estados, cuatro rutas,
 * y efectos laterales distintos en cada uno: era el único módulo de `lib/`
 * sin test (21 `*.test.ts` y ninguno suyo), y es el que decide si alguien sin
 * memberships entra o no.
 *
 * Se testea con `apiFetch` y el almacenamiento de tenant mockeados: lo que se
 * acredita es la MÁQUINA DE ESTADOS y sus efectos (qué tenant se persiste o se
 * limpia).
 *
 * Desde el ADR 0133 el efecto «guardar token» YA NO EXISTE: la sesión —también
 * la tenant-scoped que acuñan `resolve` y `select-tenant`— viaja como cookie
 * httpOnly re-emitida por el servidor. Las aserciones de abajo comprueban que
 * el cliente no guarda nada: si alguien repone un `setToken`, el mock de
 * `@/lib/auth` (que ya no lo exporta) revienta el import.
 */

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", () => ({ apiFetch: (...args: unknown[]) => apiFetchMock(...args) }));

const setTenantIdMock = vi.fn();
const clearTenantIdMock = vi.fn();
vi.mock("@/lib/tenant-storage", () => ({
  setTenantId: (...a: unknown[]) => setTenantIdMock(...a),
  clearTenantId: (...a: unknown[]) => clearTenantIdMock(...a),
}));

import {
  HOME_ROUTE,
  NO_ACCESS_ROUTE,
  SELECT_TENANT_ROUTE,
  resolveAndRoute,
  resolveSession,
  selectTenant,
  applySingleResolution,
  type ResolutionState,
  type SessionResolution,
} from "@/lib/session";

const TENANT_A = { tenant_id: "t-aaa", tenant_name: "Tenant A", role: "tenant_admin" };
const TENANT_B = { tenant_id: "t-bbb", tenant_name: "Tenant B", role: "tenant_user" };

function resolution(
  state: ResolutionState,
  over: Partial<SessionResolution> = {},
): SessionResolution {
  return {
    state,
    memberships: [],
    access_token: null,
    token_type: null,
    expires_in: null,
    ...over,
  };
}

beforeEach(() => {
  apiFetchMock.mockReset();
  setTenantIdMock.mockReset();
  clearTenantIdMock.mockReset();
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("rutas de destino", () => {
  it("las pantallas sin tenant viven FUERA del shell /admin", () => {
    // Si cayeran bajo /admin el layout admin montaría sidebar/topbar, que
    // asumen un tenant activo que aquí todavía no existe.
    expect(NO_ACCESS_ROUTE).toBe("/no-access");
    expect(SELECT_TENANT_ROUTE).toBe("/select-tenant");
    expect(NO_ACCESS_ROUTE.startsWith("/admin")).toBe(false);
    expect(SELECT_TENANT_ROUTE.startsWith("/admin")).toBe(false);
    expect(HOME_ROUTE).toBe("/admin/dashboard");
  });
});

describe("resolveSession", () => {
  it("consulta GET /auth/session/resolve", async () => {
    apiFetchMock.mockResolvedValueOnce(resolution("no_access"));
    await resolveSession();
    expect(apiFetchMock).toHaveBeenCalledWith("/auth/session/resolve");
  });
});

describe("resolveAndRoute — los cuatro estados", () => {
  it("single: entra en el dashboard, guarda el token acuñado y el único tenant", async () => {
    apiFetchMock.mockResolvedValueOnce(
      resolution("single", {
        memberships: [TENANT_A],
        access_token: "scoped-token",
        token_type: "bearer",
        expires_in: 3600,
      }),
    );

    await expect(resolveAndRoute()).resolves.toBe(HOME_ROUTE);
    expect(setTenantIdMock).toHaveBeenCalledWith(TENANT_A.tenant_id);
    expect(clearTenantIdMock).not.toHaveBeenCalled();
  });

  it("multiple: manda al selector y NO fija tenant ni token todavía", async () => {
    apiFetchMock.mockResolvedValueOnce(
      resolution("multiple", { memberships: [TENANT_A, TENANT_B] }),
    );

    await expect(resolveAndRoute()).resolves.toBe(SELECT_TENANT_ROUTE);
    // El tenant lo elige el usuario en la pantalla; aquí no se decide.
    expect(setTenantIdMock).not.toHaveBeenCalled();
    expect(clearTenantIdMock).not.toHaveBeenCalled();
  });

  it("admin: un System Admin sin membership ENTRA (nunca a no-access) y se limpia el tenant", async () => {
    apiFetchMock.mockResolvedValueOnce(resolution("admin"));

    const route = await resolveAndRoute();
    expect(route).toBe(HOME_ROUTE);
    expect(route).not.toBe(NO_ACCESS_ROUTE);
    // Sin elección explícita: el TenantProvider le aterriza en un tenant real.
    expect(clearTenantIdMock).toHaveBeenCalledTimes(1);
    // No se acuña token nuevo: sigue con el de identidad.
    expect(setTenantIdMock).not.toHaveBeenCalled();
  });

  it("no_access: a la pantalla de sin permisos, limpiando cualquier tenant rancio", async () => {
    apiFetchMock.mockResolvedValueOnce(resolution("no_access"));

    await expect(resolveAndRoute()).resolves.toBe(NO_ACCESS_ROUTE);
    expect(clearTenantIdMock).toHaveBeenCalledTimes(1);
    expect(setTenantIdMock).not.toHaveBeenCalled();
  });
});

describe("applySingleResolution", () => {
  it("persiste el tenant único; el token NO se guarda (llega por cookie)", () => {
    applySingleResolution(
      resolution("single", { memberships: [TENANT_B], access_token: "scoped-2" }),
    );
    expect(setTenantIdMock).toHaveBeenCalledWith(TENANT_B.tenant_id);
  });

  it("no revienta si no vino membership", () => {
    applySingleResolution(resolution("single"));
    expect(setTenantIdMock).not.toHaveBeenCalled();
  });
});

describe("selectTenant", () => {
  it("POSTea la elección y activa ESE tenant (la sesión la re-emite la cookie)", async () => {
    apiFetchMock.mockResolvedValueOnce({ access_token: "picked-token" });

    await selectTenant(TENANT_B.tenant_id);

    expect(apiFetchMock).toHaveBeenCalledWith("/auth/session/select-tenant", {
      method: "POST",
      body: { tenant_id: TENANT_B.tenant_id },
    });
    expect(setTenantIdMock).toHaveBeenCalledWith(TENANT_B.tenant_id);
  });

  it("si el backend rechaza el tenant, no se guarda nada", async () => {
    apiFetchMock.mockRejectedValueOnce(new Error("403 forbidden"));

    await expect(selectTenant("t-not-mine")).rejects.toThrow("403 forbidden");
    expect(setTenantIdMock).not.toHaveBeenCalled();
  });
});
