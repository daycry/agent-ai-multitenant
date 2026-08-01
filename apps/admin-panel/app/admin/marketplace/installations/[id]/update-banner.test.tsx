// @vitest-environment jsdom
//
// El banner «v X.Y disponible» de la ficha de instalación (`task_mkt2_12`).
//
// Este banner faltaba. El endpoint `update-check` y el `POST …/update` estaban
// entregados y probados, y la casilla del plan se dio por cerrada con eso — pero
// la tarea pedía además la UI, y sin ella ningún administrador iba a descubrir
// que su instalación se había quedado atrás. Estos tests son lo que impide que
// vuelva a pasar: cada uno afirma una de las tres reglas que hacen que el banner
// no mienta.
//
//   1. El delta se enseña ANTES del botón. Actualizar puede ampliar lo que la
//      capacidad hace; un botón sin el delta a la vista arranca consentimiento
//      a ciegas.
//   2. Un salto de MAJOR no es un clic más: se anuncia y se pide el opt-in.
//   3. Si va a hacer falta re-consentimiento, el botón lo DICE — prometer un
//      clic y contestar un 409 con una lista de permisos es la peor UI posible.
//
// Y el caso que más se olvida: cuando NO hay actualización, el banner no existe.
// Una franja permanente diciendo «estás al día» enseña a ignorar la franja.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import {
  UpdateBanner,
  deltaWidens,
  pendingTypes,
  type UpdateCheck,
} from "@/app/admin/marketplace/installations/[id]/update-banner";

const INSTALLATION_ID = "inst-1";

const AL_DIA: UpdateCheck = {
  installation_id: INSTALLATION_ID,
  installed_version: "1.2.0",
  latest_version: "1.2.0",
  target_version: null,
  outdated: false,
  update_available: false,
  latest_is_major_bump: false,
  permission_delta: null,
  requires_consent: false,
};

const CON_MAS_PERMISOS: UpdateCheck = {
  ...AL_DIA,
  latest_version: "1.3.0",
  target_version: "1.3.0",
  outdated: true,
  update_available: true,
  permission_delta: {
    added: [{ type: "filesystem_write", value: ["/tmp"] }],
    removed: [],
    changed: [{ type: "allowed_domains", from: ["api.acme.com"], to: ["*"] }],
  },
  requires_consent: true,
};

function montar(check: UpdateCheck) {
  apiFetchMock.mockImplementation((path: string) => {
    if (String(path).includes("update-check")) return Promise.resolve(check);
    return Promise.resolve({});
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <UpdateBanner installationId={INSTALLATION_ID} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("lógica pura del delta", () => {
  it("quitar un permiso NO cuenta como ampliación: no hay nada que decidir", () => {
    const soloQuita = { added: [], removed: [{ type: "network" }], changed: [] };
    expect(deltaWidens(soloQuita)).toBe(false);
    expect(pendingTypes(soloQuita)).toEqual([]);
  });

  it("añadir o cambiar SÍ amplía, y ambos entran en los tipos a consentir", () => {
    expect(deltaWidens(CON_MAS_PERMISOS.permission_delta)).toBe(true);
    expect(pendingTypes(CON_MAS_PERMISOS.permission_delta)).toEqual([
      "allowed_domains",
      "filesystem_write",
    ]);
  });

  it("sin delta no amplía nada (una instalación sin histórico previo)", () => {
    expect(deltaWidens(null)).toBe(false);
    expect(pendingTypes(undefined)).toEqual([]);
  });
});

describe("el banner", () => {
  it("NO se pinta cuando la instalación está al día", async () => {
    montar(AL_DIA);
    await waitFor(() => expect(apiFetchMock).toHaveBeenCalled());
    expect(screen.queryByTestId("update-banner")).toBeNull();
  });

  it("regla 1: enseña el delta de permisos en claro, no solo la versión", async () => {
    montar(CON_MAS_PERMISOS);
    await screen.findByTestId("update-banner");
    expect(screen.getByTestId("update-banner-headline").textContent).toContain("1.3.0");
    expect(screen.getByTestId("update-banner-headline").textContent).toContain("1.2.0");

    const delta = await screen.findByTestId("update-banner-delta");
    expect(delta).toBeTruthy();
    expect(screen.getByTestId("update-delta-added-filesystem_write")).toBeTruthy();
    // El ensanche `["api.acme.com"] → ["*"]` es el caso que no se puede pasar
    // por alto: no añade un permiso nuevo, amplía uno que ya estaba.
    const cambiado = screen.getByTestId("update-delta-changed-allowed_domains");
    expect(cambiado.textContent).toContain("api.acme.com");
    expect(cambiado.textContent).toContain("*");
  });

  it("regla 3: con permisos nuevos el botón dice que habrá que revisar", async () => {
    montar(CON_MAS_PERMISOS);
    const boton = await screen.findByTestId("update-banner-apply");
    expect(boton.textContent).toMatch(/revisar/i);
  });

  it("sin permisos nuevos el botón es un simple «Actualizar»", async () => {
    montar({
      ...CON_MAS_PERMISOS,
      permission_delta: { added: [], removed: [], changed: [] },
      requires_consent: false,
    });
    const boton = await screen.findByTestId("update-banner-apply");
    expect(boton.textContent).toMatch(/actualizar/i);
    expect(screen.queryByTestId("update-banner-delta")).toBeNull();
  });

  it("un delta que solo QUITA permisos lo dice, y no pide decidir nada", async () => {
    montar({
      ...CON_MAS_PERMISOS,
      permission_delta: { added: [], removed: [{ type: "network" }], changed: [] },
      requires_consent: false,
    });
    expect(await screen.findByTestId("update-banner-narrows")).toBeTruthy();
    expect(screen.queryByTestId("update-banner-delta")).toBeNull();
  });

  it("regla 2: un salto de major NO ofrece actualizar, ofrece verlo primero", async () => {
    montar({
      ...AL_DIA,
      latest_version: "2.0.0",
      target_version: null, // sin opt-in el servidor no propone destino
      outdated: true,
      update_available: true,
      latest_is_major_bump: true,
    });
    await screen.findByTestId("update-banner");
    expect(screen.getByTestId("update-banner-major")).toBeTruthy();
    expect(screen.getByTestId("update-banner-allow-major")).toBeTruthy();
    expect(screen.queryByTestId("update-banner-apply")).toBeNull();
  });

  it("el opt-in de major re-consulta con allow_major=true", async () => {
    montar({
      ...AL_DIA,
      latest_version: "2.0.0",
      target_version: null,
      outdated: true,
      update_available: true,
      latest_is_major_bump: true,
    });
    fireEvent.click(await screen.findByTestId("update-banner-allow-major"));
    await waitFor(() => {
      const llamadas = apiFetchMock.mock.calls.map((c) => String(c[0]));
      expect(llamadas.some((p) => p.includes("allow_major=true"))).toBe(true);
    });
  });

  it("actualizar manda las decisiones de consentimiento de los tipos pendientes", async () => {
    montar(CON_MAS_PERMISOS);
    fireEvent.click(await screen.findByTestId("update-banner-apply"));
    await waitFor(() => {
      const post = apiFetchMock.mock.calls.find(
        (c) => String(c[0]).endsWith("/update") && (c[1] as { method?: string })?.method === "POST",
      );
      expect(post).toBeTruthy();
      const body = JSON.parse((post![1] as { body: string }).body);
      expect(body.consent).toEqual({ allowed_domains: "grant", filesystem_write: "grant" });
    });
  });
});
