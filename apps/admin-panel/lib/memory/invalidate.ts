import type { QueryClient } from "@tanstack/react-query";

/**
 * Invalida sólo lo que un cambio en una memoria puede haber movido: el listado, los contadores,
 * la búsqueda y (si se sabe cuál) la ficha de ESA memoria. No toca la cola de compactación ni
 * las fichas de las demás: invalidar `["memories"]` a secas recargaba todo eso a cada voto.
 */
export function invalidateMemoryViews(queryClient: QueryClient, memoryId?: string): void {
  for (const scope of ["list", "counts", "search"]) {
    void queryClient.invalidateQueries({ queryKey: ["memories", scope] });
  }
  if (memoryId) {
    void queryClient.invalidateQueries({ queryKey: ["memories", "card", memoryId] });
  }
}
