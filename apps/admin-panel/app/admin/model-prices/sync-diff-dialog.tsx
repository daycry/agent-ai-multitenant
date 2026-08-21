"use client";

/**
 * Sincronizar precios: dry-run del diff + confirmacion obligatoria (task_11_16).
 *
 * Extraido de `model-price-dialogs.tsx` en prod-16 `task_prod16_06`, que pedia
 * que ninguna seccion pasara de 500 lineas: aquel fichero tenia 686 y tres
 * dialogos que no comparten nada salvo los tipos. Corte verbatim.
 *
 * Flujo en dos pasos (ADR 0021 -- el JSON de LiteLLM es un FEED DE DATOS, nunca
 * un runtime de proveedor):
 *   1) DRY-RUN  POST /admin/model-prices/sync/diff  calcula el diff por modelo
 *      (added / updated / unchanged / increased / removed) SIN escribir.
 *   2) APPLY    POST /admin/model-prices/sync/apply  escribe el catalogo. Si
 *      ALGUN precio sube >10% el backend RECHAZA el apply (409) salvo que se
 *      mande `confirm: true`. El dialogo gatea el checkbox de confirmacion con
 *      `has_large_increase` para que el humano revise la subida.
 */

import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import {
  DIFF_STATUS_BADGE,
  DIFF_STATUS_KEY,
  SKIP_FAMILY_NOT_ACTIVE,
  fmtPct,
  fmtUsd,
  type PriceSyncDiff,
  type PriceSyncResult,
} from "./model-price-types";
// ===========================================================================
// Sincronizar precios — dry-run diff + mandatory confirmation (task_11_16)
//
// Two-step flow (ADR 0021 — the LiteLLM JSON is a DATA FEED only, never a
// provider runtime):
//   1) DRY-RUN  POST /admin/model-prices/sync/diff  computes a per-model diff
//      (added / updated / unchanged / increased / removed) WITHOUT writing.
//   2) APPLY    POST /admin/model-prices/sync/apply  writes the catalog. If
//      ANY price rises >10% the backend REJECTS the apply (409) unless we
//      pass `confirm: true`. The dialog gates an explicit confirmation
//      checkbox on `has_large_increase` so the human reviews the spike.
// ===========================================================================
interface SyncDiffDialogProps {
  onClose: () => void;
  onApplied: () => void;
  // plan price-sync-active-providers (task_psa_02) — the active families the
  // sync is scoped to (derived from the active providers), shown in the dialog.
  syncFamilies: string[];
}

