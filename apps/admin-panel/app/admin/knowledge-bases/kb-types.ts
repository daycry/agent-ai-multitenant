// Tipos, constantes y agrupación pura de Knowledge Bases (tramo #9, extracción
// verbatim del monolito page.tsx — auditoría 2026-07-10). La agrupación por
// categoría (built-ins primero, «Sin categoría» al final) es un helper puro sin
// JSX ni hooks; la gestión de categorías vive en /knowledge-bases/categories.

interface KbCategorySummary {
  id: string;
  slug: string;
  name: string;
  color: string | null;
  is_builtin: boolean;
}

export interface KbCategory extends KbCategorySummary {
  tenant_id: string | null;
}

export interface KnowledgeBase {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  embedding_model_id: string;
  created_by: string | null;
  created_at: string;
  updated_at: string;
  is_builtin: boolean;
  category: KbCategorySummary | null;
}

export const DEFAULT_EMBEDDING_MODEL = "nomic-embed-text-v1.5";
const NO_CATEGORY_KEY = "__none__";

// ---------------------------------------------------------------------------
// Grouping helper
// ---------------------------------------------------------------------------

interface KbGroup {
  key: string;
  label: string;
  category: KbCategorySummary | null;
  kbs: KnowledgeBase[];
}

/**
 * Agrupa las KBs por categoría.
 *
 * `uncategorizedLabel` llega por parámetro y no se resuelve aquí dentro (prod-16
 * `task_prod16_04`): era el único texto de UI de este módulo, y hacer que la
 * función pura dependiera del idioma la habría acoplado al diccionario sólo por
 * una cadena. El llamante ya tiene `useT("knowledgeBases")` a mano.
 */
export function groupByCategory(
  kbs: KnowledgeBase[],
  categories: KbCategory[],
  uncategorizedLabel: string,
): KbGroup[] {
  const byId = new Map<string, KbGroup>();
  const sinCat: KbGroup = {
    key: NO_CATEGORY_KEY,
    label: uncategorizedLabel,
    category: null,
    kbs: [],
  };
  // Pre-cargar grupos para todas las categorías visibles, así aparecen
  // como secciones vacías si el usuario las creó pero aún no asignó
  // ninguna KB (UX más predecible).
  for (const c of categories) {
    byId.set(c.id, { key: c.id, label: c.name, category: c, kbs: [] });
  }
  for (const kb of kbs) {
    if (kb.category) {
      const g = byId.get(kb.category.id);
      if (g) g.kbs.push(kb);
      else
        // Categoría borrada en otro tab; mostramos KB en "Sin categoría".
        sinCat.kbs.push(kb);
    } else {
      sinCat.kbs.push(kb);
    }
  }
  // Orden: built-ins primero (por nombre), después custom (por nombre),
  // "Sin categoría" al final.
  const out: KbGroup[] = [];
  const groups = Array.from(byId.values()).filter((g) => g.kbs.length > 0);
  groups.sort((a, b) => {
    const aBuiltin = a.category?.is_builtin ? 0 : 1;
    const bBuiltin = b.category?.is_builtin ? 0 : 1;
    if (aBuiltin !== bBuiltin) return aBuiltin - bBuiltin;
    return a.label.localeCompare(b.label);
  });
  out.push(...groups);
  if (sinCat.kbs.length > 0) out.push(sinCat);
  return out;
}
