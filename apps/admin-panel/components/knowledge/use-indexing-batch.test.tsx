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

import { useIndexingBatch } from "./use-indexing-batch";

const doc = (id: string, status: string) => ({ id, title: id, status, error_message: null });
const FAST = { pollMs: 10, maxMs: 300 };

afterEach(() => apiFetchMock.mockReset());

describe("useIndexingBatch", () => {
  it("indexed_empty es terminal: deja de sondear", async () => {
    apiFetchMock.mockResolvedValue(doc("a", "indexed_empty"));
    const { result } = renderHook(() =>
      useIndexingBatch({ kbId: "kb", docIds: ["a"], enabled: true, ...FAST }),
    );
    await waitFor(() => expect(result.current.docs.a?.status).toBe("indexed_empty"));
    const calls = apiFetchMock.mock.calls.length;
    await new Promise((r) => setTimeout(r, 80));
    expect(apiFetchMock.mock.calls.length).toBe(calls);
    expect(apiFetchMock).toHaveBeenCalledWith("/knowledge-bases/kb/documents/a");
  });

  it("un 404 es terminal (documento borrado) y no bloquea a los demás", async () => {
    apiFetchMock.mockImplementation((path: string) =>
      path.endsWith("/a")
        ? Promise.reject(new ApiError(404, "gone"))
        : Promise.resolve(doc("b", "indexed")),
    );
    const { result } = renderHook(() =>
      useIndexingBatch({ kbId: "kb", docIds: ["a", "b"], enabled: true, ...FAST }),
    );
    await waitFor(() => expect(result.current.docs.a).toBeNull());
    await waitFor(() => expect(result.current.docs.b?.status).toBe("indexed"));
    expect(result.current.timedOut).toBe(false);
  });

  it("no sondea con el diálogo cerrado", async () => {
    apiFetchMock.mockResolvedValue(doc("a", "processing"));
    renderHook(() => useIndexingBatch({ kbId: "kb", docIds: ["a"], enabled: false, ...FAST }));
    await new Promise((r) => setTimeout(r, 60));
    expect(apiFetchMock).not.toHaveBeenCalled();
  });

  it("tiene un tope de duración y lo declara", async () => {
    apiFetchMock.mockResolvedValue(doc("a", "processing"));
    const { result } = renderHook(() =>
      useIndexingBatch({ kbId: "kb", docIds: ["a"], enabled: true, pollMs: 10, maxMs: 60 }),
    );
    await waitFor(() => expect(result.current.timedOut).toBe(true));
    const calls = apiFetchMock.mock.calls.length;
    await new Promise((r) => setTimeout(r, 60));
    expect(apiFetchMock.mock.calls.length).toBe(calls);
  });
});
