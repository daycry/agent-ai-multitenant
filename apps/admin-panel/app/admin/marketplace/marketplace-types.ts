/**
 * Tipos y ayudas del marketplace que comparten la página y sus pestañas
 * (`task_mk_23`: la pestaña de compartir se partió a `shares-tab.tsx` para que
 * `page.tsx` no rebase el límite de tamaño, y ambos leen de aquí).
 */
export interface MarketplaceListing {
  id: string;
  source_id: string;
  tenant_id: string | null;
  kind: string;
  name: string;
  version: string;
  description: string | null;
  author: string | null;
  trust_level: string;
  // ADR 0142 D6: el catálogo devuelve lo publicado MÁS lo propio en cualquier
  // estado, así que un listing del propio tenant puede llegar aquí sin estar
  // publicado. Pintarlo como uno más sería decirle a su autor que ya está en el
  // catálogo de todos cuando no lo ve nadie más que él.
  review_status: string;
  rejection_reason: string | null;
  requested_permissions: { type: string; value: unknown }[];
  is_signed: boolean;
  created_at: string;
  updated_at: string;
}

export function isPrivate(listing: MarketplaceListing): boolean {
  return listing.tenant_id !== null;
}

// ===========================================================================
// Page
// ===========================================================================
