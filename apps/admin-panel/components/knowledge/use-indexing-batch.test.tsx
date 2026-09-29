// @vitest-environment jsdom

/**
 * `task_ui_21` (gap 2 de la revisión) — el sondeo del lote del asistente: acaba con
 * `indexed_empty`, con un 404 (documento borrado), con el diálogo cerrado y con
 * un tope de duración; y sondea los ids del lote, no la página entera.
 */

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string) => apiFetchMock(path) };
});

import { ApiError } from "@/lib/api";

/** Simula `GET …/documents?ids=…`: devuelve de `store` sólo los ids pedidos. */
function listOf(store: Record<string, unknown>) {
  return (path: string) => {
    const ids = new URL(path, "http://x").searchParams.getAll("ids");
    return Promise.resolve(ids.filter((i) => i in store).map((i) => store[i]));
  };
}

import { useIndexingBatch } from "./use-indexing-batch";

const doc = (id: string, status: string) => ({ id, title: id, status, error_message: null });
const FAST = { pollMs: 10, maxMs: 300 };

afterEach(() => apiFetchMock.mockReset());

describe("useIndexingBatch", () => {
  it("indexed_empty es terminal: deja de sondear", async () => {
    apiFetchMock.mockImplementation(listOf({ a: doc("a", "indexed_empty") }));
    const { result } = renderHook(() =>
      useIndexingBatch({ kbId: "kb", docIds: ["a"], enabled: true, ...FAST }),
    );
    await waitFor(() => expect(result.current.docs.a?.status).toBe("indexed_empty"));
    const calls = apiFetchMock.mock.calls.length;
    await new Promise((r) => setTimeout(r, 80));
    expect(apiFetchMock.mock.calls.length).toBe(calls);
    expect(apiFetchMock.mock.calls[0][0]).toMatch(/^\/knowledge-bases\/kb\/documents\?/);
  });

  it("un id que ya no vuelve en la respuesta es terminal (borrado) y no bloquea a los demás", async () => {
    apiFetchMock.mockImplementation(listOf({ b: doc("b", "indexed") }));
    const { result } = renderHook(() =>
      useIndexingBatch({ kbId: "kb", docIds: ["a", "b"], enabled: true, ...FAST }),
    );
    await waitFor(() => expect(result.current.docs.a).toBeNull());
    await waitFor(() => expect(result.current.docs.b?.status).toBe("indexed"));
    expect(result.current.timedOut).toBe(false);
  });

  it("hace UNA petición por ciclo con todos los pendientes, y sólo los no terminales", async () => {
    let n = 0;
    apiFetchMock.mockImplementation((path: string) => {
      n += 1;
      const ids = new URL(path, "http://x").searchParams.getAll("ids");
      return Promise.resolve(
        ids.map((i) => doc(i, i === "slow" && n < 3 ? "processing" : "indexed")),
      );
    });
    const { result } = renderHook(() =>
      useIndexingBatch({ kbId: "kb", docIds: ["a", "b", "slow"], enabled: true, ...FAST }),
    );
    await waitFor(() => expect(result.current.docs.slow?.status).toBe("indexed"));
    const first = new URL(apiFetchMock.mock.calls[0][0], "http://x").searchParams.getAll("ids");
    expect(first).toEqual(["a", "b", "slow"]);
    for (const [path] of apiFetchMock.mock.calls.slice(1)) {
      expect(new URL(path, "http://x").searchParams.getAll("ids")).toEqual(["slow"]);
    }
    expect(apiFetchMock.mock.calls.length).toBe(3);
  });

  it("un error transitorio se reintenta sin marcar el documento como borrado", async () => {
    let n = 0;
    apiFetchMock.mockImplementation((path: string) => {
      n += 1;
      if (n === 1) return Promise.reject(new ApiError(500, "boom"));
      return listOf({ a: doc("a", "indexed") })(path);
    });
    const { result } = renderHook(() =>
      useIndexingBatch({ kbId: "kb", docIds: ["a"], enabled: true, ...FAST }),
    );
    await waitFor(() => expect(result.current.docs.a?.status).toBe("indexed"));
  });

  it("no sondea con el diálogo cerrado", async () => {
    apiFetchMock.mockImplementation(listOf({ a: doc("a", "processing") }));
    renderHook(() => useIndexingBatch({ kbId: "kb", docIds: ["a"], enabled: false, ...FAST }));
    await new Promise((r) => setTimeout(r, 60));
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it("tiene un tope de duración y lo declara", async () => {
    apiFetchMock.mockImplementation(listOf({ a: doc("a", "processing") }));
    const { result } = renderHook(() =>
      useIndexingBatch({ kbId: "kb", docIds: ["a"], enabled: true, pollMs: 10, maxMs: 60 }),
    );
    await waitFor(() => expect(result.current.timedOut).toBe(true));
    const calls = apiFetchMock.mock.calls.length;
    await new Promise((r) => setTimeout(r, 60));
    expect(apiFetchMock.mock.calls.length).toBe(calls);
  });
});
