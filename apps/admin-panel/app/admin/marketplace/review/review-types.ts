import type { BadgeVariant } from "@/components/ui/badge";

/**
 * Tipos y lógica pura de la cola de revisión (ADR 0142 D6, `task_mkt2_10`).
 *
 * Sin JSX ni hooks: lo que se puede probar sin montar un árbol de componentes
 * vive aquí. Las formas espejan `api_server/schemas/marketplace.py`.
 *
 * ## El diff de permisos, dos veces
 *
 * El backend calcula el delta autoritativo (`marketplace/listing_versions.py`),
 * pero la cola necesita enseñarlo **antes** de que exista una actualización que
 * pedirlo: el revisor compara la versión candidata con la anterior del mismo
 * listing, y eso no es una llamada de actualización, es una lectura. Así que la
 * misma aritmética vive aquí en TypeScript, con la MISMA regla de identidad (el
 * `type` manda) y la misma normalización (reordenar una lista no es un cambio).
 *
 * Que existan dos implementaciones es una decisión con coste: pueden separarse.
 * El contrapeso es que las dos están probadas contra los mismos casos —el
 * ensanche `["api.acme.com"] → ["*"]` y el reorden— y que la alternativa
 * (pedirle al backend un endpoint de diff para pintar la cola) añade una ida y
 * vuelta por fila de la lista.
 */

// ---------------------------------------------------------------------------
// Respuestas del backend
// ---------------------------------------------------------------------------

/** Los cuatro estados de `marketplace_listings.review_status`. */
export const REVIEW_STATUSES = ["draft", "pending_review", "published", "rejected"] as const;

export type ReviewStatus = (typeof REVIEW_STATUSES)[number];

export interface ReviewListing {
  id: string;
  tenant_id: string | null;
  kind: string;
  name: string;
  version: string;
  description: string | null;
  author: string | null;
  trust_level: string;
  review_status: string;
  reviewed_at: string | null;
  rejection_reason: string | null;
  manifest: Record<string, unknown>;
  requested_permissions: unknown[];
  created_at: string;
}

export interface ListingVersion {
  id: string;
  listing_id: string;
  version: string;
  changelog: string | null;
  config_schema: Record<string, unknown> | null;
  requested_permissions: unknown[];
  reviewed_by: string | null;
  reviewed_at: string | null;
  created_at: string;
}

// ---------------------------------------------------------------------------
// El diff de permisos
// ---------------------------------------------------------------------------

export interface PermissionDescriptor {
  type: string;
  value?: unknown;
}

export interface PermissionChange {
  type: string;
  from: unknown;
  to: unknown;
}

export interface PermissionDelta {
  added: PermissionDescriptor[];
  removed: PermissionDescriptor[];
  changed: PermissionChange[];
}

/** ¿Hay algo que mirar? Quitar un permiso no cuenta: no amplía nada. */
export function deltaNeedsAttention(delta: PermissionDelta): boolean {
  return delta.added.length > 0 || delta.changed.length > 0;
}

export function isEmptyDelta(delta: PermissionDelta): boolean {
  return delta.added.length === 0 && delta.removed.length === 0 && delta.changed.length === 0;
}

function indexByType(raw: unknown): Map<string, PermissionDescriptor> {
  const out = new Map<string, PermissionDescriptor>();
  if (!Array.isArray(raw)) return out;
  for (const entry of raw) {
    if (typeof entry !== "object" || entry === null) continue;
    const descriptor = entry as PermissionDescriptor;
    if (typeof descriptor.type !== "string" || descriptor.type.length === 0) continue;
    // Un tipo repetido colapsa al último — la misma regla que `consent.py`.
    out.set(descriptor.type, descriptor);
  }
  return out;
}

/**
 * Forma comparable de un `value`: las listas ordenadas, lo demás serializado.
 *
 * Sin ordenar, cualquier re-serialización del manifest levantaría un falso
 * «cambió el permiso», y los avisos falsos se acaban ignorando — que es la
 * forma barata de perder el mecanismo entero.
 */
function normalize(value: unknown): string {
  if (Array.isArray(value)) {
    return JSON.stringify([...value].map((v) => JSON.stringify(v)).sort());
  }
  return JSON.stringify(value ?? null);
}

/**
 * El delta entre lo que pedía la versión anterior y lo que pide la candidata.
 *
 * Espeja `marketplace/listing_versions.py::permission_diff`. Con `previous`
 * ausente (la primera versión de un listing) todo es `added`: es la verdad —
 * nadie había consentido nada antes.
 */
export function permissionDelta(
  previous: unknown[] | undefined | null,
  candidate: unknown[] | undefined | null,
): PermissionDelta {
  const before = indexByType(previous);
  const after = indexByType(candidate);

  const added: PermissionDescriptor[] = [];
  const removed: PermissionDescriptor[] = [];
  const changed: PermissionChange[] = [];

  for (const [type, descriptor] of [...after.entries()].sort()) {
    if (!before.has(type)) {
      added.push(descriptor);
      continue;
    }
    const from = before.get(type)?.value;
    if (normalize(from) !== normalize(descriptor.value)) {
      changed.push({ type, from, to: descriptor.value });
    }
  }
  for (const [type, descriptor] of [...before.entries()].sort()) {
    if (!after.has(type)) removed.push(descriptor);
  }

  return { added, removed, changed };
}

/**
 * La versión ANTERIOR a `current` en el histórico, o `undefined` si es la primera.
 *
 * El histórico llega ordenado por fecha descendente; «anterior» es la primera
 * fila distinta de la actual. Se elige por posición y no por semver a propósito:
 * lo que el revisor compara es «lo que había publicado» contra «lo que llega»,
 * y un semver reescrito a mano no debe cambiar esa lectura.
 */
export function previousVersion(
  versions: ListingVersion[],
  currentVersion: string,
): ListingVersion | undefined {
  return versions.find((v) => v.version !== currentVersion);
}

/**
 * ¿Puede el admin actuar sobre este listing, y cómo?
 *
 * Espeja `REVIEW_TRANSITIONS` del backend. Duplicarlo aquí evita pintar botones
 * que el backend va a rechazar con un 409, que es la peor UI posible: la que
 * ofrece algo y luego dice que no.
 */
export function availableActions(status: string): {
  canApprove: boolean;
  canReject: boolean;
  canPromote: boolean;
} {
  return {
    canApprove: status === "pending_review",
    canReject: status === "pending_review",
    canPromote: status === "published",
  };
}

/**
 * El color del estado en la cola de revisión.
 *
 * En una cola, «rechazado» y «publicado» no pueden leerse igual de un vistazo:
 * el color es lo que se ve antes que el texto. `default` para lo desconocido en
 * vez de reventar, porque un estado nuevo en el backend no debe dejar la cola
 * en blanco.
 */
export function reviewStatusVariant(status: string): BadgeVariant {
  switch (status) {
    case "published":
      return "success";
    case "pending_review":
      return "warning";
    case "rejected":
      return "danger";
    case "draft":
      return "muted";
    default:
      return "default";
  }
}
