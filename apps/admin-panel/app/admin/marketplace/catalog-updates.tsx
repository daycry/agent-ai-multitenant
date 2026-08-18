"use client";

/**
 * El aviso de actualización en el CATÁLOGO — la mitad que faltaba de
 * `task_mkt2_12`.
 *
 * ## Qué faltaba
 *
 * La tarea pedía «banner "v X.Y disponible" en ficha **y catálogo**». Se
 * entregó sólo la ficha (`installations/[id]/update-banner.tsx`), así que el
 * mecanismo seguía dependiendo de que alguien entrase, una por una, en la ficha
 * de cada instalación para descubrir que se había quedado atrás. Nadie hace
 * eso. El catálogo es la pantalla a la que se entra, y era la única que no se
 * enteraba de nada.
 *
 * ## Por qué el catálogo AVISA y la ficha DECIDE
 *
 * Aquí no se repite el banner completo con su botón de aplicar, y no es por
 * ahorrar trabajo:
 *
 * 1. **El catálogo va por listing y la actualización va por instalación.** Una
 *    misma capacidad puede estar instalada varias veces (una por proyecto) y a
 *    versiones distintas. «Actualizar» sobre una tarjeta del catálogo no tiene
 *    un sujeto único.
 * 2. **Consentir permisos pide sitio.** Aplicar un update que ensancha permisos
 *    exige enseñar el delta ANTES del botón (regla 1 del banner). En una lista
 *    de tarjetas eso, o no cabe, o se convierte en el consentimiento a ciegas
 *    que la ficha evita.
 *
 * De modo que el reparto es: el catálogo dice **qué** está atrasado y **si pide
 * más permisos**, y lleva de un clic al sitio donde se decide. Dos superficies,
 * una sola aritmética (`components/marketplace/update-check.ts`).
 *
 * ## Dos piezas, porque responden a dos preguntas distintas
 *
 * - `MarketplaceUpdatesCallout` — arriba del todo, FUERA de las pestañas:
 *   «tienes N cosas por actualizar, y éstas piden permisos nuevos». Es lo que
 *   se ve sin entrar en ninguna ficha, que es el requisito de la casilla.
 * - `CatalogUpdateChip` — en la tarjeta del catálogo: responde «¿y ésta de
 *   aquí?» mientras se navega, sin obligar a volver arriba.
 *
 * ## El fan-out, y por qué se acepta
 *
 * No hay endpoint de «dame el estado de actualización de todas mis
 * instalaciones»: `update-check` es por instalación. Se piden todas en paralelo
 * con `useQueries`, compartiendo la clave de caché de la ficha (así abrir la
 * ficha desde aquí no vuelve a preguntar). Es N peticiones pequeñas para un N
 * que en un tenant real son unidades o decenas; si algún día son cientos, lo
 * que hay que pedir es el endpoint agregado, no cambiar esta pantalla.
 */

import Link from "next/link";
import { useQueries, useQuery } from "@tanstack/react-query";
import { ArrowUpCircle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  awaitsMajorOptIn,
  hasUpdate,
  proposedVersion,
  requiresConsent,
  updateCheckKey,
  updateCheckPath,
  type UpdateCheck,
} from "@/components/marketplace/update-check";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

/**
 * La lista de instalaciones del tenant: clave y ruta ÚNICAS.
 *
 * Las exporta este módulo y las usa también la pestaña «Instaladas» de
 * `page.tsx`, para que las dos lecturas compartan entrada de caché (una sola
 * petición) y no puedan discrepar en el `limit`.
 */
export const INSTALLATIONS_KEY = ["marketplace-installations"];
export const INSTALLATIONS_PATH = "/marketplace/installations?limit=100";

/** Lo mínimo que este módulo necesita de una instalación. */
interface InstallationRef {
  id: string;
  status: string;
}

/**
 * El estado de actualización de todo lo instalado por el tenant.
 *
 * Se salta las instalaciones `revoked`: una instalación revocada no se
 * actualiza, y anunciarle una versión nueva sería ruido sobre algo que ya no
 * está en uso. Las `disabled` SÍ entran — una instalación deshabilitada por un
 * permiso denegado es justo la que puede arreglar una versión nueva.
 */
export function useInstallationUpdates(): {
  outdated: UpdateCheck[];
  byListing: Map<string, UpdateCheck[]>;
} {
  const installations = useQuery({
    queryKey: INSTALLATIONS_KEY,
    queryFn: () => apiFetch<InstallationRef[]>(INSTALLATIONS_PATH),
    refetchOnWindowFocus: false,
  });

  const live = (installations.data ?? []).filter((i) => i.status !== "revoked");

  const checks = useQueries({
    queries: live.map((installation) => ({
      queryKey: updateCheckKey(installation.id, false),
      queryFn: () => apiFetch<UpdateCheck>(updateCheckPath(installation.id, false)),
      refetchOnWindowFocus: false,
      // Un update-check que falla (una instalación con una versión ilegible en
      // BD contesta 500) no debe reintentarse en bucle ni tumbar el aviso del
      // resto: se queda sin dato y esa instalación no sale en la lista.
      retry: false,
    })),
  });

  const outdated = checks
    .map((result) => result.data)
    .filter((check): check is UpdateCheck => hasUpdate(check));

  const byListing = new Map<string, UpdateCheck[]>();
  for (const check of outdated) {
    byListing.set(check.listing_id, [...(byListing.get(check.listing_id) ?? []), check]);
  }

  return { outdated, byListing };
}

