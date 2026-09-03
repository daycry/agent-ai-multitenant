// @vitest-environment jsdom
//
// El marketplace del tenant: lo que el CATÁLOGO tiene que contar sin que nadie
// entre en una ficha (`task_mkt2_12` y `task_mkt2_10`).
//
// Las dos cosas que faltaban, y por qué cada una importa:
//
//   1. **Nadie se enteraba de que tenía actualizaciones.** El endpoint
//      `update-check` y el banner de la ficha estaban entregados, pero el
//      catálogo —la pantalla a la que se ENTRA— no los llamaba. Descubrir que
//      una instalación se había quedado atrás exigía abrir su ficha, una por
//      una, sabiendo de antemano lo que se iba a buscar. O sea: no se
//      descubría.
//   2. **Un listing propio en revisión se pintaba como uno más del catálogo.**
//      La cláusula de visibilidad es `published OR propio`, así que su autor lo
//      ve ahí… y nadie más. Sin decirlo, el catálogo le está afirmando que su
//      capacidad está disponible cuando es invisible para todo el mundo.
//
// Y el caso que se olvida: sin nada atrasado, el aviso NO existe. Una franja
// permanente diciendo «todo al día» enseña a no leer la franja.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

/** Mutable para poder mirar la pantalla con los ojos de un miembro sin rol. */
const usuario = {
  isSystemAdmin: false,
  isTenantAdmin: true,
  isTenantMember: true,
  isLoading: false,
};
vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => usuario,
}));

/** `task_mk_00`: instalar navega, así que la pantalla ya usa `useRouter`. */
const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: pushMock, prefetch: vi.fn() }),
  useParams: () => ({ id: "inst-1" }),
  useSearchParams: () => new URLSearchParams(),
}));

import { LanguageProvider } from "@/lib/lang-context";
import MarketplaceAdminPage from "./page";

const LISTING = {
  id: "listing-1",
  source_id: "src-1",
  tenant_id: null,
  kind: "tool",
  name: "acme-checker",
  version: "1.3.0",
  description: "Comprueba el estado de un servicio.",
  author: "Acme",
  trust_level: "verified",
  review_status: "published",
  rejection_reason: null,
  requested_permissions: [],
  is_signed: true,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

/** Un listing PROPIO que sigue en la cola: sólo lo ve este tenant. */
const EN_REVISION = {
  ...LISTING,
  id: "listing-2",
  tenant_id: "tenant-1",
  name: "informe-interno",
  version: "1.0.0",
  trust_level: "community",
  review_status: "pending_review",
  updated_at: "2026-08-14T09:00:00Z",
};

const INSTALACION = {
  id: "inst-1",
  tenant_id: "tenant-1",
  listing_id: "listing-1",
  project_id: null,
  version: "1.2.0",
  status: "enabled",
  granted_permissions: [],
  denied_permissions: [],
  installed_by: null,
  installed_at: "2026-08-01T00:00:00Z",
  revoked_at: null,
  revoked_by: null,
  created_at: "2026-08-01T00:00:00Z",
  updated_at: "2026-08-01T00:00:00Z",
};

const CHECK_ATRASADO = {
  installation_id: "inst-1",
  listing_id: "listing-1",
  name: "acme-checker",
  installed_version: "1.2.0",
  latest_version: "1.3.0",
  target_version: "1.3.0",
  outdated: true,
  update_available: true,
  latest_is_major_bump: false,
  permission_delta: {
    added: [{ type: "filesystem_write", value: ["/tmp"] }],
    removed: [],
    changed: [],
  },
  requires_consent: true,
};

const CHECK_AL_DIA = {
  ...CHECK_ATRASADO,
  installed_version: "1.3.0",
  target_version: null,
  outdated: false,
  update_available: false,
  permission_delta: null,
  requires_consent: false,
};

interface Escenario {
  listings?: unknown[];
  installations?: unknown[];
  check?: unknown;
  /** Respuesta del POST de instalación (o rechazo, para los caminos de error). */
  onInstall?: (body: unknown) => Promise<unknown>;
}

function montar({
  listings = [LISTING],
  installations = [INSTALACION],
  check,
  onInstall,
}: Escenario = {}) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    const url = String(path);
    if (url === "/marketplace/installations" && opts?.method === "POST") {
      return onInstall
        ? onInstall(opts.body)
        : Promise.resolve({ ...INSTALACION, id: "inst-nueva", status: "disabled" });
    }
    if (url.includes("update-check")) return Promise.resolve(check ?? CHECK_AL_DIA);
    if (url.startsWith("/marketplace/installations")) return Promise.resolve(installations);
    if (url.startsWith("/marketplace/listings")) return Promise.resolve(listings);
    if (url.startsWith("/marketplace/shares")) return Promise.resolve([]);
    return Promise.resolve([]);
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LanguageProvider>
        <MarketplaceAdminPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  pushMock.mockReset();
  usuario.isTenantAdmin = true;
  usuario.isTenantMember = true;
});

