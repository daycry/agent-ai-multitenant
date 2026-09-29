"use client";

/**
 * task_09_18 — UI de gestión del marketplace por Tenant Admin.
 *
 * Área cohesiva del admin-panel donde un Tenant Admin gestiona el
 * marketplace de su tenant. Reúne, sin duplicar, las superficies de las
 * tareas anteriores de Plan 09 en cuatro pestañas:
 *
 *   - Catálogo   — explora el catálogo (global público + privados propios) y
 *                  enlaza a la pantalla de consentimiento de una instalación
 *                  (09_07). NO configura nada: desde el ADR 0142 la
 *                  configuración se rinde AL DESPLEGAR en un proyecto (el
 *                  formulario vive en components/marketplace/
 *                  deployment-config-form.tsx y lo abren las tres puertas de
 *                  despliegue). El enlace a la config guiada de Playwright que
 *                  había aquí (09_13) se retiró con `task_mkt2_13`: pedía al
 *                  instalar unos valores —la base_url del sitio bajo prueba—
 *                  que son del proyecto, y los proyectos aún no existen cuando
 *                  se instala.
 *   - Instaladas — lista lo instalado por el tenant, enlaza al
 *                  consentimiento granular (09_07), revoca y desinstala.
 *   - Privadas   — enlaza al marketplace privado del tenant (09_16).
 *   - Publicar   — (`task_ui_32`) publica un listing propio, enlaza la cola de
 *                  revisión (System Admin) y aloja lo de compartir: gestiona los shares cross-tenant del tenant OWNER
 *                  (09_17): crea (opt-in, explícito y auditado por el
 *                  System Admin) y revoca. NUNCA un bypass implícito de RLS:
 *                  el tenant TARGET ve el listing SOLO mediante el grant, y
 *                  el System Admin audita cada share.
 *
 * Frontera multi-tenant (la FEATURE de esta fase):
 *   - Los listings privados están aislados por RLS (tenant_id non-NULL);
 *     otro tenant NUNCA los ve.
 *   - Compartir entre tenants es opt-in explícito + auditado por el System
 *     Admin; el target solo accede mediante el grant vivo.
 *   - Ni firmas ni secretos cruzan el wire (el listing expone is_signed, no
 *     la firma; un share NOMBRA el listing, no lo embebe).
 *
 * Endpoints backend (routers/marketplace.py, RLS + RBAC):
 *   GET    /marketplace/listings                       — browse
 *   GET    /marketplace/installations                  — list_installed
 *   POST   /marketplace/installations/{id}/revoke      — revoke (tenant_admin)
 *   DELETE /marketplace/installations/{id}             — uninstall (tenant_admin)
 *   GET    /marketplace/shares                          — owner's grants
 *   POST   /marketplace/shares                          — share (tenant_admin)
 *   DELETE /marketplace/shares/{id}                     — revoke share (tenant_admin)
 *
 * Permisos: LEER cualquier miembro; las mutaciones (revoke / uninstall /
 * share / revoke-share) van envueltas en <RoleGuard min="tenant_admin"> y el
 * backend las gatea igualmente.
 */

import { useMemo } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { PackagePlus, Store } from "lucide-react";

import { CatalogInstallButton } from "./catalog-install";
import {
  isPrivate,
  type MarketplaceInstallation,
  type MarketplaceListing,
} from "./marketplace-types";
import { InstalledTab } from "./installed-tab";
import { PublishTab } from "./publish-tab";
import {
  CatalogUpdateChip,
  INSTALLATIONS_KEY,
  INSTALLATIONS_PATH,
  MarketplaceUpdatesCallout,
  useInstallationUpdates,
} from "./catalog-updates";

import { PageHeader } from "@/components/layout/page-header";
import { ReviewStatusBadge, ReviewStatusNote } from "@/components/marketplace/review-status-badge";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

// ---------------------------------------------------------------------------
// Types — mirror api_server.schemas.marketplace
// ---------------------------------------------------------------------------

const TRUST_BADGE: Record<string, BadgeVariant> = {
  verified: "success",
  community: "info",
  experimental: "warning",
};

