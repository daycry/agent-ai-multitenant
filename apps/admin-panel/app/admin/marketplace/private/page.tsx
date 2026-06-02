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
 * task_09_1_02 — UX de publicación más amable y descubrible: plantillas /
 * ejemplos insertables (un SKILL.md y un tool.yaml VÁLIDOS que el parser
 * acepta) con botón "usar ejemplo", ayuda de formato inline por tipo de
 * manifest, y feedback de validación claro (se extrae el mensaje del 422 del
 * parser que explica QUÉ falla).
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
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, FileCode2, PackagePlus, Store, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { Select } from "@/components/ui/select";
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

// --------------------------------------------------------------------------
// Insertable examples — VALID manifests the Phase C parsers accept verbatim.
// Each one parses cleanly through the backend (skill_format / tool_format),
// so "usar ejemplo" + "Publicar" succeeds out of the box and the operator can
// edit from a working baseline rather than a blank box.
// --------------------------------------------------------------------------
const SKILL_EXAMPLE = `---
name: internal-reporter
description: Genera el informe interno semanal del equipo.
version: 1.0.0
dependencies:
  - httpx>=0.27
permissions:
  allowed_paths: [/workspace/reports]
  network_policy: none
examples:
  - title: Informe semanal
    prompt: "Genera el informe de la semana 23"
---

# Internal Reporter

Skill interna del tenant que recopila métricas y produce el informe
semanal en /workspace/reports.

## Uso

Indica la semana y la skill genera el documento.
`;

const TOOL_EXAMPLE = `name: internal-fetch
version: 1.0.0
description: Descarga una URL interna y devuelve su cuerpo.
kind: tool
entrypoint: internal_fetch.main:run
implementation:
  runtime: python
  module: internal_fetch.main
  reference: git+https://git.interno.test/tools/internal-fetch@v1.0.0
dependencies:
  - httpx>=0.27
permissions:
  allowed_domains: [api.interno.test]
  network_policy: restricted
input_schema:
  type: object
  properties:
    url: { type: string }
  required: [url]
output_schema:
  type: object
  properties:
    status: { type: integer }
    body: { type: string }
`;

const MCP_EXAMPLE = `name: internal-mcp
version: 1.0.0
description: Servidor MCP interno que expone las herramientas del equipo.
kind: mcp_server
entrypoint: internal_mcp.server:main
implementation:
  runtime: node
  module: internal_mcp.server
  reference: npm:@interno/mcp-server@1
permissions:
  allowed_domains: [mcp.interno.test]
  network_policy: restricted
`;

/** The valid example manifest for each kind (used by "usar ejemplo"). */
const EXAMPLE_BY_KIND: Record<ListingKind, string> = {
  skill: SKILL_EXAMPLE,
  tool: TOOL_EXAMPLE,
  mcp_server: MCP_EXAMPLE,
};

// --------------------------------------------------------------------------
// Inline format help — what each manifest kind needs. Mirrors the Phase C
// parsers (skill_format.REQUIRED_FIELDS / tool_format.REQUIRED_FIELDS) so the
// operator knows the required surface BEFORE the backend rejects it.
// --------------------------------------------------------------------------
interface FormatHelp {
  summary: string;
  required: string[];
  optional: string[];
}

const FORMAT_HELP: Record<ListingKind, FormatHelp> = {
  skill: {
    summary:
      "Un SKILL.md es Markdown con un frontmatter YAML (entre líneas ---) seguido del cuerpo en prosa.",
    required: ["name", "description", "version (semver, p. ej. 1.0.0)"],
    optional: [
      "dependencies (lista)",
      "permissions: allowed_domains / allowed_paths / network_policy (none | restricted | open)",
      "examples (lista de { title, prompt })",
    ],
  },
  tool: {
    summary: "Un tool es un documento YAML plano (sin cuerpo Markdown).",
    required: [
      "name",
      "version (semver)",
      "description",
      "entrypoint (módulo:función)",
      "implementation.runtime",
    ],
    optional: [
      "kind (por defecto tool)",
      "implementation.module / implementation.reference",
      "dependencies, input_schema, output_schema",
      "permissions: allowed_domains / allowed_paths / network_policy",
    ],
  },
  mcp_server: {
    summary:
      "Un MCP server usa el mismo YAML que un tool, con kind: mcp_server (debe coincidir con el tipo elegido).",
    required: [
      "name",
      "version (semver)",
      "description",
      "entrypoint",
      "implementation.runtime",
      "kind: mcp_server",
    ],
    optional: [
      "implementation.module / implementation.reference",
      "dependencies, input_schema, output_schema",
      "permissions: allowed_domains / allowed_paths / network_policy",
    ],
  },
};

