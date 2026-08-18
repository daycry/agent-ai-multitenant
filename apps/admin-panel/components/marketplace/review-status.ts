import type { BadgeVariant } from "@/components/ui/badge";
import type { Lang } from "@/lib/i18n";

/**
 * El vocabulario de `marketplace_listings.review_status`, en un solo sitio.
 *
 * ## Por qué se sacó de la cola de revisión
 *
 * Los cuatro estados los pintan ahora DOS públicos distintos: el System Admin
 * en su cola (`app/admin/marketplace/review/`) y el AUTOR en su marketplace
 * privado, que es donde `task_mkt2_10` pedía que la UI dejara de decir
 * «publicado» a algo que está esperando revisión.
 *
 * Que los dos vean lo mismo no es cosmética: si la cola llama «Pendiente de
 * revisión» a lo que la pantalla del autor llama «En espera», el autor no puede
 * relacionar lo que lee con lo que le dicen que hay que hacer. Y si el color
 * discrepa —rechazado en rojo aquí y en gris allá— la lectura de un vistazo,
 * que es la que se hace de verdad, dice cosas distintas.
 *
 * Sin JSX ni hooks: el badge y las notas viven en `review-status-badge.tsx`.
 */

/** La clave del diccionario (`marketplaceReview`) que nombra cada estado. */
const STATUS_KEY = {
  pending_review: "statusPendingReview",
  published: "statusPublished",
  rejected: "statusRejected",
  draft: "statusDraft",
} as const;

export type ReviewStatusKey = (typeof STATUS_KEY)[keyof typeof STATUS_KEY];

/**
 * La clave del texto de un estado, o `null` si el backend manda uno que esta
 * versión del panel no conoce.
 *
 * Devolver `null` en vez de reventar es deliberado: un estado nuevo en el
 * backend debe pintarse crudo (el llamante enseña el valor tal cual), no dejar
 * la pantalla en blanco.
 */
export function reviewStatusKey(status: string): ReviewStatusKey | null {
  return (STATUS_KEY as Record<string, ReviewStatusKey | undefined>)[status] ?? null;
}

/**
 * El color de un estado de revisión.
 *
 * En una lista, «rechazado» y «publicado» no pueden leerse igual de un vistazo:
 * el color es lo que se ve antes que el texto. `default` para lo desconocido en
 * vez de reventar, porque un estado nuevo en el backend no debe dejar la
 * pantalla en blanco.
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

/**
 * La fecha en que algo entró en la cola, legible.
 *
 * Existe porque «pendiente de revisión» sin ninguna referencia temporal es la
 * mitad de la verdad: el autor no sabe si lo mandó ayer o hace tres semanas, y
 * ésa es justo la pista que necesita para decidir si preguntar. No se inventa
 * una previsión de cuándo saldrá —eso el panel no lo sabe— , se dice desde
 * cuándo espera.
 *
 * Una fecha ilegible se devuelve tal cual: enseñar el valor crudo es mejor
 * pista de un bug que un «Invalid Date».
 */
export function formatQueuedSince(iso: string, lang: Lang): string {
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return iso;
  return parsed.toLocaleDateString(lang, { year: "numeric", month: "long", day: "numeric" });
}
