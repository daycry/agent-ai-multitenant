// @vitest-environment jsdom
//
// Publicar en el marketplace privado — la mitad de `task_mkt2_10` que faltaba:
// **que la UI diga que queda pendiente de revisión, y no «publicado»**.
//
// Desde el ADR 0142 D6 la fila nace en `pending_review` y espera a un System
// Admin. La pantalla contestaba «Listing publicado. Ya aparece en tu catálogo
// privado», que era falso dos veces — y la segunda es la grave: la cláusula de
// visibilidad del catálogo es `published OR propio`, de modo que mientras
// espera **no lo ve NADIE** salvo su tenant autor, ni siquiera aquél con el que
// se comparta por un grant. Quien publicaba y compartía se quedaba esperando
// una instalación que no podía ocurrir, sin nada en pantalla que lo explicara.
//
// Lo que estos tests fijan:
//
//   - el resultado sale del `review_status` que DEVUELVE el backend, no del
//     hecho de que la petición fuese bien;
//   - dice quién decide y qué pasa mientras tanto (las dos preguntas que
//     quedaban sin respuesta);
//   - el listado del propio tenant enseña el estado real de cada fila, con el
//     motivo cuando fue un rechazo — que es lo único con lo que se corrige.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
import PrivateMarketplacePage from "./page";

const BASE = {
  id: "listing-1",
  source_id: "src-1",
  tenant_id: "tenant-1",
  kind: "skill",
  name: "internal-reporter",
  version: "1.0.0",
  description: "Informe interno.",
  author: "Equipo Plataforma",
  trust_level: "community",
  review_status: "pending_review",
  reviewed_at: null,
  rejection_reason: null,
  requested_permissions: [],
  is_signed: false,
  created_at: "2026-08-14T09:00:00Z",
  updated_at: "2026-08-14T09:00:00Z",
};

function montar({ listings = [] as unknown[], publicado = BASE } = {}) {
  apiFetchMock.mockImplementation((path: string, init?: { method?: string }) => {
    const url = String(path);
    if (url.startsWith("/marketplace/private/listings") && init?.method === "POST") {
      return Promise.resolve(publicado);
    }
    if (url.startsWith("/marketplace/listings")) return Promise.resolve(listings);
    return Promise.resolve([]);
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LanguageProvider>
        <PrivateMarketplacePage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

/** Rellena el manifest y pulsa «Publicar». */
async function publicar() {
  fireEvent.change(await screen.findByTestId("private-manifest"), {
    target: { value: "name: x\nversion: 1.0.0\n" },
  });
  fireEvent.click(screen.getByTestId("private-publish-submit"));
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("publicar deja el listing EN COLA, y la UI lo dice", () => {
  it("lo avisa ANTES de pulsar, no como sorpresa después", async () => {
    montar();
    const nota = await screen.findByTestId("private-publish-review-note");
    expect(nota.textContent).toMatch(/no publica/i);
    expect(nota.textContent).toMatch(/cola de revisi/i);
  });

  it("el resultado dice «enviado a revisión», NO «publicado»", async () => {
    montar();
    await publicar();

    const aviso = await screen.findByTestId("private-publish-queued");
    expect(aviso.textContent).toMatch(/revisi/i);
    expect(aviso.textContent).not.toMatch(/^Listing publicado/);
    // El estado real, con la misma palabra que usa quien lo revisa.
    expect(screen.getByTestId("private-publish-queued-status").textContent).toMatch(
      /pendiente de revisi/i,
    );
    expect(screen.queryByTestId("private-publish-success")).toBeNull();
  });

  it("dice QUIÉN decide y QUÉ pasa mientras tanto", async () => {
    montar();
    await publicar();

    const aviso = await screen.findByTestId("private-publish-queued");
    expect(aviso.textContent).toMatch(/system admin/i);
    // Lo que nadie contaba: invisible para todos menos para el propio tenant,
    // grants de share incluidos.
    expect(aviso.textContent).toMatch(/comparta|compartas/i);
  });

  it("si el backend contestara «published», se dice eso y no lo contrario", async () => {
    // El estado sale de la respuesta, no de una suposición del cliente: si
    // algún día la revisión se salta (un listing de plataforma, un cambio de
    // política), esta pantalla no se queda mintiendo al revés.
    montar({ publicado: { ...BASE, review_status: "published" } });
    await publicar();

    expect((await screen.findByTestId("private-publish-success")).textContent).toMatch(/catálogo/i);
    expect(screen.queryByTestId("private-publish-queued")).toBeNull();
  });
});

describe("el catálogo privado enseña el estado REAL de cada fila", () => {
  it("pendiente de revisión: con desde cuándo espera y qué implica", async () => {
    montar({ listings: [BASE] });

    expect((await screen.findByTestId("private-listing-status-listing-1")).textContent).toMatch(
      /pendiente de revisi/i,
    );
    const nota = await screen.findByTestId("private-listing-note-listing-1");
    expect(nota.textContent).toMatch(/system admin/i);
    // Una referencia temporal: «pendiente» sin decir desde cuándo es media
    // verdad, y era la pista que el autor necesitaba para decidir si preguntar.
    expect(screen.getByTestId("review-status-since").textContent).toMatch(/2026/);
  });

  it("rechazado: el motivo se ve, porque es lo único con lo que se corrige", async () => {
    montar({
      listings: [
        {
          ...BASE,
          review_status: "rejected",
          rejection_reason: "El manifest pide allowed_domains: ['*'].",
        },
      ],
    });

    expect((await screen.findByTestId("private-listing-status-listing-1")).textContent).toMatch(
      /rechazad/i,
    );
    expect((await screen.findByTestId("review-status-reason")).textContent).toContain(
      "allowed_domains",
    );
  });

  it("publicado: el estado se ve y no arrastra explicación (no hay nada que explicar)", async () => {
    montar({ listings: [{ ...BASE, review_status: "published" }] });

    expect((await screen.findByTestId("private-listing-status-listing-1")).textContent).toMatch(
      /publicado/i,
    );
    expect(screen.queryByTestId("private-listing-note-listing-1")).toBeNull();
  });
});