describe("el aviso de actualización del catálogo", () => {
  it("pregunta por CADA instalación viva, sin que nadie abra su ficha", async () => {
    montar({ check: CHECK_ATRASADO });
    await waitFor(() => {
      const rutas = apiFetchMock.mock.calls.map((c) => String(c[0]));
      expect(rutas.some((r) => r.includes("/installations/inst-1/update-check"))).toBe(true);
    });
  });

  it("dice cuántas hay atrasadas y con qué versión, en el catálogo mismo", async () => {
    montar({ check: CHECK_ATRASADO });
    const titulo = await screen.findByTestId("marketplace-updates-title");
    expect(titulo.textContent).toContain("1");

    const fila = await screen.findByTestId("marketplace-update-row-inst-1");
    expect(fila.textContent).toContain("acme-checker");
    expect(fila.textContent).toContain("1.2.0");
    expect(fila.textContent).toContain("1.3.0");
  });

  it("avisa de que pide permisos nuevos ANTES de mandar a nadie a actualizar", async () => {
    montar({ check: CHECK_ATRASADO });
    expect((await screen.findByTestId("marketplace-updates-consent")).textContent).toMatch(
      /permisos/i,
    );
    expect(screen.getByTestId("marketplace-update-consent-inst-1")).toBeTruthy();
  });

  it("lleva a la FICHA, que es donde el delta cabe y se decide", async () => {
    montar({ check: CHECK_ATRASADO });
    const enlace = (await screen.findByTestId("marketplace-update-open-inst-1")).closest("a");
    expect(enlace?.getAttribute("href")).toBe("/admin/marketplace/installations/inst-1");
    // Y NO promete aplicar nada desde una lista donde el delta no cabe.
    expect(enlace?.textContent ?? "").not.toMatch(/^Actualizar$/);
  });

  it("marca la tarjeta del catálogo de la capacidad atrasada", async () => {
    montar({ check: CHECK_ATRASADO });
    const chip = await screen.findByTestId("catalog-update-listing-1");
    expect(chip.textContent).toContain("1.3.0");
  });

  it("con todo al día no pinta ninguna franja", async () => {
    montar({ check: CHECK_AL_DIA });
    await screen.findByTestId("catalog-list");
    await waitFor(() => {
      const rutas = apiFetchMock.mock.calls.map((c) => String(c[0]));
      expect(rutas.some((r) => r.includes("update-check"))).toBe(true);
    });
    expect(screen.queryByTestId("marketplace-updates-callout")).toBeNull();
    expect(screen.queryByTestId("catalog-update-listing-1")).toBeNull();
  });

  it("una instalación REVOCADA no se pregunta ni se anuncia", async () => {
    montar({
      installations: [{ ...INSTALACION, status: "revoked" }],
      check: CHECK_ATRASADO,
    });
    await screen.findByTestId("catalog-list");
    await waitFor(() => {
      expect(apiFetchMock.mock.calls.length).toBeGreaterThan(0);
    });
    const rutas = apiFetchMock.mock.calls.map((c) => String(c[0]));
    expect(rutas.some((r) => r.includes("update-check"))).toBe(false);
    expect(screen.queryByTestId("marketplace-updates-callout")).toBeNull();
  });
});

