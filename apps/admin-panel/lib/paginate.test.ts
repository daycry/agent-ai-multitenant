import { describe, expect, it } from "vitest";

import { fetchAllPages } from "@/lib/paginate";

// PROY2-08: los boards llamaban al backend sin limit/offset y el
// DEFAULT_PAGE_SIZE=100 truncaba en silencio (un plan de 200 tareas pintaba
// 100). fetchAllPages agota las páginas y avisa si tocó el tope de seguridad.

function pagedFetcher(total: number): (path: string) => Promise<number[]> {
  const rows = Array.from({ length: total }, (_, i) => i);
  return async (path: string) => {
    const url = new URL(`http://x${path}`);
    const limit = Number(url.searchParams.get("limit"));
    const offset = Number(url.searchParams.get("offset"));
    return rows.slice(offset, offset + limit);
  };
}

describe("fetchAllPages", () => {
  it("una sola página corta (< pageSize) llega entera sin truncar", async () => {
    const result = await fetchAllPages<number>("/plans", {
      fetcher: pagedFetcher(7),
      pageSize: 100,
    });
    expect(result.items).toHaveLength(7);
    expect(result.truncated).toBe(false);
  });

  it("agota varias páginas (>100 filas ya no se pierden)", async () => {
    const result = await fetchAllPages<number>("/plans", {
      fetcher: pagedFetcher(230),
      pageSize: 100,
    });
    expect(result.items).toHaveLength(230);
    expect(result.items[229]).toBe(229);
    expect(result.truncated).toBe(false);
  });

  it("respeta query strings preexistentes (usa & no ?)", async () => {
    const seen: string[] = [];
    const fetcher = async (path: string) => {
      seen.push(path);
      return [] as number[];
    };
    await fetchAllPages<number>("/projects/p/tasks?plan_id=x", { fetcher, pageSize: 50 });
    expect(seen[0]).toBe("/projects/p/tasks?plan_id=x&limit=50&offset=0");
  });

  it("marca truncated al tocar el tope de seguridad", async () => {
    const result = await fetchAllPages<number>("/plans", {
      fetcher: pagedFetcher(1000),
      pageSize: 100,
      maxPages: 3,
    });
    expect(result.items).toHaveLength(300);
    expect(result.truncated).toBe(true);
  });

  it("una página exactamente llena pide la siguiente (vacía) y no trunca", async () => {
    const result = await fetchAllPages<number>("/plans", {
      fetcher: pagedFetcher(100),
      pageSize: 100,
    });
    expect(result.items).toHaveLength(100);
    expect(result.truncated).toBe(false);
  });
});
