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
 *   - Compartir  — gestiona los shares cross-tenant del tenant OWNER
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

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, PackagePlus, Share2, ShieldCheck, Store, Trash2 } from "lucide-react";

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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { Select } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { apiFetch } from "@/lib/api";
import { useT, type Translator } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

// ---------------------------------------------------------------------------
// Types — mirror api_server.schemas.marketplace
// ---------------------------------------------------------------------------
interface MarketplaceListing {
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

interface MarketplaceInstallation {
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
}

interface MarketplaceShare {
  id: string;
  listing_id: string;
  owner_tenant_id: string;
  target_tenant_id: string;
  granted_by: string | null;
  revoked_at: string | null;
  revoked_by: string | null;
  created_at: string;
  updated_at: string;
}

const TRUST_BADGE: Record<string, BadgeVariant> = {
  verified: "success",
  community: "info",
  experimental: "warning",
};

/**
 * Variante + CLAVE del diccionario por estado de instalacion.
 *
 * El mapa se conserva para que TypeScript siga exigiendo una entrada por
 * estado; lo que cambia es que lleva la clave y no el texto.
 */
const STATUS_BADGE: Record<
  string,
  { variant: BadgeVariant; labelKey: Parameters<Translator<"marketplace">>[0] }
> = {
  enabled: { variant: "success", labelKey: "installStatusEnabled" },
  disabled: { variant: "warning", labelKey: "installStatusDisabled" },
  revoked: { variant: "muted", labelKey: "installStatusRevoked" },
};

/** Listings carrying a non-null tenant_id are the caller tenant's PRIVATE rows. */
function isPrivate(listing: MarketplaceListing): boolean {
  return listing.tenant_id !== null;
}

// ===========================================================================
// Page
// ===========================================================================
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
          <TabsTrigger value="shares" data-testid="marketplace-tab-shares">
            {t("tabShares")}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="catalog" data-testid="marketplace-panel-catalog">
          <CatalogTab />
        </TabsContent>
        <TabsContent value="installed" data-testid="marketplace-panel-installed">
          <InstalledTab />
        </TabsContent>
        <TabsContent value="shares" data-testid="marketplace-panel-shares">
          <SharesTab />
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
// Installed — consent / revoke / uninstall
// ===========================================================================
function InstalledTab() {
  const t = useT("marketplace");
  const tCommon = useT("common");
  const errorText = useErrorText();
  const queryClient = useQueryClient();

  // Misma clave y misma ruta que el aviso de actualización (`catalog-updates`):
  // una sola petición para las dos lecturas, y ningún sitio donde el `limit`
  // pueda discrepar.
  const installedQuery = useQuery({
    queryKey: INSTALLATIONS_KEY,
    queryFn: () => apiFetch<MarketplaceInstallation[]>(INSTALLATIONS_PATH),
    refetchOnWindowFocus: false,
  });

  const revokeMutation = useMutation({
    mutationFn: (installationId: string) =>
      apiFetch<MarketplaceInstallation>(`/marketplace/installations/${installationId}/revoke`, {
        method: "POST",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["marketplace-installations"] });
    },
  });