describe("el estado de revisión en el catálogo", () => {
  it("un listing propio en cola NO se pinta como publicado", async () => {
    montar({ listings: [LISTING, EN_REVISION] });
    const badge = await screen.findByTestId("catalog-review-status-listing-2");
    expect(badge.textContent).toMatch(/pendiente de revisi/i);

    // Y con la consecuencia, que es la mitad que faltaba: mientras espera no lo
    // ve nadie más, ni siquiera un tenant con el que se comparta.
    const nota = await screen.findByTestId("catalog-review-note-listing-2");
    expect(nota.textContent).toMatch(
      /sólo es visible para tu tenant|solo es visible para tu tenant/i,
    );
  });

  it("lo publicado no arrastra ninguna etiqueta de estado (ruido)", async () => {
    montar({ listings: [LISTING] });
    await screen.findByTestId("catalog-listing-listing-1");
    expect(screen.queryByTestId("catalog-review-status-listing-1")).toBeNull();
    expect(screen.queryByTestId("catalog-review-note-listing-1")).toBeNull();
  });

  it("el reclamo de publicar dice que publicar deja el listing en cola", async () => {
    montar({ listings: [LISTING] });
    const nota = await screen.findByTestId("catalog-publish-review-note");
    expect(nota.textContent).toMatch(/cola de revisi/i);
  });
});

// ---------------------------------------------------------------------------
// `task_mk_00` — instalar desde el catálogo.
//
// Hasta hoy el panel no emitía UN SOLO `POST /marketplace/installations`: la
// pestaña «Instaladas» presuponía instalaciones que ninguna pantalla sabía
// crear. La tarjeta del catálogo era informativa y punto.
//
// Dos decisiones que estos tests fijan, y por qué:
//
//   * **La petición va síncrona** (sin `async_gates`). El camino asíncrono
//     devuelve `202` y deja la fila en `analyzing` hasta que un worker de la
//     lane `marketplace` la atienda; en una instalación que no levante esa lane,
//     `analyzing` es para siempre y la UI habría dicho «aceptada». Un fallo
//     honesto es mejor que un verde que miente.
//   * **El destino de la navegación sale de la respuesta, no de una suposición**:
//     un listing que exige consentimiento nace `disabled` con cero permisos
//     otorgados (`marketplace/install.py:367-374`) y ahí falta un paso
//     obligatorio; uno `verified` nace `enabled` y lo siguiente no es consentir,
//     es desplegar — que es justo lo que ofrece su ficha.
// ---------------------------------------------------------------------------

