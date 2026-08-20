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
 * task_mkt2_10 — **publicar no publica.** Desde el ADR 0142 D6 la fila nace en
 * `review_status = 'pending_review'` y espera a que un System Admin la mire.
 * Esta pantalla decía «Listing publicado. Ya aparece en tu catálogo privado» y
 * era falso dos veces: ni estaba publicado, ni «aparecía» para nadie más que
 * este tenant — la cláusula de visibilidad del catálogo es `published OR
 * propio` (`marketplace/review.py`), así que ni siquiera un tenant con un grant
 * de share lo ve mientras espera. Quien publicaba y compartía se quedaba
 * esperando una instalación que era imposible.
 *
 * Ahora el resultado sale del `review_status` que devuelve el backend, y cada
 * fila del catálogo privado enseña su estado real con su consecuencia
 * (`components/marketplace/review-status-badge.tsx`).
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
import { ReviewStatusBadge, ReviewStatusNote } from "@/components/marketplace/review-status-badge";
import { apiFetch } from "@/lib/api";
import { useT, type Translator } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

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
  // ADR 0142 D6 — el dato que esta pantalla omitía. Publicar deja la fila en
  // `pending_review`, y hasta que un System Admin la apruebe no la ve nadie más
  // que este tenant (`catalog_visibility_clause` = `published OR propio`).
  review_status: string;
  reviewed_at: string | null;
  rejection_reason: string | null;
  requested_permissions: { type: string; value: unknown }[];
  is_signed: boolean;
  created_at: string;
  updated_at: string;
}

/**
 * Los tres tipos de manifest, con la CLAVE de su etiqueta.
 *
 * `value` es el enum del backend y no se traduce; lo que se traduce es la
 * glosa entre parentesis, que dice al operador que formato va a pegar.
 */
