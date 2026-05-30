"use client";

/**
 * task_09_16 — Marketplace privado del tenant.
 *
 * Un tenant publica sus PROPIAS skills/tools internas como listings
 * PRIVADOS (`tenant_id` = tenant del usuario; el modelo híbrido + RLS de la
 * Fase A las aísla — otro tenant NUNCA ve un listing privado ajeno). Esta
 * pantalla:
 *
 *   - lista los listings privados del tenant (catálogo interno),
 *   - deja al tenant_admin PUBLICAR un nuevo listing pegando el manifest
 *     (SKILL.md para una skill, manifest YAML de tool para tool/mcp_server),
 *   - DESPUBLICAR (soft-delete) un listing propio.
 *
 * El backend valida el manifest con los parsers de la Fase C
 * (skill_format / tool_format); un manifest mal formado es un 422 y NO se
 * crea fila. El nivel de confianza (community), la fuente privada y el
 * tenant_id son siempre derivados en servidor — nunca del wire.
 *
 * Endpoints backend (routers/marketplace.py, RLS + RBAC):
 *   GET    /marketplace/listings              — browse (global + privados propios)
 *   POST   /marketplace/private/listings      — publicar (tenant_admin)
 *   DELETE /marketplace/private/listings/{id} — despublicar (tenant_admin)
 *
 * Permisos: LEER cualquier miembro; PUBLICAR/DESPUBLICAR solo tenant_admin
 * (gateado en backend y en la UI con <RoleGuard min="tenant_admin">).
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { PackagePlus, Store, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { ApiError, apiFetch } from "@/lib/api";

// --------------------------------------------------------------------------
// Types — mirror api_server.schemas.marketplace
// --------------------------------------------------------------------------
type ListingKind = "skill" | "tool" | "mcp_server";

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
  requested_permissions: { type: string; value: unknown }[];
  is_signed: boolean;
  created_at: string;
  updated_at: string;
}

const KIND_OPTIONS: { value: ListingKind; label: string }[] = [
  { value: "skill", label: "Skill (SKILL.md)" },
  { value: "tool", label: "Tool (manifest YAML)" },
  { value: "mcp_server", label: "MCP server (manifest YAML)" },
];

const SKILL_PLACEHOLDER = `---
name: internal-reporter
description: Genera el informe interno semanal.
version: 1.0.0
permissions:
  allowed_paths: [/workspace/reports]
  network_policy: none
---

# Internal Reporter

Skill interna del tenant...`;

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function PrivateMarketplacePage() {
  const queryClient = useQueryClient();

  const listingsQuery = useQuery({
    queryKey: ["marketplace-listings"],
    queryFn: () => apiFetch<MarketplaceListing[]>("/marketplace/listings?limit=100"),
    refetchOnWindowFocus: false,
  });

  const [kind, setKind] = useState<ListingKind>("skill");
  const [manifest, setManifest] = useState<string>("");
  const [author, setAuthor] = useState<string>("");

  const publishMutation = useMutation({
    mutationFn: (payload: { kind: ListingKind; manifest: string; author: string | null }) =>
      apiFetch<MarketplaceListing>("/marketplace/private/listings", {
        method: "POST",
        body: payload,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["marketplace-listings"] });
      setManifest("");
      setAuthor("");
    },
  });

  const unpublishMutation = useMutation({
    mutationFn: (listingId: string) =>
      apiFetch<void>(`/marketplace/private/listings/${listingId}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["marketplace-listings"] });
    },
  });

  // Only the caller tenant's OWN private listings (tenant_id non-null). The
  // browse endpoint also returns the global catalog (tenant_id === null),
  // which is NOT part of the private catalog view.
  const privateListings = useMemo(
    () => (listingsQuery.data ?? []).filter((l) => l.tenant_id !== null),
    [listingsQuery.data],
  );

  function submit() {
    if (manifest.trim() === "") return;
    publishMutation.mutate({ kind, manifest, author: author.trim() === "" ? null : author });
  }

  return (
    <div
      className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="private-marketplace-page"
    >
      <PageHeader
        icon={<Store className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Marketplace privado"
        description="Publica las skills y tools internas de tu tenant como listings privados. Solo tu tenant las ve; el manifest se valida al publicar."
        data-testid="private-marketplace-header"
      />

      {/* Publish form (tenant_admin only) */}
      <RoleGuard min="tenant_admin">
        <Card className="mt-6" data-testid="private-publish-card">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <PackagePlus className="h-4 w-4" />
              Publicar listing privado
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1">
              <Label htmlFor="private-kind">Tipo</Label>
              <select
                id="private-kind"
                className="border-input bg-background flex h-10 w-full rounded-md border px-3 py-2 text-sm"
                value={kind}
                onChange={(e) => setKind(e.target.value as ListingKind)}
                data-testid="private-kind-select"
              >
                {KIND_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1">
              <Label htmlFor="private-author">Autor (opcional)</Label>
              <Input
                id="private-author"
                placeholder="Equipo Plataforma"
                value={author}
                onChange={(e) => setAuthor(e.target.value)}
                data-testid="private-author"
              />
            </div>

            <div className="space-y-1">
              <Label htmlFor="private-manifest">Manifest</Label>
              <textarea
                id="private-manifest"
                className="border-input bg-background min-h-[180px] w-full rounded-md border px-3 py-2 font-mono text-xs"
                placeholder={SKILL_PLACEHOLDER}
                value={manifest}
                onChange={(e) => setManifest(e.target.value)}
                data-testid="private-manifest"
              />
            </div>

            <div className="flex items-center justify-between gap-3">
              <p className="text-muted-foreground text-xs">
                El nombre y la versión se leen del manifest. Una versión duplicada se rechaza.
              </p>
              <Button
                onClick={submit}
                disabled={manifest.trim() === "" || publishMutation.isPending}
                data-testid="private-publish-submit"
              >
                {publishMutation.isPending ? "Publicando…" : "Publicar"}
              </Button>
            </div>

            {publishMutation.isError ? (
              <p className="text-destructive text-xs" data-testid="private-publish-error">
                {publishMutation.error instanceof ApiError
                  ? publishMutation.error.body
                  : String(publishMutation.error)}
              </p>
            ) : null}
          </CardContent>
        </Card>
      </RoleGuard>

      {/* The tenant's private catalog */}
      <div className="mt-8">
        <h2 className="mb-3 text-sm font-semibold" data-testid="private-listings-title">
          Catálogo privado del tenant
        </h2>

        {listingsQuery.isLoading ? (
          <p className="text-muted-foreground text-sm" data-testid="private-loading">
            Cargando…
          </p>
        ) : listingsQuery.isError ? (
          <p className="text-destructive text-sm" data-testid="private-error">
            {listingsQuery.error instanceof ApiError
              ? listingsQuery.error.body
              : String(listingsQuery.error)}
          </p>
        ) : privateListings.length === 0 ? (
          <Card>
            <CardContent className="py-10 text-center">
              <p className="text-muted-foreground text-sm italic" data-testid="private-empty">
                Este tenant todavía no ha publicado ningún listing privado.
              </p>
            </CardContent>
          </Card>
        ) : (
          <ul className="space-y-3" data-testid="private-listing-list">
            {privateListings.map((listing) => (
              <li key={listing.id}>
                <Card data-testid={`private-listing-${listing.id}`}>
                  <CardHeader className="flex flex-row items-start justify-between gap-4">
                    <div className="min-w-0">
                      <CardTitle className="flex items-center gap-2 text-base">
                        <span className="truncate">{listing.name}</span>
                        <Badge variant="info" data-testid={`private-listing-kind-${listing.id}`}>
                          {listing.kind}
                        </Badge>
                        <Badge variant="muted">{listing.version}</Badge>
                        <Badge variant="warning">privado</Badge>
                      </CardTitle>
                      {listing.description ? (
                        <p className="text-muted-foreground mt-1 break-words text-xs">
                          {listing.description}
                        </p>
                      ) : null}
                    </div>
                    <RoleGuard min="tenant_admin">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => unpublishMutation.mutate(listing.id)}
                        disabled={unpublishMutation.isPending}
                        data-testid={`private-unpublish-${listing.id}`}
                        aria-label="Despublicar"
                      >
                        <Trash2 className="mr-1 h-3.5 w-3.5" />
                        Despublicar
                      </Button>
                    </RoleGuard>
                  </CardHeader>
                </Card>
              </li>
            ))}
          </ul>
        )}

        {unpublishMutation.isError ? (
          <p className="text-destructive mt-3 text-xs" data-testid="private-unpublish-error">
            {unpublishMutation.error instanceof ApiError
              ? unpublishMutation.error.body
              : String(unpublishMutation.error)}
          </p>
        ) : null}
      </div>
    </div>
  );
}
