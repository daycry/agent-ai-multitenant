"use client";

/**
 * Textos de la cola de revisión (ADR 0142 D6, `task_mkt2_10`).
 *
 * ## Por qué un diccionario local y no `lib/i18n/dictionary.ts`
 *
 * El diccionario global es un fichero **compartido** que varias tandas de
 * trabajo tocan a la vez, y las tres puertas de despliegue (fase 2 de este
 * mismo plan) están escribiendo en él ahora mismo. Un namespace nuevo ahí es un
 * conflicto de merge garantizado por un beneficio nulo: nadie fuera de esta
 * carpeta usa estos textos.
 *
 * Usa las MISMAS primitivas (`Lang`, `Translation`, `interpolate`,
 * `useLangOptional`), así que el contrato no se bifurca: los dos idiomas siguen
 * siendo obligatorios en cada clave —el tipo `Translation` lo exige— y el
 * selector ES/EN del header lo gobierna igual. Si algún día estos textos hacen
 * falta fuera, mudarlos al diccionario global es mover el objeto.
 */

import { useCallback } from "react";

import {
  dictionary,
  interpolate,
  type Lang,
  type Translation,
  type TranslationVars,
} from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";

/**
 * Los cuatro estados NO se escriben aquí: se toman del diccionario global.
 *
 * Desde `task_mkt2_10` hay dos públicos leyendo los mismos valores —el System
 * Admin en esta cola y el autor en su marketplace privado—, y una copia local
 * de estos textos es el camino conocido para que la cola diga «Pendiente de
 * revisión» donde la otra pantalla dice otra cosa. El resto de este diccionario
 * sigue siendo local porque nadie fuera de esta carpeta lo usa.
 */
const sharedStatus = dictionary.marketplaceReview;

const messages = {
  title: { es: "Cola de revisión", en: "Review queue" },
  subtitle: {
    es: "Nada entra al catálogo sin que alguien lo mire.",
    en: "Nothing reaches the catalog without someone looking at it.",
  },
  back: { es: "Volver al marketplace", en: "Back to the marketplace" },
  empty: {
    es: "No hay nada esperando revisión.",
    en: "Nothing is waiting for review.",
  },
  loading: { es: "Cargando…", en: "Loading…" },
  filterLabel: { es: "Estado", en: "Status" },
  statusPendingReview: sharedStatus.statusPendingReview,
  statusPublished: sharedStatus.statusPublished,
  statusRejected: sharedStatus.statusRejected,
  statusDraft: sharedStatus.statusDraft,
  ownerGlobal: { es: "Catálogo oficial", en: "Official catalog" },
  ownerTenant: { es: "Tenant {tenant}", en: "Tenant {tenant}" },
  approve: { es: "Aprobar", en: "Approve" },
  approveAndPromote: { es: "Aprobar y verificar", en: "Approve and verify" },
  reject: { es: "Rechazar", en: "Reject" },
  promote: { es: "Marcar como verificado", en: "Mark as verified" },
  demote: { es: "Bajar a community", en: "Demote to community" },
  rejectReasonLabel: {
    es: "Motivo del rechazo (obligatorio)",
    en: "Rejection reason (required)",
  },
  rejectReasonHelp: {
    es: "El autor lee esto para saber qué corregir. Un rechazo sin motivo no se puede recurrir.",
    en: "The author reads this to know what to fix. A rejection with no reason cannot be appealed.",
  },
  rejectReasonMissing: {
    es: "Escribe el motivo antes de rechazar.",
    en: "Write the reason before rejecting.",
  },
  rejectConfirm: { es: "Confirmar rechazo", en: "Confirm rejection" },
  cancel: { es: "Cancelar", en: "Cancel" },
  manifest: { es: "Manifest de esta versión", en: "This version's manifest" },
  permissionsTitle: { es: "Permisos que pide", en: "Permissions requested" },
  permissionsNone: { es: "No pide ningún permiso.", en: "It requests no permissions." },
  diffTitle: { es: "Cambios frente a {version}", en: "Changes against {version}" },
  diffFirstVersion: {
    es: "Primera versión: no hay nada con qué compararla.",
    en: "First version: there is nothing to compare it against.",
  },
  diffNone: {
    es: "Los permisos no cambian respecto a la versión anterior.",
    en: "Permissions are unchanged from the previous version.",
  },
  diffAdded: { es: "Nuevos", en: "Added" },
  diffRemoved: { es: "Ya no los pide", en: "No longer requested" },
  diffChanged: { es: "Cambian de alcance", en: "Scope changed" },
  diffNeedsAttention: {
    es: "Esta versión amplía lo que pedía. Míralo antes de aprobar.",
    en: "This version widens what it asked for. Look before approving.",
  },
  changelog: { es: "Notas de la versión", en: "Release notes" },
  changelogEmpty: { es: "Sin notas.", en: "No notes." },
  rejectedReasonShown: { es: "Motivo del rechazo", en: "Rejection reason" },
  errorTitle: { es: "No se pudo completar", en: "Could not complete" },
} satisfies Record<string, Translation>;

export type ReviewMessageKey = keyof typeof messages;

export function translateReview(lang: Lang, key: ReviewMessageKey, vars?: TranslationVars): string {
  return interpolate(messages[key][lang], vars);
}

export function useReviewT(): (key: ReviewMessageKey, vars?: TranslationVars) => string {
  const lang = useLangOptional();
  return useCallback(
    (key: ReviewMessageKey, vars?: TranslationVars) => translateReview(lang, key, vars),
    [lang],
  );
}

/** La etiqueta humana de un `review_status`. */
export function statusLabel(
  t: (key: ReviewMessageKey, vars?: TranslationVars) => string,
  status: string,
): string {
  switch (status) {
    case "pending_review":
      return t("statusPendingReview");
    case "published":
      return t("statusPublished");
    case "rejected":
      return t("statusRejected");
    case "draft":
      return t("statusDraft");
    default:
      return status;
  }
}
