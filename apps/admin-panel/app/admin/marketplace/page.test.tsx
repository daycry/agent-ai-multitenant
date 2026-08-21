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
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: false,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
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
}

function montar({ listings = [LISTING], installations = [INSTALACION], check }: Escenario = {}) {
  apiFetchMock.mockImplementation((path: string) => {
    const url = String(path);
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