/** Listings carrying a non-null tenant_id are the caller tenant's PRIVATE rows. */
export default function MarketplaceAdminPage() {
  const t = useT("marketplace");
  return (
    <div
      className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="marketplace-admin-page"
    >
      <PageHeader
        icon={<Store className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        data-testid="marketplace-admin-header"
        actions={
          <>
            <Button asChild variant="outline" size="sm" data-testid="marketplace-private-link">
              <Link href="/admin/marketplace/private">
                <Store className="mr-1 h-3.5 w-3.5" />
                {t("privateLink")}
              </Link>
            </Button>
            <RoleGuard min="tenant_admin">
              <Button asChild size="sm" data-testid="marketplace-publish-cta">
                <Link href="/admin/marketplace/private">
                  <PackagePlus className="mr-1 h-3.5 w-3.5" />
                  {t("publish")}
                </Link>
              </Button>
            </RoleGuard>
          </>
        }
      />

      {/* `task_mkt2_12`: el aviso de actualización va FUERA de las pestañas.
          Dentro de «Instaladas» volvería a ser algo a lo que hay que ir a
          mirar, que es exactamente lo que hacía invisible el mecanismo. */}
      <MarketplaceUpdatesCallout />

      <Tabs defaultValue="catalog" className="mt-6" data-testid="marketplace-tabs">
        <TabsList data-testid="marketplace-tablist">
          <TabsTrigger value="catalog" data-testid="marketplace-tab-catalog">
            {t("tabCatalog")}
          </TabsTrigger>
          <TabsTrigger value="installed" data-testid="marketplace-tab-installed">
            {t("tabInstalled")}
          </TabsTrigger>
          <TabsTrigger value="publish" data-testid="marketplace-tab-publish">
            {t("tabPublish")}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="catalog" data-testid="marketplace-panel-catalog">
          <CatalogTab />
        </TabsContent>
        <TabsContent value="installed" data-testid="marketplace-panel-installed">
          <InstalledTab />
        </TabsContent>
        <TabsContent value="publish" data-testid="marketplace-panel-publish">
          <PublishTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}

// ===========================================================================
// Catalog — browse global + own private listings
// ===========================================================================
function CatalogTab() {
  const t = useT("marketplace");
  const tCommon = useT("common");
  const errorText = useErrorText();
  // El estado de actualización de lo instalado, indexado por listing: es lo que
  // convierte una tarjeta del catálogo en «ésta la tienes atrasada». Comparte
  // caché con el aviso de arriba y con la ficha, así que no cuesta una segunda
  // ronda de peticiones.
  const { byListing } = useInstallationUpdates();
  // `task_mk_00`: qué listings tiene ya este tenant, para no ofrecer instalar
  // dos veces. Misma clave y misma ruta que el aviso de actualizaciones, así
  // que sale de la caché ya caliente y no cuesta una petición extra.
  const installedQuery = useQuery({
    queryKey: INSTALLATIONS_KEY,
    queryFn: () => apiFetch<MarketplaceInstallation[]>(INSTALLATIONS_PATH),
    refetchOnWindowFocus: false,
  });
  // Ni `revoked` ni `blocked` cuentan como instalada: la primera se desinstaló y
  // la segunda la rechazó una puerta de seguridad. Ofrecer instalar de nuevo es
  // lo correcto en ambos casos, y pintar «Instalada» en verde sobre una
  // bloqueada diría lo contrario que la pestaña Instaladas, que la pinta en rojo.
  const instaladas = useMemo(
    () =>
      new Set(
        (installedQuery.data ?? [])
          .filter(
            (installation) =>
              installation.status !== "revoked" && installation.status !== "blocked",
          )
          .map((installation) => installation.listing_id),
      ),
    [installedQuery.data],
  );
  const listingsQuery = useQuery({
    queryKey: ["marketplace-listings"],
    queryFn: () => apiFetch<MarketplaceListing[]>("/marketplace/listings?limit=100"),
    refetchOnWindowFocus: false,
  });

  if (listingsQuery.isLoading) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="catalog-loading">
        {tCommon("loading")}
      </p>
    );
  }
  if (listingsQuery.isError) {
    return (
      <p className="text-destructive text-sm" data-testid="catalog-error">
        {errorText(listingsQuery.error)}
      </p>
    );
  }

  const listings = listingsQuery.data ?? [];
  if (listings.length === 0) {
    return (
      <div className="space-y-4">
        <PublishCallout />
        <Card>
          <CardContent className="py-10 text-center">
            <p className="text-muted-foreground text-sm italic" data-testid="catalog-empty">
              {t("catalogEmpty")}
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <PublishCallout />
      <ul className="space-y-3" data-testid="catalog-list">
        {listings.map((listing) => {
          return (
            <li key={listing.id}>
              <Card data-testid={`catalog-listing-${listing.id}`}>
                <CardHeader className="flex flex-row items-start justify-between gap-4">
                  <div className="min-w-0">
                    <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                      <span className="truncate">{listing.name}</span>
                      <Badge variant="info" data-testid={`catalog-kind-${listing.id}`}>
                        {listing.kind}
                      </Badge>
                      <Badge variant="muted">{listing.version}</Badge>
                      <Badge
                        variant={TRUST_BADGE[listing.trust_level] ?? "muted"}
                        data-testid={`catalog-trust-${listing.id}`}
                      >
                        {listing.trust_level}
                      </Badge>
                      {isPrivate(listing) ? (
                        <Badge variant="warning" data-testid={`catalog-private-${listing.id}`}>
                          {t("badgePrivate")}
                        </Badge>
                      ) : (
                        <Badge variant="default" data-testid={`catalog-global-${listing.id}`}>
                          {t("badgeGlobal")}
                        </Badge>
                      )}
                      {/* `task_mkt2_10`: si esta fila NO está publicada es que
                          es del propio tenant y sigue en revisión (o fue
                          rechazada) — nadie más la ve. El catálogo lo dice
                          aquí, donde su autor la va a buscar. */}
                      {listing.review_status !== "published" ? (
                        <ReviewStatusBadge
                          status={listing.review_status}
                          testId={`catalog-review-status-${listing.id}`}
                        />
                      ) : null}
                      {/* `task_mkt2_12`: y si la tiene instalada y atrasada. */}
                      <CatalogUpdateChip
                        checks={byListing.get(listing.id)}
                        testId={`catalog-update-${listing.id}`}
                      />
                    </CardTitle>
                    {listing.description ? (
                      <p className="text-muted-foreground mt-1 break-words text-xs">
                        {listing.description}
                      </p>
                    ) : null}
                    <ReviewStatusNote
                      listing={listing}
                      testId={`catalog-review-note-${listing.id}`}
                    />
                  </div>
                  <CatalogInstallButton
                    listingId={listing.id}
                    installed={instaladas.has(listing.id)}
                    pidePermisos={(listing.requested_permissions ?? []).length > 0}
                  />
                </CardHeader>
              </Card>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

// ===========================================================================
// PublishCallout — a prominent, discoverable "publish your own" banner
// ===========================================================================
/**
 * A tenant_admin-only callout that makes publishing OBVIOUS from the catalog
 * itself: a short explainer plus a primary CTA to the private publish screen.
 * Hidden for non-admins (the RoleGuard), since they cannot publish anyway.
 */
function PublishCallout() {
  const t = useT("marketplaceReview");
  const tMkt = useT("marketplace");
  return (
    <RoleGuard min="tenant_admin">
      <Card
        className="border-primary/30 from-primary/5 bg-gradient-to-r to-transparent"
        data-testid="catalog-publish-callout"
      >
        <CardContent className="flex flex-col gap-3 py-5 sm:flex-row sm:items-center sm:justify-between">
          <div className="flex items-start gap-3">
            <span className="bg-primary/10 text-primary flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
              <PackagePlus className="h-5 w-5" />
            </span>
            <div className="space-y-0.5">
              <p className="text-sm font-semibold">{tMkt("calloutTitle")}</p>
              <p className="text-muted-foreground text-xs">{tMkt("calloutBody")}</p>
              {/* La otra mitad, que faltaba: publicar deja el listing EN COLA.
                  Decirlo aquí —antes de que nadie pulse— evita que la sorpresa
                  llegue después, cuando el listing ya está esperando y su autor
                  cree que está publicado. */}
              <p
                className="text-muted-foreground text-xs"
                data-testid="catalog-publish-review-note"
              >
                {t("beforePublish")}
              </p>
            </div>
          </div>
          <Button asChild size="sm" data-testid="catalog-publish-cta">
            <Link href="/admin/marketplace/private">
              <PackagePlus className="mr-1 h-3.5 w-3.5" />
              {tMkt("calloutCta")}
            </Link>
          </Button>
        </CardContent>
      </Card>
    </RoleGuard>
  );
}

// ===========================================================================
// Shares — cross-tenant sharing (opt-in, explicit grant, System-Admin audited)
// ===========================================================================
