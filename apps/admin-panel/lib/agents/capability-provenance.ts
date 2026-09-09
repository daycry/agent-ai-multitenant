/**
 * De dónde viene una capacidad (tool o skill) asignada a un agente.
 *
 * `task_mk_13` (UI-04): las filas materializadas desde el marketplace (ADR 0100)
 * llevan `source_installation_id` + el nombre del listing y la versión que la
 * API resuelve (`marketplace_provenance` en `routers/agents/common.py`). La ficha
 * del agente lo enseña como insignia para que «la tiene» venga con «de dónde».
 */
export interface CapabilityProvenance extends Record<string, string> {
  listing: string;
  version: string;
}

export interface ProvenanceFields {
  source_installation_id?: string | null;
  source_listing_name?: string | null;
  source_version?: string | null;
}

/** `null` para una fila nativa del tenant (sin instalación detrás). */
export function provenanceOf(row: ProvenanceFields): CapabilityProvenance | null {
  if (!row.source_installation_id) return null;
  return { listing: row.source_listing_name ?? "—", version: row.source_version ?? "?" };
}

/** Índice `id → procedencia` sólo con las filas que vienen del marketplace. */
export function provenanceIndex<T extends ProvenanceFields>(
  rows: readonly T[] | undefined,
  idOf: (row: T) => string,
): Map<string, CapabilityProvenance> {
  const out = new Map<string, CapabilityProvenance>();
  for (const row of rows ?? []) {
    const prov = provenanceOf(row);
    if (prov) out.set(idOf(row), prov);
  }
  return out;
}
