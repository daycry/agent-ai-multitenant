// @vitest-environment jsdom
// La cola de revisión del marketplace (ADR 0142 D6, `task_mkt2_10`).
//
// Lo que este test protege, y por qué cada cosa:
//
//   - **Rechazar sin motivo no manda nada.** Es la guarda de UI que evita el
//     viaje al 422; si desaparece, el operador ve un error de servidor donde
//     debería ver un campo obligatorio.
//   - **Solo se pintan las acciones legales.** Un botón «Aprobar» sobre algo ya
//     publicado devolvería 409; ofrecerlo es prometer y luego negar.
//   - **El delta de permisos se ve ANTES de decidir**, y el ensanche
//     `["api.acme.com"] → ["*"]` sale marcado. Revisar sin el delta es aprobar
//     por el nombre del listing, que es lo que D6 quiere impedir.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: true,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import { LanguageProvider } from "@/lib/lang-context";
import MarketplaceReviewPage from "./page";

const PENDING = {
  id: "listing-1",
  tenant_id: "11111111-2222-3333-4444-555555555555",
  kind: "tool",
  name: "acme-checker",
  version: "2.0.0",
  description: "Comprueba el estado de un servicio.",
  author: "Acme",
  trust_level: "community",
  review_status: "pending_review",
  reviewed_at: null,
  rejection_reason: null,
  manifest: { entrypoint: "acme.main:run" },
  requested_permissions: [
    { type: "allowed_domains", value: ["*"] },
    { type: "allowed_paths", value: ["/tmp"] },
  ],
  created_at: "2026-08-01T00:00:00Z",
};

const VERSIONS = [
  {
    id: "v2",
    listing_id: "listing-1",
    version: "2.0.0",
    changelog: "Ahora habla con cualquier dominio.",
    config_schema: null,
    requested_permissions: PENDING.requested_permissions,
    reviewed_by: null,
    reviewed_at: null,
    created_at: "2026-08-01T00:00:00Z",
  },
  {
    id: "v1",
    listing_id: "listing-1",
    version: "1.0.0",
    changelog: "Primera.",
    config_schema: null,
    requested_permissions: [{ type: "allowed_domains", value: ["api.acme.com"] }],
    reviewed_by: "admin",
    reviewed_at: "2026-07-30T00:00:00Z",
    created_at: "2026-07-30T00:00:00Z",
  },
];

function wireApi(queue: unknown[] = [PENDING], versions: unknown[] = VERSIONS) {
  apiFetchMock.mockImplementation((path: string, init?: RequestInit) => {
    if (init?.method === "POST") return Promise.resolve({});
    if (path.startsWith("/admin/marketplace/review-queue")) return Promise.resolve(queue);
    if (path.includes("/versions")) return Promise.resolve(versions);
    return Promise.resolve([]);
  });
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <MarketplaceReviewPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("cola de revisión", () => {
  it("lista lo pendiente y pide la cola al endpoint del admin", async () => {
    wireApi();
    renderPage();

    await screen.findByText("acme-checker");
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/admin/marketplace/review-queue?review_status=pending_review",
    );
  });

  it("enseña el ensanche de permisos y lo marca como algo que mirar", async () => {
    wireApi();
    renderPage();

    await screen.findByTestId("diff-changed");
    expect(screen.getByTestId("diff-changed").textContent).toContain("allowed_domains");
    expect(screen.getByTestId("diff-added").textContent).toContain("allowed_paths");
    expect(screen.getByTestId("diff-needs-attention")).toBeTruthy();
  });

  it("aprobar manda promote=false; aprobar-y-verificar manda promote=true", async () => {
    wireApi();
    renderPage();

    fireEvent.click(await screen.findByTestId("approve"));
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(
        "/admin/marketplace/listings/listing-1/approve",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ promote: false }) }),
      ),
    );

    fireEvent.click(screen.getByTestId("approve-and-promote"));
    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(
        "/admin/marketplace/listings/listing-1/approve",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ promote: true }) }),
      ),
    );
  });

  it("rechazar SIN motivo no llama al backend y avisa", async () => {
    wireApi();
    renderPage();

    fireEvent.click(await screen.findByTestId("reject"));
    fireEvent.click(await screen.findByTestId("reject-confirm"));

    await screen.findByTestId("review-error");
    const rejectCalls = apiFetchMock.mock.calls.filter(([path]) =>
      String(path).endsWith("/reject"),
    );
    expect(rejectCalls).toHaveLength(0);
  });

  it("rechazar CON motivo lo manda tal cual", async () => {
    wireApi();
    renderPage();

    fireEvent.click(await screen.findByTestId("reject"));
    const textarea = (await screen.findByTestId("reject-reason")).querySelector("textarea");
    expect(textarea).toBeTruthy();
    fireEvent.change(textarea!, { target: { value: "Pide acceso a toda la red." } });
    fireEvent.click(screen.getByTestId("reject-confirm"));

    await waitFor(() =>
      expect(apiFetchMock).toHaveBeenCalledWith(
        "/admin/marketplace/listings/listing-1/reject",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ reason: "Pide acceso a toda la red." }),
        }),
      ),
    );
  });

  it("un listing publicado no ofrece aprobar ni rechazar, sí promocionar", async () => {
    wireApi([{ ...PENDING, review_status: "published" }]);
    renderPage();

    await screen.findByTestId("promote");
    expect(screen.queryByTestId("approve")).toBeNull();
    expect(screen.queryByTestId("reject")).toBeNull();
  });

  it("un `verified` ofrece BAJAR, no volver a subir", async () => {
    wireApi([{ ...PENDING, review_status: "published", trust_level: "verified" }]);
    renderPage();

    await screen.findByTestId("demote");
    expect(screen.queryByTestId("promote")).toBeNull();
  });

  it("con la cola vacía lo dice en vez de enseñar una lista en blanco", async () => {
    wireApi([]);
    renderPage();

    await screen.findByTestId("review-queue-empty");
  });

  it("la primera versión de un listing no finge tener un diff", async () => {
    wireApi([PENDING], [VERSIONS[0]]);
    renderPage();

    await screen.findByTestId("permission-diff");
    expect(screen.queryByTestId("diff-changed")).toBeNull();
    expect(screen.queryByTestId("diff-needs-attention")).toBeNull();
  });
});