  const uninstallMutation = useMutation({
    mutationFn: (installationId: string) =>
      apiFetch<void>(`/marketplace/installations/${installationId}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["marketplace-installations"] });
    },
  });

  if (installedQuery.isLoading) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="installed-loading">
        {tCommon("loading")}
      </p>
    );
  }
  if (installedQuery.isError) {
    return (
      <p className="text-destructive text-sm" data-testid="installed-error">
        {errorText(installedQuery.error)}
      </p>
    );
  }

  const installations = installedQuery.data ?? [];
  if (installations.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center">
          <p className="text-muted-foreground text-sm italic" data-testid="installed-empty">
            {t("installedEmpty")}
          </p>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="space-y-3" data-testid="installed-list">
      {installations.map((install) => {
        const known = STATUS_BADGE[install.status];
        // Un estado que este mapa no conoce se muestra CRUDO a proposito: es el
        // valor del backend, y traducirlo a un texto inventado esconderia la
        // divergencia en vez de enseniarla.
        const statusVariant = known?.variant ?? ("muted" as BadgeVariant);
        const statusLabel = known ? t(known.labelKey) : install.status;
        const isRevoked = install.status === "revoked";
        return (
          <Card key={install.id} data-testid={`installed-${install.id}`}>
            <CardHeader className="flex flex-row items-start justify-between gap-4">
              <div className="min-w-0">
                <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                  <span className="truncate font-mono text-sm">{install.listing_id}</span>
                  <Badge variant="muted">{install.version}</Badge>
                  <Badge variant={statusVariant} data-testid={`installed-status-${install.id}`}>
                    {statusLabel}
                  </Badge>
                </CardTitle>
              </div>
              <div className="flex shrink-0 flex-wrap items-center gap-1">
                <Button
                  asChild
                  variant="outline"
                  size="sm"
                  data-testid={`installed-consent-${install.id}`}
                >
                  <Link href={`/admin/marketplace/installations/${install.id}/permissions`}>
                    <ShieldCheck className="mr-1 h-3.5 w-3.5" />
                    {t("permissions")}
                  </Link>
                </Button>
                <RoleGuard min="tenant_admin">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => revokeMutation.mutate(install.id)}
                    disabled={isRevoked || revokeMutation.isPending}
                    data-testid={`installed-revoke-${install.id}`}
                    aria-label={t("revoke")}
                  >
                    <Ban className="mr-1 h-3.5 w-3.5" />
                    {t("revoke")}
                  </Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => uninstallMutation.mutate(install.id)}
                    disabled={uninstallMutation.isPending}
                    data-testid={`installed-uninstall-${install.id}`}
                    aria-label={t("uninstall")}
                  >
                    <Trash2 className="mr-1 h-3.5 w-3.5" />
                    {t("uninstall")}
                  </Button>
                </RoleGuard>
              </div>
            </CardHeader>
          </Card>
        );
      })}

      {revokeMutation.isError ? (
        <p className="text-destructive text-xs" data-testid="installed-revoke-error">
          {errorText(revokeMutation.error)}
        </p>
      ) : null}
      {uninstallMutation.isError ? (
        <p className="text-destructive text-xs" data-testid="installed-uninstall-error">
          {errorText(uninstallMutation.error)}
        </p>
      ) : null}
    </div>
  );
}

// ===========================================================================
// Shares — cross-tenant sharing (opt-in, explicit grant, System-Admin audited)
// ===========================================================================
function SharesTab() {
  const t = useT("marketplace");
  const tCommon = useT("common");
  const errorText = useErrorText();
  const queryClient = useQueryClient();

  const listingsQuery = useQuery({
    queryKey: ["marketplace-listings"],
    queryFn: () => apiFetch<MarketplaceListing[]>("/marketplace/listings?limit=100"),
    refetchOnWindowFocus: false,
  });

  const sharesQuery = useQuery({
    queryKey: ["marketplace-shares"],
    queryFn: () => apiFetch<MarketplaceShare[]>("/marketplace/shares"),
    refetchOnWindowFocus: false,
  });

  // Only the tenant's OWN private listings can be shared — a global catalog
  // listing is already visible to everyone (nothing to share). The backend
  // enforces this; we only offer shareable rows in the picker.
  const privateListings = useMemo(
    () => (listingsQuery.data ?? []).filter(isPrivate),
    [listingsQuery.data],
  );

  const [listingId, setListingId] = useState<string>("");
  const [targetTenantId, setTargetTenantId] = useState<string>("");

  const shareMutation = useMutation({
    mutationFn: (payload: { listing_id: string; target_tenant_id: string }) =>
      apiFetch<MarketplaceShare>("/marketplace/shares", { method: "POST", body: payload }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["marketplace-shares"] });
      setTargetTenantId("");
    },
  });

  const revokeShareMutation = useMutation({
    mutationFn: (shareId: string) =>
      apiFetch<void>(`/marketplace/shares/${shareId}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["marketplace-shares"] });
    },
  });

  function submitShare() {
    if (listingId === "" || targetTenantId.trim() === "") return;
    shareMutation.mutate({ listing_id: listingId, target_tenant_id: targetTenantId.trim() });
  }

  const shares = sharesQuery.data ?? [];

  return (
    <div className="space-y-6">
      {/* Create a share (tenant_admin only) */}
      <RoleGuard min="tenant_admin">
        <Card data-testid="share-create-card">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Share2 className="h-4 w-4" />
              {t("shareCardTitle")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="text-muted-foreground text-xs" data-testid="share-explainer">
              {t("shareExplainer")}
            </p>

            <div className="space-y-1">
              <Label htmlFor="share-listing">{t("shareListingLabel")}</Label>
              <Select
                id="share-listing"
                value={listingId}
                onChange={(e) => setListingId(e.target.value)}
                data-testid="share-listing-select"
              >
                <option value="">{t("sharePickListing")}</option>
                {privateListings.map((listing) => (
                  <option key={listing.id} value={listing.id}>
                    {listing.name} {listing.version}
                  </option>
                ))}
              </Select>
              {privateListings.length === 0 ? (
                <p className="text-muted-foreground text-xs" data-testid="share-no-private">
                  {t("shareNoPrivateBefore")}{" "}
                  <Link href="/admin/marketplace/private" className="underline">
                    {t("shareNoPrivateLink")}
                  </Link>
                  .
                </p>
              ) : null}
            </div>

            <div className="space-y-1">
              <Label htmlFor="share-target">{t("shareTargetLabel")}</Label>
              <Input
                id="share-target"
                placeholder="00000000-0000-0000-0000-000000000000"
                value={targetTenantId}
                onChange={(e) => setTargetTenantId(e.target.value)}
                data-testid="share-target-input"
              />
            </div>

            <div className="flex items-center justify-end">
              <Button
                onClick={submitShare}
                disabled={
                  listingId === "" || targetTenantId.trim() === "" || shareMutation.isPending
                }
                data-testid="share-submit"
              >
                {shareMutation.isPending ? t("shareSubmitting") : t("shareSubmit")}
              </Button>
            </div>

            {shareMutation.isError ? (
              <p className="text-destructive text-xs" data-testid="share-error">
                {errorText(shareMutation.error)}
              </p>
            ) : null}
          </CardContent>
        </Card>
      </RoleGuard>

      {/* The tenant's outgoing share grants */}
      <div>
        <h2 className="mb-3 text-sm font-semibold" data-testid="shares-title">
          {t("sharesTitle")}
        </h2>

        {sharesQuery.isLoading ? (
          <p className="text-muted-foreground text-sm" data-testid="shares-loading">
            {tCommon("loading")}
          </p>
        ) : sharesQuery.isError ? (
          <p className="text-destructive text-sm" data-testid="shares-error">
            {errorText(sharesQuery.error)}
          </p>
        ) : shares.length === 0 ? (
          <Card>
            <CardContent className="py-10 text-center">
              <p className="text-muted-foreground text-sm italic" data-testid="shares-empty">
                {t("sharesEmpty")}
              </p>
            </CardContent>
          </Card>
        ) : (
          <ul className="space-y-3" data-testid="shares-list">
            {shares.map((share) => (
              <li key={share.id}>
                <Card data-testid={`share-${share.id}`}>
                  <CardHeader className="flex flex-row items-start justify-between gap-4">
                    <div className="min-w-0 space-y-1">
                      <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                        <span className="text-muted-foreground text-xs">listing</span>
                        <span className="truncate font-mono text-sm">{share.listing_id}</span>
                      </CardTitle>
                      <p className="text-muted-foreground break-all font-mono text-xs">
                        → tenant {share.target_tenant_id}
                      </p>
                    </div>
                    <RoleGuard min="tenant_admin">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => revokeShareMutation.mutate(share.id)}
                        disabled={revokeShareMutation.isPending}
                        data-testid={`share-revoke-${share.id}`}
                        aria-label={t("revokeShare")}
                      >
                        <Ban className="mr-1 h-3.5 w-3.5" />
                        {t("revoke")}
                      </Button>
                    </RoleGuard>
                  </CardHeader>
                </Card>
              </li>
            ))}
          </ul>
        )}

        {revokeShareMutation.isError ? (
          <p className="text-destructive mt-3 text-xs" data-testid="share-revoke-error">
            {errorText(revokeShareMutation.error)}
          </p>
        ) : null}
      </div>
    </div>
  );
}
