"use client";

/**
 * Ficha de una instalación del marketplace (ADR 0142, `task_mkt2_06`).
 *
 * Antes de la fase 2 no existía: de una instalación sólo se podía abrir su
 * pantalla de consentimiento, porque no había nada más que enseñar. Con el
 * despliegue como entidad sí lo hay —dónde está desplegada, con qué versión y
 * con qué configuración— y ése es el contenido de esta página.
 *
 * ## Cómo se resuelve la instalación
 *
 * No hay `GET /marketplace/installations/{id}`: la lista está paginada (tope
 * 500) y resolver un id filtrándola sería correcto sólo hasta el tenant número
 * 501. Se usa `GET …/{id}/permissions`, que SÍ es una lectura por id exacta y
 * ya devuelve `listing_id` y `status`. El resto (nombre, versión, `kind`,
 * confianza y el manifest con su `config_schema`) sale del listing.
 *
 * ## De dónde sale el `config_schema` que se rinde aquí
 *
 * Del manifest VIVO del listing. Lo autoritativo es el de la versión PINADA por
 * la instalación —y es lo que el backend valida—, pero esa versión no se expone
 * todavía por API: llega con la fase 4 (versiones). Mientras tanto la diferencia
 * sólo puede aparecer si alguien re-publica el listing sin que la instalación se
 * actualice, y el backend rechazaría la config con sus propios errores, que esta
 * pantalla pinta. Es una degradación honesta, no un fingimiento.
 */

import { useMemo } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { ShieldCheck, Store } from "lucide-react";

import { DeploymentsSection } from "./deployments-section";
import { UpdateBanner } from "./update-banner";

import { PageHeader } from "@/components/layout/page-header";
import { capabilityFromManifest } from "@/components/marketplace/deployment-types";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

interface InstallationPermissions {
  installation_id: string;
  listing_id: string;
  status: string;
}

interface Listing {
  id: string;
  kind: string;
  name: string;
  version: string;
  description: string | null;
  trust_level: string;
  manifest: Record<string, unknown>;
}

const TRUST_BADGE: Record<string, BadgeVariant> = {
  verified: "success",
  community: "info",
  experimental: "warning",
};

export default function InstallationDetailPage() {
  const params = useParams<{ id: string }>();
  const installationId = params.id;
  const t = useT("marketplaceDeploy");
  const errorText = useErrorText();

  const installQuery = useQuery({
    queryKey: ["installation-permissions", installationId],
    queryFn: () =>
      apiFetch<InstallationPermissions>(`/marketplace/installations/${installationId}/permissions`),
    refetchOnWindowFocus: false,
    enabled: Boolean(installationId),
  });

  const listingId = installQuery.data?.listing_id;
  const listingQuery = useQuery({
    queryKey: ["marketplace-listing", listingId],
    queryFn: () => apiFetch<Listing>(`/marketplace/listings/${listingId}`),
    refetchOnWindowFocus: false,
    enabled: Boolean(listingId),
  });

  const capability = useMemo(
    () => capabilityFromManifest(listingQuery.data?.manifest),
    [listingQuery.data],
  );

  const listing = listingQuery.data;

  return (
    <div
      className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="installation-detail-page"
    >
      <PageHeader
        icon={<Store className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={listing ? listing.name : t("installationTitle")}
        description={t("installationDescription")}
        data-testid="installation-detail-header"
        actions={
          <Button asChild variant="outline" size="sm" data-testid="installation-permissions-link">
            <Link href={`/admin/marketplace/installations/${installationId}/permissions`}>
              <ShieldCheck className="mr-1 h-3.5 w-3.5" />
              {t("permissionsLink")}
            </Link>
          </Button>
        }
      />

      {installQuery.isError ? (
        <p className="text-destructive mt-6 text-sm" data-testid="installation-detail-error">
          {errorText(installQuery.error)}
        </p>
      ) : null}

      {listing ? (
        <div className="mt-4 flex flex-wrap items-center gap-2" data-testid="installation-badges">
          <Badge variant="info">{listing.kind}</Badge>
          <Badge variant="muted">{listing.version}</Badge>
          <Badge variant={TRUST_BADGE[listing.trust_level] ?? "muted"}>{listing.trust_level}</Badge>
        </div>
      ) : null}

      {listing?.description ? (
        <p className="text-muted-foreground mt-2 text-sm">{listing.description}</p>
      ) : null}

      {/* Antes de los despliegues: si hay versión nueva, se ve al entrar. Un
          aviso de actualización debajo de una lista larga es un aviso que nadie
          lee (`task_mkt2_12`). */}
      {installQuery.data ? <UpdateBanner installationId={installationId} /> : null}

      {installQuery.data ? (
        <DeploymentsSection installationId={installationId} capability={capability} />
      ) : null}
    </div>
  );
}