/**
 * Extract a human-readable message from a publish error. The backend's 422
 * is FastAPI's ``{"detail": "<parser message>"}`` JSON — surface that exact
 * parser message (what is malformed) rather than the raw envelope. Falls back
 * to the raw body / string for non-JSON errors.
 */
function publishErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    try {
      const parsed = JSON.parse(err.body) as { detail?: unknown };
      if (typeof parsed.detail === "string" && parsed.detail.trim() !== "") {
        return parsed.detail;
      }
    } catch {
      // body was not JSON — fall through to the raw body.
    }
    return err.body;
  }
  return String(err);
}

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

  const help = FORMAT_HELP[kind];

  function submit() {
    if (manifest.trim() === "") return;
    publishMutation.mutate({ kind, manifest, author: author.trim() === "" ? null : author });
  }

  /** Insert the valid example manifest for the currently selected kind. */
  function useExample() {
    setManifest(EXAMPLE_BY_KIND[kind]);
    publishMutation.reset();
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
        actions={
          <Button asChild variant="outline" size="sm" data-testid="private-back-to-catalog">
            <Link href="/admin/marketplace">
              <ArrowLeft className="mr-1 h-3.5 w-3.5" />
              Volver al catálogo
            </Link>
          </Button>
        }
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
              <Select
                id="private-kind"
                value={kind}
                onChange={(e) => setKind(e.target.value as ListingKind)}
                data-testid="private-kind-select"
              >
                {KIND_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </Select>
            </div>

            {/* Inline format help — what THIS kind's manifest needs. */}
            <div
              className="bg-muted/40 space-y-2 rounded-md border p-3"
              data-testid="private-format-help"
            >
              <p className="text-xs font-medium" data-testid="private-format-help-summary">
                {help.summary}
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                <div>
                  <p className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wide">
                    Campos obligatorios
                  </p>
                  <ul className="text-muted-foreground mt-1 list-disc space-y-0.5 pl-4 text-xs">
                    {help.required.map((field) => (
                      <li key={field}>
                        <code className="text-foreground">{field}</code>
                      </li>
                    ))}
                  </ul>
                </div>
                <div>
                  <p className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wide">
                    Opcionales
                  </p>
                  <ul className="text-muted-foreground mt-1 list-disc space-y-0.5 pl-4 text-xs">
                    {help.optional.map((field) => (
                      <li key={field}>
                        <code className="text-foreground">{field}</code>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
              <p className="text-muted-foreground text-[11px]">
                ¿Dudas con el formato? Consulta la{" "}
                <Link href="/docs/03-guides/publicar-en-marketplace" className="underline">
                  guía de publicación
                </Link>
                .
              </p>
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
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor="private-manifest">Manifest</Label>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={useExample}
                  data-testid="private-use-example"
                >
                  <FileCode2 className="mr-1 h-3.5 w-3.5" />
                  Usar ejemplo
                </Button>
              </div>
              <textarea
                id="private-manifest"
                className="border-input bg-background min-h-[220px] w-full rounded-md border px-3 py-2 font-mono text-xs"
                placeholder={EXAMPLE_BY_KIND[kind]}
                value={manifest}
                onChange={(e) => setManifest(e.target.value)}
                spellCheck={false}
                data-testid="private-manifest"
              />
              <p className="text-muted-foreground text-[11px]" data-testid="private-example-hint">
                Pulsa «Usar ejemplo» para insertar un manifest {kind} válido y editarlo desde ahí.
              </p>
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
              <div
                className="border-destructive/40 bg-destructive/10 rounded-md border p-3"
                role="alert"
                data-testid="private-publish-error"
              >
                <p className="text-destructive text-xs font-semibold">No se pudo publicar</p>
                <p className="text-destructive mt-1 break-words text-xs">
                  {publishErrorMessage(publishMutation.error)}
                </p>
                <p className="text-muted-foreground mt-1 text-[11px]">
                  Corrige el manifest según el mensaje y vuelve a publicar. No se ha creado ningún
                  listing.
                </p>
              </div>
            ) : null}

            {publishMutation.isSuccess ? (
              <p
                className="text-xs text-green-600 dark:text-green-400"
                data-testid="private-publish-success"
              >
                Listing publicado. Ya aparece en tu catálogo privado.
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
            <CardContent className="space-y-3 py-10 text-center">
              <p className="text-muted-foreground text-sm italic" data-testid="private-empty">
                Este tenant todavía no ha publicado ningún listing privado.
              </p>
              <RoleGuard min="tenant_admin">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={useExample}
                  data-testid="private-empty-use-example"
                >
                  <FileCode2 className="mr-1 h-3.5 w-3.5" />
                  Empezar con un ejemplo
                </Button>
              </RoleGuard>
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
