"use client";

/**
 * Lo que el AUTOR de un listing ve sobre su revisión — `task_mkt2_10`.
 *
 * ## El problema que arregla
 *
 * Publicar un listing privado deja la fila en `pending_review` (ADR 0142 D6:
 * «nada entra al catálogo sin ojos»), y la UI contestaba «Listing publicado. Ya
 * aparece en tu catálogo privado». Las dos frases eran falsas a la vez.
 *
 * Y hay una consecuencia que la palabra «publicado» tapaba del todo: la
 * cláusula de visibilidad del catálogo es `published OR propio`
 * (`marketplace/review.py::catalog_visibility_clause`), así que mientras espera
 * revisión **no lo ve nadie más que su tenant autor** — ni siquiera el tenant
 * con el que se comparta mediante un grant. Quien publicaba y compartía se
 * quedaba esperando a que el otro lo instalase, sin nada en pantalla que
 * explicara por qué no aparecía.
 *
 * ## Las dos piezas
 *
 * - `ReviewStatusBadge` — el estado REAL, con el mismo texto y el mismo color
 *   que la cola del System Admin (`components/marketplace/review-status.ts`).
 * - `ReviewStatusNote` — lo que ese estado implica: quién decide, qué pasa
 *   mientras tanto y, si fue un rechazo, el motivo con el que se corrige. Un
 *   estado sin su consecuencia sigue dejando al autor adivinando.
 */

import { Badge } from "@/components/ui/badge";
import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";

import { formatQueuedSince, reviewStatusKey, reviewStatusVariant } from "./review-status";

/** Lo que estas piezas necesitan de un listing (espeja `MarketplaceListingResponse`). */
export interface ReviewedListing {
  review_status: string;
  rejection_reason?: string | null;
  /** Cuándo cambió por última vez, que para un `pending_review` es cuándo entró en cola. */
  updated_at?: string;
}

/** El estado de revisión, con el color y el texto de la cola del admin. */
export function ReviewStatusBadge({ status, testId }: { status: string; testId?: string }) {
  const t = useT("marketplaceReview");
  const key = reviewStatusKey(status);

  return (
    <Badge variant={reviewStatusVariant(status)} data-testid={testId ?? "review-status-badge"}>
      {/* Un estado que este panel no conoce se pinta crudo: es más informativo
          que una etiqueta vacía y hace evidente que hay que añadirlo. */}
      {key ? t(key) : status}
    </Badge>
  );
}

/**
 * Qué significa ese estado para quien publicó.
 *
 * Sólo habla cuando hay algo que decir: un listing `published` no necesita
 * explicación —está donde el autor cree que está— y una nota permanente
 * enseñaría a no leer las notas.
 */
export function ReviewStatusNote({
  listing,
  testId,
}: {
  listing: ReviewedListing;
  testId?: string;
}) {
  const t = useT("marketplaceReview");
  const lang = useLangOptional();

  if (listing.review_status === "pending_review") {
    return (
      <div
        className="text-muted-foreground mt-1 space-y-0.5 text-xs"
        data-testid={testId ?? "review-status-note"}
      >
        <p>{t("queuedWho")}</p>
        <p>{t("queuedMeanwhile")}</p>
        {listing.updated_at ? (
          <p data-testid="review-status-since">
            {t("pendingSince", { date: formatQueuedSince(listing.updated_at, lang) })}
          </p>
        ) : null}
      </div>
    );
  }

  if (listing.review_status === "rejected") {
    return (
      <div
        className="border-danger/40 bg-danger/10 mt-2 space-y-1 rounded-md border p-2"
        data-testid={testId ?? "review-status-note"}
      >
        <p className="text-xs font-semibold">{t("rejectionReason")}</p>
        {/* El motivo es lo único con lo que se corrige un rechazo. Si el
            backend lo devolviese vacío se dice, en vez de dejar el hueco: un
            rechazo mudo es indistinguible de un borrado. */}
        <p className="break-words text-xs" data-testid="review-status-reason">
          {listing.rejection_reason?.trim() ? listing.rejection_reason : t("rejectionMissing")}
        </p>
        <p className="text-muted-foreground text-xs">{t("rejectedFix")}</p>
      </div>
    );
  }

  return null;
}