describe("instalar desde el catálogo", () => {
  it("ofrece instalar y manda el listing_id al backend", async () => {
    montar({ installations: [] });

    fireEvent.click(await screen.findByTestId("catalog-install-listing-1"));

    await waitFor(() => {
      const post = apiFetchMock.mock.calls.find(
        ([, opts]) => (opts as { method?: string } | undefined)?.method === "POST",
      );
      expect(post).toBeTruthy();
      expect(post?.[0]).toBe("/marketplace/installations");
      expect((post?.[1] as { body: { listing_id: string } }).body.listing_id).toBe("listing-1");
    });
  });

  it("no usa el camino asíncrono, que sin worker deja la instalación en análisis para siempre", async () => {
    montar({ installations: [] });

    fireEvent.click(await screen.findByTestId("catalog-install-listing-1"));

    await waitFor(() => {
      const post = apiFetchMock.mock.calls.find(
        ([, opts]) => (opts as { method?: string } | undefined)?.method === "POST",
      );
      const body = (post?.[1] as { body: Record<string, unknown> }).body;
      expect(body.async_gates ?? false).toBe(false);
    });
  });

  it("si el listing exige consentimiento, lleva a otorgar los permisos", async () => {
    montar({
      listings: [
        { ...LISTING, requested_permissions: [{ type: "filesystem_read", value: ["/x"] }] },
      ],
      installations: [],
      onInstall: () => Promise.resolve({ ...INSTALACION, id: "inst-nueva", status: "disabled" }),
    });

    fireEvent.click(await screen.findByTestId("catalog-install-listing-1"));

    await waitFor(() =>
      expect(pushMock).toHaveBeenCalledWith(
        "/admin/marketplace/installations/inst-nueva/permissions",
      ),
    );
  });

  it("si no hay nada que consentir, lleva a la ficha, que es donde se despliega", async () => {
    montar({
      installations: [],
      onInstall: () => Promise.resolve({ ...INSTALACION, id: "inst-nueva", status: "enabled" }),
    });

    fireEvent.click(await screen.findByTestId("catalog-install-listing-1"));

    await waitFor(() =>
      expect(pushMock).toHaveBeenCalledWith("/admin/marketplace/installations/inst-nueva"),
    );
    expect(pushMock).not.toHaveBeenCalledWith(
      "/admin/marketplace/installations/inst-nueva/permissions",
    );
  });

  it("un rechazo del backend se lee, y no se pinta el cuerpo crudo", async () => {
    const { ApiError } = await import("@/lib/api");
    montar({
      installations: [],
      onInstall: () =>
        Promise.reject(
          new ApiError(
            409,
            JSON.stringify({ detail: "listing already installed for this tenant" }),
          ),
        ),
    });

    fireEvent.click(await screen.findByTestId("catalog-install-listing-1"));

    const error = await screen.findByTestId("catalog-install-error-listing-1");
    expect(error.textContent).toContain("already installed");
    expect(error.textContent).not.toContain("{");
  });

  it("lo que ya está instalado no se ofrece instalar otra vez", async () => {
    montar({ installations: [{ ...INSTALACION, listing_id: "listing-1", status: "enabled" }] });

    await screen.findByTestId("catalog-installed-listing-1");
    expect(screen.queryByTestId("catalog-install-listing-1")).toBeNull();
    expect(
      apiFetchMock.mock.calls.some(
        ([, opts]) => (opts as { method?: string } | undefined)?.method === "POST",
      ),
    ).toBe(false);
  });

  it("instalar es cosa de un tenant_admin: a un miembro no se le ofrece", async () => {
    usuario.isTenantAdmin = false;
    montar({ installations: [] });

    await screen.findByText("acme-checker");
    expect(screen.queryByTestId("catalog-install-listing-1")).toBeNull();
  });
});

// La revisión de dos lentes (2026-09-03) encontró el caso que faltaba: un listing
// `community` que NO pide permisos también nace `disabled`, porque `needs_consent`
// mira sólo el nivel de confianza. Llevarlo a la pantalla de permisos lo dejaba en
// una página vacía sin botón que pulsar — y sin forma de habilitar la instalación.
describe("el destino tras instalar mira si hay algo que consentir", () => {
  it("un listing sin permisos declarados no manda a una pantalla de consentimiento vacía", async () => {
    montar({
      listings: [{ ...LISTING, trust_level: "community", requested_permissions: [] }],
      installations: [],
      onInstall: () => Promise.resolve({ ...INSTALACION, id: "inst-nueva", status: "disabled" }),
    });

    fireEvent.click(await screen.findByTestId("catalog-install-listing-1"));

    await waitFor(() =>
      expect(pushMock).toHaveBeenCalledWith("/admin/marketplace/installations/inst-nueva"),
    );
    expect(pushMock).not.toHaveBeenCalledWith(
      "/admin/marketplace/installations/inst-nueva/permissions",
    );
  });

  it("una instalación bloqueada no se pinta como instalada: se puede reintentar", async () => {
    montar({
      installations: [{ ...INSTALACION, listing_id: "listing-1", status: "blocked" }],
    });

    await screen.findByTestId("catalog-install-listing-1");
    expect(screen.queryByTestId("catalog-installed-listing-1")).toBeNull();
  });
});
