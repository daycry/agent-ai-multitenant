"use client";

/**
 * Tabla del catálogo Modelos & Precios: una fila por precio, con el histórico,
 * la edición y el "superseder" por fila.
 *
 * Extraída de `page.tsx` en prod-16 `task_prod16_06`. Corte verbatim: mismos
 * `data-testid`, mismo formato USD y mismo gateado por `RoleGuard`.
 *
 * La mutación de superseder vive aquí porque este es su único llamante; sigue
 * invalidando `["model-prices"]`, así que la lista de la página se refresca
 * igual que antes.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Coins, History, Pencil, Trash2 } from "lucide-react";

import { StateBlock } from "@/components/shared/state-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { RoleGuard } from "@/components/ui/role-guard";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { SOURCE_BADGE, UNIT_KEY, fmtDate, fmtUsd, type ModelPrice } from "./model-price-types";

export interface HistoryTarget {
  provider: string;
  model_id: string;
  modality: string;
}

export function PriceTable({
  rows,
  providerLabel,
  isLoading,
  isError,
  error,
  onHistory,
  onEdit,
}: {
  rows: ModelPrice[];
  /** provider_id → display_name; sólo poblado para el System Admin. */
  providerLabel: Map<string, string>;
  isLoading: boolean;
  isError: boolean;
  error: unknown;
  onHistory: (target: HistoryTarget) => void;
  onEdit: (price: ModelPrice) => void;
}) {
  const t = useT("modelPrices");
  const errorText = useErrorText();
  const queryClient = useQueryClient();

  const supersedeMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<ModelPrice>(`/admin/model-prices/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["model-prices"] });
    },
  });

  return (
    <div className="mt-6">
      <StateBlock
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={rows.length === 0}
        loadingLabel={t("loading")}
        loadingTestId="prices-loading"
        errorTitle={t("loadError")}
        errorTestId="prices-error"
        emptyIcon={Coins}
        emptyTitle={t("emptyTitle")}
        emptyDescription={t("emptyDescription")}
        emptyTestId="prices-empty"
      >
        <div className="overflow-hidden rounded-xl border">
          <Table data-testid="prices-table" className="text-sm">
            <TableHeader className="bg-muted normal-case">
              <TableRow>
                <TableHead className="px-3 py-2">{t("colFamily")}</TableHead>
                <TableHead className="px-3 py-2">{t("colModel")}</TableHead>
                <TableHead className="px-3 py-2">{t("colModality")}</TableHead>
                <TableHead className="px-3 py-2">{t("colProvider")}</TableHead>
                <TableHead className="px-3 py-2">{t("colInput")}</TableHead>
                <TableHead className="px-3 py-2">{t("colOutput")}</TableHead>
                <TableHead className="px-3 py-2">{t("colCache")}</TableHead>
                <TableHead className="px-3 py-2">{t("colUnit")}</TableHead>
                <TableHead className="px-3 py-2">{t("colSource")}</TableHead>
                <TableHead className="px-3 py-2">{t("colValidity")}</TableHead>
                <TableHead className="px-3 py-2 text-right">{t("colActions")}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((p) => {
                const open = p.effective_to === null;
                return (
                  <TableRow key={p.id} data-testid={`price-row-${p.id}`}>
                    <TableCell className="px-3 py-2">{p.provider}</TableCell>
                    <TableCell className="px-3 py-2 font-mono text-xs">{p.model_id}</TableCell>
                    <TableCell className="px-3 py-2">
                      <Badge variant="muted">{p.modality}</Badge>
                    </TableCell>
                    {/* task_11_2_06 — associated platform provider (read). */}
                    <TableCell className="px-3 py-2" data-testid={`price-provider-${p.id}`}>
                      {p.provider_id === null ? (
                        <span className="text-muted-foreground text-xs italic">
                          {t("unlinked")}
                        </span>
                      ) : (
                        <Badge variant="info">
                          {providerLabel.get(p.provider_id) ?? p.provider_id}
                        </Badge>
                      )}
                    </TableCell>
                    <TableCell className="px-3 py-2" data-testid={`price-input-${p.id}`}>
                      {fmtUsd(p.input_price)}
                    </TableCell>
                    <TableCell className="px-3 py-2">{fmtUsd(p.output_price)}</TableCell>
                    <TableCell className="px-3 py-2" data-testid={`price-cached-${p.id}`}>
                      {fmtUsd(p.cached_input_price)}
                    </TableCell>
                    <TableCell className="px-3 py-2 text-xs">
                      {UNIT_KEY[p.unit] ? t(UNIT_KEY[p.unit]) : p.unit}
                    </TableCell>
                    <TableCell className="px-3 py-2">
                      <Badge variant={SOURCE_BADGE[p.source] ?? "muted"}>{p.source}</Badge>
                    </TableCell>
                    <TableCell className="px-3 py-2 text-xs">
                      {open ? (
                        <Badge variant="success" data-testid={`price-current-${p.id}`}>
                          {t("current")}
                        </Badge>
                      ) : (
                        <span className="text-muted-foreground">
                          {fmtDate(p.effective_from)} → {fmtDate(p.effective_to)}
                        </span>
                      )}
                    </TableCell>
                    <TableCell className="px-3 py-2">
                      <div className="flex items-center justify-end gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() =>
                            onHistory({
                              provider: p.provider,
                              model_id: p.model_id,
                              modality: p.modality,
                            })
                          }
                          data-testid={`price-history-${p.id}`}
                          aria-label={t("history")}
                        >
                          <History className="h-3.5 w-3.5" />
                        </Button>
                        <RoleGuard min="system_admin">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onEdit(p)}
                            data-testid={`price-edit-${p.id}`}
                            aria-label={t("edit")}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => supersedeMutation.mutate(p.id)}
                            disabled={!open || supersedeMutation.isPending}
                            data-testid={`price-supersede-${p.id}`}
                            aria-label={t("supersede")}
                          >
                            <Trash2 className="text-destructive h-3.5 w-3.5" />
                          </Button>
                        </RoleGuard>
                      </div>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </div>
      </StateBlock>

      {supersedeMutation.isError ? (
        <p className="text-destructive mt-3 text-xs" data-testid="price-supersede-error">
          {errorText(supersedeMutation.error)}
        </p>
      ) : null}
    </div>
  );
}