/**
 * «Tienes N instalaciones con una versión más nueva disponible».
 *
 * Vive fuera de las pestañas a propósito: la actualización no es asunto de una
 * pestaña concreta, y esconderla dentro de «Instaladas» la devolvería al mismo
 * sitio del que se la quiere sacar — uno al que hay que ir a mirar.
 */
export function MarketplaceUpdatesCallout() {
  const t = useT("marketplaceDeploy");
  const { outdated } = useInstallationUpdates();

  if (outdated.length === 0) return null;

  const needConsent = outdated.filter(requiresConsent);

  return (
    <Card
      className="border-warning/40 bg-warning/10 mt-4"
      data-testid="marketplace-updates-callout"
    >
      <CardContent className="flex flex-col gap-3 py-4">
        <div className="flex flex-wrap items-center gap-2">
          <ArrowUpCircle className="text-warning h-5 w-5 shrink-0" />
          <span className="font-medium" data-testid="marketplace-updates-title">
            {t("catalogUpdatesTitle", { n: outdated.length })}
          </span>
        </div>

        {/* El dato que decide si esto se puede aplicar de un clic o no. Va
            arriba, antes de las filas: es la misma regla que el banner de la
            ficha — lo que amplía permisos se dice ANTES de ofrecer nada. */}
        {needConsent.length > 0 ? (
          <p className="text-sm" data-testid="marketplace-updates-consent">
            {t("catalogUpdatesConsent", { n: needConsent.length })}
          </p>
        ) : null}

        <ul className="flex flex-col gap-2" data-testid="marketplace-updates-list">
          {outdated.map((check) => (
            <li
              key={check.installation_id}
              className="flex flex-wrap items-center gap-2 text-sm"
              data-testid={`marketplace-update-row-${check.installation_id}`}
            >
              <span>
                {/* Con el opt-in de major pendiente NO se dice «de la X a la
                    Y»: el backend todavía no propone ese salto, y redactarlo
                    como un camino ya trazado prometería un clic que no existe. */}
                {awaitsMajorOptIn(check)
                  ? t("catalogUpdatesRowMajor", {
                      name: check.name,
                      installed: check.installed_version,
                      version: check.latest_version,
                    })
                  : t("catalogUpdatesRow", {
                      name: check.name,
                      installed: check.installed_version,
                      version: proposedVersion(check),
                    })}
              </span>
              {requiresConsent(check) ? (
                <Badge
                  variant="warning"
                  data-testid={`marketplace-update-consent-${check.installation_id}`}
                >
                  {t("catalogUpdatesNeedsConsent")}
                </Badge>
              ) : null}
              {check.latest_is_major_bump ? (
                <Badge
                  variant="warning"
                  data-testid={`marketplace-update-major-${check.installation_id}`}
                >
                  {t("updateMajor")}
                </Badge>
              ) : null}
              <Button
                asChild
                size="sm"
                variant="outline"
                data-testid={`marketplace-update-open-${check.installation_id}`}
              >
                {/* El enlace dice ABRIR, no «actualizar»: quien lo pulsa
                    navega, no aplica nada. La acción vive en la ficha, con el
                    delta a la vista. */}
                <Link href={`/admin/marketplace/installations/${check.installation_id}`}>
                  {t("catalogUpdatesOpen")}
                </Link>
              </Button>
            </li>
          ))}
        </ul>
      </CardContent>
    </Card>
  );
}

/**
 * El distintivo de una tarjeta del catálogo: «esta capacidad, la que estás
 * mirando, la tienes atrasada».
 *
 * Con varias instalaciones del mismo listing no se elige una versión
 * «ganadora» —serían dos verdades a medias en una etiqueta—: se dice cuántas
 * hay, y el aviso de arriba las nombra una a una.
 */
export function CatalogUpdateChip({
  checks,
  testId,
}: {
  checks: UpdateCheck[] | undefined;
  testId?: string;
}) {
  const t = useT("marketplaceDeploy");

  if (!checks || checks.length === 0) return null;

  return (
    <Badge variant="warning" data-testid={testId ?? "catalog-update-chip"}>
      {checks.length === 1
        ? t("catalogUpdateChip", { version: proposedVersion(checks[0]) })
        : t("catalogUpdateChipMany", { n: checks.length })}
    </Badge>
  );
}
