"use client";

/**
 * Cola de revisión del marketplace — ADR 0142 D6, `task_mkt2_10`.
 *
 * «Nada entra al catálogo sin ojos». Ésta es la pantalla donde están los ojos:
 * el System Admin ve lo que espera aprobación de CUALQUIER tenant (la cola corre
 * sobre la sesión BYPASSRLS, porque un `pending_review` es invisible para todo
 * el que no sea su autor) y decide.
 *
 * Tres decisiones de diseño que no son cosméticas:
 *
 * 1. **El diff de permisos se enseña antes de la decisión, no después.** Cuando
 *    la versión candidata amplía lo que pedía la anterior, la cola lo dice en
 *    claro y arriba. Revisar sin ver el delta es aprobar por el nombre del
 *    listing, que es exactamente lo que D6 quiere impedir.
 * 2. **El motivo del rechazo es un campo obligatorio con preview markdown**, no
 *    un `prompt()`. El autor lo va a leer para corregir; un rechazo mudo es
 *    indistinguible de un borrado y no se puede recurrir. El backend además lo
 *    rechaza con 422 si llega vacío o en blanco.
 * 3. **Solo se pintan las acciones que el backend acepta** (`availableActions`
 *    espeja `REVIEW_TRANSITIONS`). Ofrecer un botón que devuelve 409 es la peor
 *    UI posible: la que promete algo y luego dice que no.
 *
 * Endpoints (routers/marketplace.py, todos `system_admin` sobre `get_admin_session`):
 *   GET  /admin/marketplace/review-queue?review_status=…
 *   GET  /admin/marketplace/listings/{id}/versions
 *   POST /admin/marketplace/listings/{id}/approve   { promote }
 *   POST /admin/marketplace/listings/{id}/reject    { reason }
 *   POST /admin/marketplace/listings/{id}/promote   { trust_level }
 */

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ArrowLeft, Check, ShieldCheck, Store, X } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { RoleGuard } from "@/components/ui/role-guard";
import { Select } from "@/components/ui/select";
import { apiFetch } from "@/lib/api";
import { useErrorText } from "@/lib/use-error-text";

import { statusLabel, useReviewT } from "./review-i18n";
import {
  availableActions,
  deltaNeedsAttention,
  isEmptyDelta,
  permissionDelta,
  previousVersion,
  reviewStatusVariant,
  type ListingVersion,
  type PermissionDelta,
  type ReviewListing,
} from "./review-types";

const QUEUE_KEY = "marketplace-review-queue";

export default function MarketplaceReviewPage() {
  const t = useReviewT();
  const [statusFilter, setStatusFilter] = useState("pending_review");

  const queue = useQuery<ReviewListing[]>({
    queryKey: [QUEUE_KEY, statusFilter],
    queryFn: () =>
      apiFetch<ReviewListing[]>(`/admin/marketplace/review-queue?review_status=${statusFilter}`),
  });

  return (
    <RoleGuard min="system_admin">
      <div className="flex flex-col gap-6">
        <PageHeader
          title={t("title")}
          description={t("subtitle")}
          icon={<ShieldCheck className="h-6 w-6" />}
          actions={
            <Link href="/admin/marketplace">
              <Button variant="outline" size="sm">
                <ArrowLeft className="mr-2 h-4 w-4" />
                {t("back")}
              </Button>
            </Link>
          }
        />

        <div className="flex items-end gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="review-status-filter">{t("filterLabel")}</Label>
            <Select
              id="review-status-filter"
              data-testid="review-status-filter"
              value={statusFilter}
              onChange={(event) => setStatusFilter(event.target.value)}
            >
              <option value="pending_review">{t("statusPendingReview")}</option>
              <option value="published">{t("statusPublished")}</option>
              <option value="rejected">{t("statusRejected")}</option>
              <option value="draft">{t("statusDraft")}</option>
            </Select>
          </div>
        </div>

        {queue.isLoading ? (
          <p className="text-muted-foreground text-sm">{t("loading")}</p>
        ) : (queue.data ?? []).length === 0 ? (
          <p className="text-muted-foreground text-sm" data-testid="review-queue-empty">
            {t("empty")}
          </p>
        ) : (
          <div className="flex flex-col gap-4">
            {(queue.data ?? []).map((listing) => (
              <ReviewCard key={listing.id} listing={listing} filter={statusFilter} />
            ))}
          </div>
        )}
      </div>
    </RoleGuard>
  );
}