export function SyncDiffDialog({ onClose, onApplied, syncFamilies }: SyncDiffDialogProps) {
  const t = useT("modelPrices");
  const errorText = useErrorText();
  const [confirmed, setConfirmed] = useState(false);

  const diffQuery = useQuery({
    queryKey: ["model-prices", "sync-diff"],
    queryFn: () => apiFetch<PriceSyncDiff>("/admin/model-prices/sync/diff", { method: "POST" }),
    refetchOnWindowFocus: false,
    retry: false,
  });

  const applyMutation = useMutation({
    mutationFn: (confirm: boolean) =>
      apiFetch<PriceSyncResult>("/admin/model-prices/sync/apply", {
        method: "POST",
        body: { confirm },
      }),
    onSuccess: onApplied,
  });

  const diff = diffQuery.data;
  // plan price-sync-active-providers (task_psa_02) — how many feed entries the
  // backend skipped because their family is not an active provider.
  const familyNotActiveSkipped = diff
    ? diff.skipped.filter((s) => s.reason === SKIP_FAMILY_NOT_ACTIVE).length
    : 0;
  const needsConfirm = diff?.has_large_increase ?? false;
  // A change actually exists when something is added / updated / increased.
  const hasChanges = diff ? diff.added + diff.updated + diff.increased > 0 : false;
  // The apply is allowed when there are changes and, if a >10% rise exists,
  // the human has ticked the explicit confirmation box.
  const canApply = hasChanges && (!needsConfirm || confirmed);

  return (
    <Dialog open onOpenChange={(next) => (next ? undefined : onClose())} size="2xl">
      <DialogContent data-testid="price-sync-dialog">
        <DialogHeader>
          <DialogTitle>{t("syncTitle")}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <p className="text-muted-foreground text-xs" data-testid="sync-feed-note">
            {t("syncFeedNote")}
          </p>

          {/* plan price-sync-active-providers (task_psa_02) — the sync is scoped
              to the families of the ACTIVE providers (ADR 0028). With none
              active, the apply imports nothing. */}
          {syncFamilies.length > 0 ? (
            <p className="text-muted-foreground mt-2 text-xs" data-testid="sync-dialog-scope">
              {t("scopeLead")}{" "}
              <span className="text-foreground font-medium">{syncFamilies.join(", ")}</span>{" "}
              {t("syncDialogScopeTail")}
            </p>
          ) : (
            <p className="text-warning mt-2 text-xs" data-testid="sync-dialog-scope-empty">
              {t("syncDialogScopeEmpty")}
            </p>
          )}

          {diffQuery.isLoading ? (
            <p className="text-muted-foreground mt-3 text-sm" data-testid="sync-loading">
              {t("syncCalculating")}
            </p>
          ) : diffQuery.isError ? (
            <p className="text-destructive mt-3 text-sm" data-testid="sync-diff-error">
              {errorText(diffQuery.error)}
            </p>
          ) : diff ? (
            <>
              <div
                className="text-muted-foreground mt-3 flex flex-wrap gap-2 text-xs"
                data-testid="sync-summary"
              >
                <Badge variant="info">{t("syncAdded", { n: diff.added })}</Badge>
                <Badge variant="primary">{t("syncUpdated", { n: diff.updated })}</Badge>
                <Badge variant="danger">{t("syncIncreased", { n: diff.increased })}</Badge>
                <Badge variant="warning">{t("syncRemoved", { n: diff.removed })}</Badge>
                <Badge variant="muted">{t("syncUnchanged", { n: diff.unchanged })}</Badge>
                {/* plan price-sync-active-providers (task_psa_02) — feed entries
                    dropped because their family is not an active provider. */}
                {familyNotActiveSkipped > 0 ? (
                  <Badge variant="muted" data-testid="sync-skipped-family">
                    {t("syncSkippedFamily", { n: familyNotActiveSkipped })}
                  </Badge>
                ) : null}
              </div>

              {hasChanges ? (
                <div
                  className="mt-3 max-h-80 overflow-auto rounded-lg border"
                  data-testid="sync-diff-table"
                >
                  <table className="w-full text-sm">
                    <thead className="bg-muted text-muted-foreground sticky top-0">
                      <tr className="text-left">
                        <th className="px-3 py-2 font-medium">{t("syncColModel")}</th>
                        <th className="px-3 py-2 font-medium">{t("syncColStatus")}</th>
                        <th className="px-3 py-2 font-medium">{t("syncColInput")}</th>
                        <th className="px-3 py-2 font-medium">{t("syncColOutput")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {diff.rows
                        .filter((r) => r.status !== "unchanged")
                        .map((r) => {
                          const key = `${r.provider}:${r.model_id}:${r.modality}`;
                          return (
                            <tr
                              key={key}
                              className="border-t"
                              data-testid={`sync-row-${r.model_id}`}
                              data-status={r.status}
                            >
                              <td className="px-3 py-2 font-mono text-xs">
                                {r.provider} / {r.model_id}
                              </td>
                              <td className="px-3 py-2">
                                <Badge variant={DIFF_STATUS_BADGE[r.status]}>
                                  {t(DIFF_STATUS_KEY[r.status])}
                                </Badge>
                                {r.manual_skipped ? (
                                  <span
                                    className="text-muted-foreground ml-1 text-xs italic"
                                    data-testid={`sync-manual-${r.model_id}`}
                                  >
                                    {t("syncManualSkipped")}
                                  </span>
                                ) : null}
                              </td>
                              <td className="px-3 py-2 text-xs">
                                {fmtUsd(r.old_input)} → {fmtUsd(r.new_input)}
                                {r.input_pct !== null ? (
                                  <span
                                    className={
                                      r.status === "increased"
                                        ? "text-destructive ml-1 font-medium"
                                        : "text-muted-foreground ml-1"
                                    }
                                    data-testid={`sync-input-pct-${r.model_id}`}
                                  >
                                    {fmtPct(r.input_pct)}
                                  </span>
                                ) : null}
                              </td>
                              <td className="px-3 py-2 text-xs">
                                {fmtUsd(r.old_output)} → {fmtUsd(r.new_output)}
                                {r.output_pct !== null ? (
                                  <span
                                    className={
                                      r.status === "increased"
                                        ? "text-destructive ml-1 font-medium"
                                        : "text-muted-foreground ml-1"
                                    }
                                    data-testid={`sync-output-pct-${r.model_id}`}
                                  >
                                    {fmtPct(r.output_pct)}
                                  </span>
                                ) : null}
                              </td>
                            </tr>
                          );
                        })}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p
                  className="text-muted-foreground mt-3 text-sm italic"
                  data-testid="sync-no-changes"
                >
                  {t("syncNoChanges")}
                </p>
              )}

              {needsConfirm ? (
                <div
                  className="border-destructive/40 bg-destructive/5 mt-4 flex items-start gap-2 rounded-lg border p-3"
                  data-testid="sync-confirm-gate"
                >
                  <AlertTriangle className="text-destructive mt-0.5 h-4 w-4 shrink-0" />
                  <div className="space-y-2">
                    <p className="text-destructive text-xs font-medium">
                      {t("syncConfirmWarning", { n: diff.increased })}
                    </p>
                    <label className="flex items-center gap-2 text-xs">
                      <Checkbox
                        checked={confirmed}
                        onChange={(e) => setConfirmed(e.target.checked)}
                        data-testid="sync-confirm-checkbox"
                      />
                      {t("syncConfirmCheckbox")}
                    </label>
                  </div>
                </div>
              ) : null}

              {applyMutation.isError ? (
                <p className="text-destructive mt-3 text-xs" data-testid="sync-apply-error">
                  {errorText(applyMutation.error)}
                </p>
              ) : null}
            </>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="sync-cancel">
            {t("cancel")}
          </Button>
          <Button
            onClick={() => applyMutation.mutate(needsConfirm && confirmed)}
            disabled={!canApply || applyMutation.isPending}
            data-testid="sync-apply"
          >
            {applyMutation.isPending ? t("syncApplying") : t("syncApply")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
