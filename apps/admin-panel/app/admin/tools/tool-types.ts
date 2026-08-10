/**
 * Tipos y constantes compartidos del catálogo de tools (`/admin/tools`).
 *
 * Salieron del `page.tsx` al trocearlo (prod-16 `task_prod16_08`): las tres
 * piezas del módulo los necesitan, y dejarlos en la pantalla obligaba a
 * importarlos desde `page.tsx`, que es el import que nadie quiere ver.
 */

import type { ImplementationType, SecurityLevel, ToolCategory } from "@/lib/tools/taxonomy";

/** Espeja `api_server.schemas.catalog.ToolResponse`. */
export interface CatalogTool {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  category: string;
  implementation_type: string;
  implementation_ref: string | null;
  security_level: string;
  is_builtin: boolean;
  is_runtime_wired: boolean;
}

export interface ToolFormValue {
  name: string;
  description: string;
  category: ToolCategory;
  implementation_type: ImplementationType;
  implementation_ref: string;
  security_level: SecurityLevel;
}

export const EMPTY_FORM: ToolFormValue = {
  name: "",
  description: "",
  category: "custom",
  implementation_type: "http_endpoint",
  security_level: "safe",
  implementation_ref: "",
};

/** Centinela «todas» de un filtro por faceta (no acota nada). */
export const ALL = "__all__";

/** Lo mínimo de un término de taxonomía que esta pantalla necesita pintar. */
export interface BilingualTermLike {
  labelEs: string;
  labelEn: string;
}