const KIND_OPTIONS: { value: ListingKind; labelKey: PrivateKey }[] = [
  { value: "skill", labelKey: "kindSkill" },
  { value: "tool", labelKey: "kindTool" },
  { value: "mcp_server", labelKey: "kindMcp" },
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
/** Las claves del namespace `marketplacePrivate`. */
type PrivateKey = Parameters<Translator<"marketplacePrivate">>[0];

/**
 * Ayuda de formato por tipo de manifest.
 *
 * Las listas mezclan a proposito NOMBRES DE CAMPO —que no se traducen, porque
 * son lo que hay que escribir literalmente en el YAML— con glosas, que si.
 * Por eso cada elemento es o una cadena cruda o una clave del diccionario, y
 * no todo lo uno o todo lo otro.
 */
type HelpField = { raw: string } | { key: PrivateKey };

interface FormatHelp {
  summaryKey: PrivateKey;
  required: HelpField[];
  optional: HelpField[];
}

const FORMAT_HELP: Record<ListingKind, FormatHelp> = {
  skill: {
    summaryKey: "helpSkillSummary",
    required: [{ raw: "name" }, { raw: "description" }, { key: "fieldVersion" }],
    optional: [
      { key: "fieldDependencies" },
      {
        raw: "permissions: allowed_domains / allowed_paths / network_policy (none | restricted | open)",
      },
      { key: "fieldExamples" },
    ],
  },
  tool: {
    summaryKey: "helpToolSummary",
    required: [
      { raw: "name" },
      { key: "fieldVersionShort" },
      { raw: "description" },
      { key: "fieldEntrypoint" },
      { raw: "implementation.runtime" },
    ],
    optional: [
      { key: "fieldKindDefault" },
      { raw: "implementation.module / implementation.reference" },
      { raw: "dependencies, input_schema, output_schema" },
      { raw: "permissions: allowed_domains / allowed_paths / network_policy" },
    ],
  },
  mcp_server: {
    summaryKey: "helpMcpSummary",
    required: [
      { raw: "name" },
      { key: "fieldVersionShort" },
      { raw: "description" },
      { raw: "entrypoint" },
      { raw: "implementation.runtime" },
      { raw: "kind: mcp_server" },
    ],
    optional: [
      { raw: "implementation.module / implementation.reference" },
      { raw: "dependencies, input_schema, output_schema" },
      { raw: "permissions: allowed_domains / allowed_paths / network_policy" },
    ],
  },
};

// `publishErrorMessage` vivia aqui y hacia DOS cosas: extraer el `detail` del
// 422 del parser —que es lo util— y, si no lo habia, pintar el cuerpo crudo
// del backend, que es justo lo que prohibe `task_prod16_05`. `errorText` hace
// la primera y sustituye la segunda por un texto del diccionario, asi que la
// funcion local sobraba entera.

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function PrivateMarketplacePage() {
  const queryClient = useQueryClient();
  const t = useT("marketplaceReview");
  const tp = useT("marketplacePrivate");
  const tMkt = useT("marketplace");
  const tCommon = useT("common");
  const errorText = useErrorText();

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
        title={tp("title")}
        description={tp("description")}
        data-testid="private-marketplace-header"
        actions={
          <Button asChild variant="outline" size="sm" data-testid="private-back-to-catalog">
            <Link href="/admin/marketplace">
              <ArrowLeft className="mr-1 h-3.5 w-3.5" />
              {tp("backToCatalog")}
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
              {tp("publishCardTitle")}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-1">
              <Label htmlFor="private-kind">{tp("kindLabel")}</Label>
              <Select
                id="private-kind"
                value={kind}
                onChange={(e) => setKind(e.target.value as ListingKind)}
                data-testid="private-kind-select"
              >
                {KIND_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {tp(opt.labelKey)}
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
                {tp(help.summaryKey)}
              </p>
              <div className="grid gap-2 sm:grid-cols-2">
                <div>
                  <p className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wide">
                    {tp("fieldsRequired")}
                  </p>
                  <ul className="text-muted-foreground mt-1 list-disc space-y-0.5 pl-4 text-xs">
                    {help.required.map((field) => {
                      const text = "raw" in field ? field.raw : tp(field.key);
                      return (
                        <li key={text}>
                          <code className="text-foreground">{text}</code>
                        </li>
                      );
                    })}
                  </ul>
                </div>
                <div>
                  <p className="text-muted-foreground text-[11px] font-semibold uppercase tracking-wide">
                    {tp("fieldsOptional")}
                  </p>
                  <ul className="text-muted-foreground mt-1 list-disc space-y-0.5 pl-4 text-xs">
                    {help.optional.map((field) => {
                      const text = "raw" in field ? field.raw : tp(field.key);
                      return (
                        <li key={text}>
                          <code className="text-foreground">{text}</code>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              </div>
              <p className="text-muted-foreground text-[11px]">
                {tp("helpDoubtsBefore")}{" "}
                <Link href="/docs/03-guides/publicar-en-marketplace" className="underline">
                  {tp("helpDoubtsLink")}
                </Link>
                .
              </p>
            </div>

            <div className="space-y-1">
              <Label htmlFor="private-author">{tp("authorLabel")}</Label>
              <Input
                id="private-author"
                placeholder={tp("authorPlaceholder")}
                value={author}
                onChange={(e) => setAuthor(e.target.value)}
                data-testid="private-author"
              />
            </div>

            <div className="space-y-1">
              <div className="flex items-center justify-between gap-2">
                <Label htmlFor="private-manifest">{tp("manifestLabel")}</Label>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={useExample}
                  data-testid="private-use-example"
                >
                  <FileCode2 className="mr-1 h-3.5 w-3.5" />
                  {tp("useExample")}
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
                {tp("exampleHint", { kind })}
              </p>
            </div>

            {/* Lo que «Publicar» hace de verdad, ANTES de pulsarlo. Que la
                sorpresa llegue después —con el listing ya en cola y su autor
                creyendo que está publicado— es el fallo que arregla
                `task_mkt2_10`. */}
            <p
              className="border-info/40 bg-info/10 rounded-md border p-2 text-xs"
              data-testid="private-publish-review-note"
            >
              {t("beforePublish")}
            </p>

            <div className="flex items-center justify-between gap-3">
              <p className="text-muted-foreground text-xs">{tp("versionHint")}</p>
              <Button
                onClick={submit}
                disabled={manifest.trim() === "" || publishMutation.isPending}
                data-testid="private-publish-submit"
              >
                {publishMutation.isPending ? tp("publishing") : tp("publish")}
              </Button>
            </div>

            {publishMutation.isError ? (
              <div
                className="border-destructive/40 bg-destructive/10 rounded-md border p-3"
                role="alert"
                data-testid="private-publish-error"
              >
                <p className="text-destructive text-xs font-semibold">{tp("publishFailedTitle")}</p>
                <p className="text-destructive mt-1 break-words text-xs">
                  {errorText(publishMutation.error)}
                </p>
                <p className="text-muted-foreground mt-1 text-[11px]">{tp("publishFailedHint")}</p>
              </div>
            ) : null}

            {/* El resultado dice lo que HAY, no lo que se esperaba: el estado
                sale de la fila que devuelve el backend (`review_status`), no de
                que la petición haya ido bien. Decir «publicado» a un
                `pending_review` era mentir con un 201 en la mano. */}
            {publishMutation.isSuccess ? (
              publishMutation.data.review_status === "published" ? (
                <p
                  className="text-xs text-green-600 dark:text-green-400"
                  data-testid="private-publish-success"
                >
                  {t("publishedTitle")}
                </p>
              ) : (
                <div
                  className="border-warning/40 bg-warning/10 space-y-1 rounded-md border p-3"
                  role="status"
                  data-testid="private-publish-queued"
                >
                  <p className="flex flex-wrap items-center gap-2 text-xs font-semibold">
                    {t("queuedTitle")}
                    <ReviewStatusBadge
                      status={publishMutation.data.review_status}
                      testId="private-publish-queued-status"
                    />
                  </p>
                  {/* Quién decide y qué pasa mientras: las dos preguntas que
                      quedaban sin respuesta y que llevaban a esperar a que un
                      tenant con quien se había compartido lo instalara… algo
                      que no podía pasar, porque no lo veía. */}
                  <p className="text-xs">{t("queuedWho")}</p>
                  <p className="text-muted-foreground text-xs">{t("queuedMeanwhile")}</p>
                </div>
              )
            ) : null}
          </CardContent>
        </Card>
      </RoleGuard>

      {/* The tenant's private catalog */}
      <div className="mt-8">
        <h2 className="mb-3 text-sm font-semibold" data-testid="private-listings-title">
          {tp("listTitle")}
        </h2>

        {listingsQuery.isLoading ? (
          <p className="text-muted-foreground text-sm" data-testid="private-loading">
            {tCommon("loading")}
          </p>
        ) : listingsQuery.isError ? (
          <p className="text-destructive text-sm" data-testid="private-error">
            {errorText(listingsQuery.error)}
          </p>
        ) : privateListings.length === 0 ? (
          <Card>
            <CardContent className="space-y-3 py-10 text-center">
              <p className="text-muted-foreground text-sm italic" data-testid="private-empty">
                {tp("listEmpty")}
              </p>
              <RoleGuard min="tenant_admin">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={useExample}
                  data-testid="private-empty-use-example"
                >
                  <FileCode2 className="mr-1 h-3.5 w-3.5" />
                  {tp("startWithExample")}
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
                      <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                        <span className="truncate">{listing.name}</span>
                        <Badge variant="info" data-testid={`private-listing-kind-${listing.id}`}>
                          {listing.kind}
                        </Badge>
                        <Badge variant="muted">{listing.version}</Badge>
                        <Badge variant="warning">{tMkt("badgePrivate")}</Badge>
                        {/* El estado REAL de cada fila. «privado» dice de quién
                            es; esto dice si existe para alguien más. */}
                        <ReviewStatusBadge
                          status={listing.review_status}
                          testId={`private-listing-status-${listing.id}`}
                        />
                      </CardTitle>
                      {listing.description ? (
                        <p className="text-muted-foreground mt-1 break-words text-xs">
                          {listing.description}
                        </p>
                      ) : null}
                      <ReviewStatusNote
                        listing={listing}
                        testId={`private-listing-note-${listing.id}`}
                      />
                    </div>
                    <RoleGuard min="tenant_admin">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => unpublishMutation.mutate(listing.id)}
                        disabled={unpublishMutation.isPending}
                        data-testid={`private-unpublish-${listing.id}`}
                        aria-label={tp("unpublish")}
                      >
                        <Trash2 className="mr-1 h-3.5 w-3.5" />
                        {tp("unpublish")}
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
            {errorText(unpublishMutation.error)}
          </p>
        ) : null}
      </div>
    </div>
  );
}
