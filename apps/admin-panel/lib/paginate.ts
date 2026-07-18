/**
 * Paginación exhaustiva contra la API (PROY2-08).
 *
 * Los boards llamaban a los listados sin `limit`/`offset` y el backend
 * aplicaba DEFAULT_PAGE_SIZE=100 truncando EN SILENCIO: un plan de 200 tareas
 * pintaba 100, >100 planes desaparecían del kanban gerencial. `fetchAllPages`
 * agota las páginas hasta la primera incompleta y, si toca el tope de
 * seguridad (`maxPages`), lo dice (`truncated: true`) para que la UI muestre
 * el aviso en vez de callar.
 */

import { apiFetch } from "@/lib/api";

export interface PaginatedResult<T> {
  items: T[];
  /** true = se alcanzó el tope de seguridad y PUEDE haber más filas. */
  truncated: boolean;
}

export interface FetchAllPagesOptions<T> {
  /** Inyectable para tests; por defecto `apiFetch`. */
  fetcher?: (path: string) => Promise<T[]>;
  pageSize?: number;
  /** Tope de seguridad contra listados patológicos (default 20 → 2000 filas). */
  maxPages?: number;
}

export async function fetchAllPages<T>(
  path: string,
  options: FetchAllPagesOptions<T> = {},
): Promise<PaginatedResult<T>> {
  const fetcher = options.fetcher ?? ((p: string) => apiFetch<T[]>(p));
  const pageSize = options.pageSize ?? 100;
  const maxPages = options.maxPages ?? 20;
  const sep = path.includes("?") ? "&" : "?";

  const items: T[] = [];
  for (let page = 0; page < maxPages; page++) {
    const chunk = await fetcher(`${path}${sep}limit=${pageSize}&offset=${page * pageSize}`);
    items.push(...chunk);
    if (chunk.length < pageSize) {
      return { items, truncated: false };
    }
  }
  return { items, truncated: true };
}
