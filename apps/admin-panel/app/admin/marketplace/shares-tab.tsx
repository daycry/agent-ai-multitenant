"use client";

/**
 * Pestaña «Compartir» del marketplace: crear un grant hacia otro tenant y ver
 * los grants vivos del propio. Partida de `page.tsx` en `task_mk_23` al añadir
 * el buscador de tenant (UI-06) y los nombres junto a los UUID.
 */

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Share2 } from "lucide-react";

import { isPrivate, type MarketplaceListing } from "./marketplace-types";

import { TenantPicker } from "@/components/marketplace/tenant-picker";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { Select } from "@/components/ui/select";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

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
  // task_mk_23 (UI-06): nombres junto a los UUID (la lista los resuelve).
  listing_name?: string | null;
  target_tenant_name?: string | null;
}

export function SharesTab() {
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
              {/* task_mk_23 (UI-06): buscador de tenant en vez de teclear el UUID. */}
              <TenantPicker
                inputId="share-target"
                value={targetTenantId}
                onChange={(tenantId) => setTargetTenantId(tenantId)}
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
                        <span className="text-muted-foreground text-xs">
                          {t("shareCardListing")}
                        </span>
                        <span
                          className={share.listing_name ? "truncate" : "truncate font-mono text-sm"}
                          title={share.listing_id}
                          data-testid={`share-listing-name-${share.id}`}
                        >
                          {share.listing_name ?? share.listing_id}
                        </span>
                      </CardTitle>
                      <p
                        className="text-muted-foreground break-all text-xs"
                        title={share.target_tenant_id}
                        data-testid={`share-target-name-${share.id}`}
                      >
                        {t("shareCardTarget")}{" "}
                        <span className={share.target_tenant_name ? "font-medium" : "font-mono"}>
                          {share.target_tenant_name ?? share.target_tenant_id}
                        </span>
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
