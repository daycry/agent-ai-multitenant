// @vitest-environment jsdom
// task_mem_02: el visor del run resuelve `[mem:<id corto>]` contra la API de memorias.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { CitedText } from "./memory-citations";

function mount(text: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <p data-testid="out">
        <CitedText text={text} />
      </p>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

const RESOLVED = {
  prefix: "abcdef12",
  status: "ok",
  memory: { id: "abcdef12-0000-0000-0000-000000000000", content: "El proyecto usa asyncpg." },
};

describe("CitedText", () => {
  it("sin citas pinta el texto tal cual y no consulta nada", () => {
    mount("Todo hecho, sin memorias.");
    expect(screen.getByTestId("out").textContent).toBe("Todo hecho, sin memorias.");
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it("resuelve la cita por prefijo de id y despliega la memoria citada", async () => {
    apiFetchMock.mockResolvedValue([RESOLVED]);
    mount("Elegí asyncpg [mem:abcdef12] por eso.");

    expect(apiFetchMock).toHaveBeenCalledWith("/memories/resolve?prefixes=abcdef12");
    const chip = await screen.findByRole("button", { name: /mem:abcdef12/ });
    await waitFor(() => expect((chip as HTMLButtonElement).disabled).toBe(false));
    expect(screen.getByTestId("out").textContent).toContain("Elegí asyncpg ");
    expect(screen.getByTestId("out").textContent).toContain(" por eso.");

    fireEvent.click(chip);
    expect(screen.getByTestId("memory-citation-body-abcdef12").textContent).toContain(
      "El proyecto usa asyncpg.",
    );
  });

  it("la cita desplegada enlaza a la ficha de la memoria (task_mem_20)", async () => {
    apiFetchMock.mockResolvedValue([RESOLVED]);
    mount("Elegí asyncpg [mem:abcdef12].");
    const chip = await screen.findByRole("button", { name: /mem:abcdef12/ });
    await waitFor(() => expect((chip as HTMLButtonElement).disabled).toBe(false));
    fireEvent.click(chip);
    expect(screen.getByTestId("memory-citation-open-abcdef12").getAttribute("href")).toBe(
      "/admin/memories/abcdef12-0000-0000-0000-000000000000",
    );
  });

  it("una cita que no se resuelve se enseña como desconocida, sin desplegable", async () => {
    apiFetchMock.mockResolvedValue([{ prefix: "deadbeef", status: "unknown" }]);
    mount("Según [mem:deadbeef] hice esto.");

    const chip = await screen.findByRole("button", { name: /mem:deadbeef \(desconocida\)/ });
    expect((chip as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByTestId("memory-citation-body-deadbeef")).toBeNull();
  });

  it("todas las citas del informe se resuelven en UNA petición (sin repetidas)", async () => {
    apiFetchMock.mockResolvedValue([
      RESOLVED,
      { prefix: "deadbeef", status: "unknown" },
      { prefix: "cafe0123", status: "unknown" },
    ]);
    mount("A [mem:abcdef12], B [mem:deadbeef], C [mem:cafe0123] y otra vez A [mem:abcdef12].");
    await screen.findAllByRole("button", { name: /mem:deadbeef \(desconocida\)/ });
    expect(apiFetchMock).toHaveBeenCalledTimes(1);
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/memories/resolve?prefixes=abcdef12,deadbeef,cafe0123",
    );
  });

  it("un prefijo ambiguo se pinta como ambigua, sin adivinar memoria", async () => {
    apiFetchMock.mockResolvedValue([{ prefix: "abcdef12", status: "ambiguous" }]);
    mount("Según [mem:abcdef12] hice esto.");
    const chip = await screen.findByRole("button", { name: /mem:abcdef12 \(ambigua\)/ });
    expect((chip as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByTestId("memory-citation-body-abcdef12")).toBeNull();
  });
});