// ---------------------------------------------------------------------------
// Una fila de la cola
// ---------------------------------------------------------------------------
function ReviewCard({ listing, filter }: { listing: ReviewListing; filter: string }) {
  const errorText = useErrorText();
  const t = useReviewT();
  const qc = useQueryClient();
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);

  const actions = availableActions(listing.review_status);

  // El histórico: es con lo que se compara la versión candidata. Si el listing
  // no tiene más versiones, el diff dirá honestamente «es la primera».
  const versions = useQuery<ListingVersion[]>({
    queryKey: ["marketplace-listing-versions", listing.id],
    queryFn: () => apiFetch<ListingVersion[]>(`/admin/marketplace/listings/${listing.id}/versions`),
  });

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: [QUEUE_KEY, filter] });
    void qc.invalidateQueries({ queryKey: ["marketplace-listing-versions", listing.id] });
  };

  const act = useMutation({
    mutationFn: (input: { path: string; body: Record<string, unknown> }) =>
      apiFetch(`/admin/marketplace/listings/${listing.id}/${input.path}`, {
        method: "POST",
        body: JSON.stringify(input.body),
      }),
    onSuccess: () => {
      setError(null);
      setRejecting(false);
      setReason("");
      invalidate();
    },
    onError: (err: unknown) => setError(errorText(err)),
  });

  const previous = previousVersion(versions.data ?? [], listing.version);
  const delta: PermissionDelta = permissionDelta(
    previous?.requested_permissions,
    listing.requested_permissions,
  );
  const current = (versions.data ?? []).find((v) => v.version === listing.version);

  const submitRejection = () => {
    if (reason.trim().length === 0) {
      setError(t("rejectReasonMissing"));
      return;
    }
    act.mutate({ path: "reject", body: { reason } });
  };

  return (
    <Card data-testid={`review-card-${listing.name}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <CardTitle className="flex items-center gap-2">
            <Store className="h-4 w-4" />
            {listing.name}
            <span className="text-muted-foreground text-sm font-normal">v{listing.version}</span>
          </CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="muted">{listing.kind}</Badge>
            <Badge variant="muted">{listing.trust_level}</Badge>
            {/* El estado sí tiñe: en una cola de revisión, «rechazado» y
                «publicado» no pueden leerse igual de un vistazo. */}
            <Badge
              variant={reviewStatusVariant(listing.review_status)}
              data-testid="review-status-badge"
            >
              {statusLabel(t, listing.review_status)}
            </Badge>
            <span className="text-muted-foreground text-xs">
              {listing.tenant_id === null
                ? t("ownerGlobal")
                : t("ownerTenant", { tenant: listing.tenant_id.slice(0, 8) })}
            </span>
          </div>
          {listing.description ? (
            <p className="text-muted-foreground text-sm">{listing.description}</p>
          ) : null}
        </div>
      </CardHeader>

      <CardContent className="flex flex-col gap-4">
        <PermissionDiff delta={delta} previousVersionName={previous?.version} />

        <section className="flex flex-col gap-1">
          <h4 className="text-sm font-medium">{t("changelog")}</h4>
          <p className="text-muted-foreground text-sm whitespace-pre-wrap">
            {current?.changelog ?? t("changelogEmpty")}
          </p>
        </section>

        {listing.review_status === "rejected" && listing.rejection_reason ? (
          <section className="flex flex-col gap-1">
            <h4 className="text-sm font-medium">{t("rejectedReasonShown")}</h4>
            <p className="text-muted-foreground text-sm" data-testid="review-rejection-reason">
              {listing.rejection_reason}
            </p>
          </section>
        ) : null}

        <details className="text-sm">
          <summary className="cursor-pointer font-medium">{t("manifest")}</summary>
          <pre className="bg-muted mt-2 max-h-72 overflow-auto rounded-md p-3 text-xs">
            {JSON.stringify(listing.manifest, null, 2)}
          </pre>
        </details>

        {error ? (
          <p className="text-destructive text-sm" data-testid="review-error">
            {t("errorTitle")}: {error}
          </p>
        ) : null}

        {rejecting ? (
          <div className="flex flex-col gap-2">
            <Label htmlFor={`reject-reason-${listing.id}`}>{t("rejectReasonLabel")}</Label>
            <MarkdownTextarea
              data-testid="reject-reason"
              value={reason}
              onChange={setReason}
              rows={3}
              hint={t("rejectReasonHelp")}
            />
            <div className="flex gap-2">
              <Button
                variant="destructive"
                size="sm"
                data-testid="reject-confirm"
                disabled={act.isPending}
                onClick={submitRejection}
              >
                {t("rejectConfirm")}
              </Button>
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  setRejecting(false);
                  setError(null);
                }}
              >
                {t("cancel")}
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            {actions.canApprove ? (
              <>
                <Button
                  size="sm"
                  data-testid="approve"
                  disabled={act.isPending}
                  onClick={() => act.mutate({ path: "approve", body: { promote: false } })}
                >
                  <Check className="mr-2 h-4 w-4" />
                  {t("approve")}
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  data-testid="approve-and-promote"
                  disabled={act.isPending}
                  onClick={() => act.mutate({ path: "approve", body: { promote: true } })}
                >
                  <ShieldCheck className="mr-2 h-4 w-4" />
                  {t("approveAndPromote")}
                </Button>
              </>
            ) : null}
            {actions.canReject ? (
              <Button
                size="sm"
                variant="destructive"
                data-testid="reject"
                onClick={() => setRejecting(true)}
              >
                <X className="mr-2 h-4 w-4" />
                {t("reject")}
              </Button>
            ) : null}
            {actions.canPromote ? (
              listing.trust_level === "verified" ? (
                <Button
                  size="sm"
                  variant="outline"
                  data-testid="demote"
                  disabled={act.isPending}
                  onClick={() =>
                    act.mutate({ path: "promote", body: { trust_level: "community" } })
                  }
                >
                  {t("demote")}
                </Button>
              ) : (
                <Button
                  size="sm"
                  variant="outline"
                  data-testid="promote"
                  disabled={act.isPending}
                  onClick={() => act.mutate({ path: "promote", body: { trust_level: "verified" } })}
                >
                  <ShieldCheck className="mr-2 h-4 w-4" />
                  {t("promote")}
                </Button>
              )
            ) : null}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// El diff de permisos — lo primero que el revisor tiene que ver
// ---------------------------------------------------------------------------
function PermissionDiff({
  delta,
  previousVersionName,
}: {
  delta: PermissionDelta;
  previousVersionName?: string;
}) {
  const t = useReviewT();

  if (previousVersionName === undefined) {
    return (
      <section className="flex flex-col gap-1" data-testid="permission-diff">
        <h4 className="text-sm font-medium">{t("permissionsTitle")}</h4>
        <p className="text-muted-foreground text-sm">{t("diffFirstVersion")}</p>
        <PermissionList items={delta.added} emptyLabel={t("permissionsNone")} />
      </section>
    );
  }

  return (
    <section className="flex flex-col gap-2" data-testid="permission-diff">
      <h4 className="text-sm font-medium">{t("diffTitle", { version: previousVersionName })}</h4>

      {deltaNeedsAttention(delta) ? (
        <p
          className="text-destructive flex items-center gap-2 text-sm font-medium"
          data-testid="diff-needs-attention"
        >
          <AlertTriangle className="h-4 w-4" />
          {t("diffNeedsAttention")}
        </p>
      ) : null}

      {isEmptyDelta(delta) ? (
        <p className="text-muted-foreground text-sm" data-testid="diff-none">
          {t("diffNone")}
        </p>
      ) : (
        <div className="flex flex-col gap-2 text-sm">
          {delta.added.length > 0 ? (
            <div data-testid="diff-added">
              <span className="font-medium">{t("diffAdded")}: </span>
              {delta.added.map((p) => p.type).join(", ")}
            </div>
          ) : null}
          {delta.changed.length > 0 ? (
            <div data-testid="diff-changed">
              <span className="font-medium">{t("diffChanged")}: </span>
              {delta.changed
                .map((c) => `${c.type} (${JSON.stringify(c.from)} → ${JSON.stringify(c.to)})`)
                .join(", ")}
            </div>
          ) : null}
          {delta.removed.length > 0 ? (
            <div data-testid="diff-removed">
              <span className="font-medium">{t("diffRemoved")}: </span>
              {delta.removed.map((p) => p.type).join(", ")}
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function PermissionList({ items, emptyLabel }: { items: { type: string }[]; emptyLabel: string }) {
  if (items.length === 0) {
    return <p className="text-muted-foreground text-sm">{emptyLabel}</p>;
  }
  return (
    <ul className="text-muted-foreground list-inside list-disc text-sm">
      {items.map((p) => (
        <li key={p.type}>{p.type}</li>
      ))}
    </ul>
  );
}
