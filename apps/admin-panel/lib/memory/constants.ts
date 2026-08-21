/**
 * Opciones canónicas de `memory_scope` (ADR 0055/0071), compartidas por la ficha
 * del agente y la del equipo. Espejan el enum `MemoryScope` del backend.
 *
 * i18n (prod-16 `task_prod16_03`): la opción guarda su CLAVE del diccionario, no
 * su etiqueta. Antes traía el texto castellano cableado, y como este módulo es
 * puro (no puede llamar a un hook) las dos pantallas que lo consumen —ya
 * migradas por lo demás— pintaban «Privada» y «Compartida con equipo» con el
 * toggle en EN. Con la clave, cada consumidor resuelve el texto con el idioma
 * activo (`useT("memoryScope")` desde un componente, `translate` desde aquí).
 */

import { translate, type Lang, type MessageKey } from "@/lib/i18n";

/** La clave de `memoryScope` que nombra cada opción. */
export type MemoryScopeKey = MessageKey<"memoryScope">;

export const MEMORY_SCOPE_OPTIONS = [
  { value: "private", key: "private" },
  { value: "team_shared", key: "teamShared" },
  { value: "project_shared", key: "projectShared" },
  { value: "global", key: "global" },
] as const satisfies readonly { value: string; key: MemoryScopeKey }[];

/**
 * Etiqueta legible de un scope en el idioma pedido.
 *
 * El fallback al valor crudo es deliberado: si el backend añadiera un scope que
 * este panel aún no conoce, enseñar `some_new_scope` es mejor pista para quien lo
 * lea que un hueco vacío o un «—» que se confunde con «sin política».
 */
export function memoryScopeLabel(value: string | null | undefined, lang: Lang): string {
  const option = MEMORY_SCOPE_OPTIONS.find((o) => o.value === value);
  if (option) return translate(lang, "memoryScope", option.key);
  return value ?? "—";
}
