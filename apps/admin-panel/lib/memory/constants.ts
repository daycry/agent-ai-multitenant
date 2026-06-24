/**
 * Opciones canónicas de `memory_scope` (ADR 0055/0071), compartidas por la ficha
 * del agente y la del equipo. Espejan el enum `MemoryScope` del backend.
 */
export const MEMORY_SCOPE_OPTIONS = [
  { value: "private", label: "Privada" },
  { value: "team_shared", label: "Compartida con equipo" },
  { value: "project_shared", label: "Compartida con proyecto" },
  { value: "global", label: "Global del tenant" },
] as const;

/** Etiqueta legible de un scope, con fallback al propio valor. */
export function memoryScopeLabel(value: string | null | undefined): string {
  return MEMORY_SCOPE_OPTIONS.find((o) => o.value === value)?.label ?? value ?? "—";
}
