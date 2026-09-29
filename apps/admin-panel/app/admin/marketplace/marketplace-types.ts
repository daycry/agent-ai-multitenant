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

/** `GET /marketplace/installations` — espeja `MarketplaceInstallationResponse`. */
export interface InstallationDeployment {
  /** Id del despliegue (un proyecto puede tener uno `disabled` y otro `active`). */
  id: string;
  project_id: string;
  project_name: string;
  status: string;
}

export interface MarketplaceInstallation {
  id: string;
  tenant_id: string;
  listing_id: string;
  project_id: string | null;
  version: string;
  status: string;
  granted_permissions: unknown[];
  denied_permissions: unknown[];
  installed_by: string | null;
  installed_at: string;
  revoked_at: string | null;
  revoked_by: string | null;
  created_at: string;
  updated_at: string;
  // task_mk_10 (ADR 0081 reabierto): por dónde llega la capacidad —
  // `catalog_row` | `on_deploy` | `deferred`. Ausente en respuestas antiguas.
  capability?: string | null;
  capability_reason?: string | null;
  // task_mk_23 (UI-06): nombre y tipo del listing, para no enseñar el UUID.
  listing_name?: string | null;
  listing_kind?: string | null;
  // `task_ui_32`: proyectos con despliegue vivo (nunca los retirados). Ausente en
  // respuestas antiguas.
  deployments?: InstallationDeployment[];
}

export function isPrivate(listing: MarketplaceListing): boolean {
  return listing.tenant_id !== null;
}

// ===========================================================================
// Page
// ===========================================================================
