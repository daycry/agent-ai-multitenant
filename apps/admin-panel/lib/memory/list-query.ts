/**
 * Ruta de `GET /memories` para cada vista de `/admin/memories` (`task_mem_20`).
 *
 * Una pestaña es un ámbito (`scope`) o la vista «Disputadas» (`disputed=true`, sea cual sea el
 * ámbito). «Nunca recuperada» es `recall_count = 0`, o sea `max_recall_count=0`, y se combina
 * con cualquiera de las dos.
 */
import type { MemoryScope } from "@/components/memories/types";

export type MemoryView = MemoryScope | "all" | "disputed";

export function memoryListPath(opts: {
  view: MemoryView;
  neverRecalled: boolean;
  offset: number;
}): string {
  const qs: string[] = [];
  if (opts.view === "disputed") qs.push("disputed=true");
  else if (opts.view !== "all") qs.push(`scope=${opts.view}`);
  if (opts.neverRecalled) qs.push("max_recall_count=0");
  if (opts.offset > 0) qs.push(`offset=${opts.offset}`);
  return `/memories${qs.length ? `?${qs.join("&")}` : ""}`;
}
