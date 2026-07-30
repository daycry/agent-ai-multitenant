"use client";

// Dialogs del catálogo Modelos & Precios (tramo #9, extracción verbatim del
// monolito page.tsx — auditoría 2026-07-10): dry-run del sync LiteLLM con gate
// de confirmación >10%, form crear/editar USD-canónico e histórico por modelo
// con sparkline SVG puro (sin dependencia de gráficas). PriceSparkline queda
// privado del módulo (solo lo usa el histórico).

import { useMemo, useState } from "react";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { apiFetch } from "@/lib/api";
import { useErrorText } from "@/lib/use-error-text";

import {
  DIFF_STATUS_BADGE,
  DIFF_STATUS_LABEL,
  MODALITIES,
  SKIP_FAMILY_NOT_ACTIVE,
  SOURCE_BADGE,
  SOURCES,
  UNIT_LABEL,
  UNITS,
  fmtDate,
  fmtPct,
  fmtUsd,
  type Modality,
  type ModelPrice,
  type PriceSyncDiff,
  type PriceSyncResult,
  type Source,
  type Unit,
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
          <DialogTitle>Sincronizar precios (LiteLLM)</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <p className="text-muted-foreground text-xs" data-testid="sync-feed-note">
            Lee el JSON público de precios de LiteLLM como fuente de datos (no como runtime — ADR
            0021). Esta es una previsualización: nada se escribe hasta que confirmes. Una subida de
            precio &gt;10% exige confirmación explícita.
          </p>

          {/* plan price-sync-active-providers (task_psa_02) — the sync is scoped
              to the families of the ACTIVE providers (ADR 0028). With none
              active, the apply imports nothing. */}
          {syncFamilies.length > 0 ? (
            <p className="text-muted-foreground mt-2 text-xs" data-testid="sync-dialog-scope">
              Sincronizando solo:{" "}
              <span className="text-foreground font-medium">{syncFamilies.join(", ")}</span>{" "}
              (familias de los proveedores LLM activos). El resto del feed se omite.
            </p>
          ) : (
            <p className="text-warning mt-2 text-xs" data-testid="sync-dialog-scope-empty">
              No hay proveedores LLM activos; el sync no traerá nada.
            </p>
          )}

          {diffQuery.isLoading ? (
            <p className="text-muted-foreground mt-3 text-sm" data-testid="sync-loading">
              Calculando diff…
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
                <Badge variant="info">{diff.added} nuevos</Badge>
                <Badge variant="primary">{diff.updated} actualizados</Badge>
                <Badge variant="danger">{diff.increased} subidas &gt;10%</Badge>
                <Badge variant="warning">{diff.removed} descontinuados</Badge>
                <Badge variant="muted">{diff.unchanged} sin cambios</Badge>
                {/* plan price-sync-active-providers (task_psa_02) — feed entries
                    dropped because their family is not an active provider. */}
                {familyNotActiveSkipped > 0 ? (
                  <Badge variant="muted" data-testid="sync-skipped-family">
                    {familyNotActiveSkipped} fuera de familias activas
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
                        <th className="px-3 py-2 font-medium">Modelo</th>
                        <th className="px-3 py-2 font-medium">Estado</th>
                        <th className="px-3 py-2 font-medium">Input (ant. → nuevo)</th>
                        <th className="px-3 py-2 font-medium">Output (ant. → nuevo)</th>
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
                                  {DIFF_STATUS_LABEL[r.status]}
                                </Badge>
                                {r.manual_skipped ? (
                                  <span
                                    className="text-muted-foreground ml-1 text-xs italic"
                                    data-testid={`sync-manual-${r.model_id}`}
                                  >
                                    (manual, no se pisa)
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
                  El catálogo ya está al día — nada que aplicar.
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
                      Hay {diff.increased} subida(s) de precio superior(es) al 10%. Revisa los
                      cambios y confirma explícitamente para aplicarlos.
                    </p>
                    <label className="flex items-center gap-2 text-xs">
                      <Checkbox
                        checked={confirmed}
                        onChange={(e) => setConfirmed(e.target.checked)}
                        data-testid="sync-confirm-checkbox"
                      />
                      Confirmo que he revisado las subidas &gt;10% y deseo aplicarlas.
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
            Cancelar
          </Button>
          <Button
            onClick={() => applyMutation.mutate(needsConfirm && confirmed)}
            disabled={!canApply || applyMutation.isPending}
            data-testid="sync-apply"
          >
            {applyMutation.isPending ? "Aplicando…" : "Aplicar cambios"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ===========================================================================
// Create / edit dialog (System Admin only — wrapped at the call site too)
// ===========================================================================
interface PriceFormDialogProps {
  mode: "create" | "edit";
  price?: ModelPrice;
  onClose: () => void;
  onSaved: () => void;
}

export function PriceFormDialog({ mode, price, onClose, onSaved }: PriceFormDialogProps) {
  const errorText = useErrorText();
  const isEdit = mode === "edit";

  // The catalog key (provider/model_id/modality) is immutable on edit.
  const [provider, setProvider] = useState(price?.provider ?? "");
  const [modelId, setModelId] = useState(price?.model_id ?? "");
  const [modality, setModality] = useState<Modality>((price?.modality as Modality) ?? "text");
  const [inputPrice, setInputPrice] = useState(price?.input_price ?? "");
  const [outputPrice, setOutputPrice] = useState(price?.output_price ?? "");
  const [cachedInputPrice, setCachedInputPrice] = useState(price?.cached_input_price ?? "");
  const [unit, setUnit] = useState<Unit>((price?.unit as Unit) ?? "per_1m_tokens");
  const [contextWindow, setContextWindow] = useState(
    price?.context_window != null ? String(price.context_window) : "",
  );
  const [source, setSource] = useState<Source>((price?.source as Source) ?? "manual");

  const saveMutation = useMutation({
    mutationFn: () => {
      if (isEdit && price) {
        // PATCH only the mutable fields. The key is immutable.
        const body: Record<string, unknown> = {
          input_price: inputPrice,
          output_price: outputPrice,
          cached_input_price: cachedInputPrice.trim() === "" ? null : cachedInputPrice,
          unit,
          context_window: contextWindow.trim() === "" ? null : Number(contextWindow),
          source,
        };
        return apiFetch<ModelPrice>(`/admin/model-prices/${price.id}`, {
          method: "PATCH",
          body,
        });
      }
      // USD-canonical: no `currency` on the wire.
      const body: Record<string, unknown> = {
        provider: provider.trim(),
        model_id: modelId.trim(),
        modality,
        input_price: inputPrice,
        output_price: outputPrice,
        cached_input_price: cachedInputPrice.trim() === "" ? null : cachedInputPrice,
        unit,
        context_window: contextWindow.trim() === "" ? null : Number(contextWindow),
        source,
      };
      return apiFetch<ModelPrice>("/admin/model-prices", { method: "POST", body });
    },
    onSuccess: onSaved,
  });

  const canSave =
    inputPrice.trim() !== "" &&
    outputPrice.trim() !== "" &&
    (isEdit || (provider.trim() !== "" && modelId.trim() !== ""));

  return (
    <Dialog open onOpenChange={(next) => (next ? undefined : onClose())} size="lg">
      <DialogContent data-testid="price-form-dialog">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Editar precio" : "Nuevo precio"}</DialogTitle>
        </DialogHeader>
        <DialogBody>
          <p className="text-muted-foreground text-xs" data-testid="price-form-usd-note">
            Precios en USD canónico, {UNIT_LABEL[unit] ?? unit}. El precio de caché (prompt caching)
            es opcional; si se omite, el sistema usa ~10% del input.
          </p>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="space-y-1">
              <Label htmlFor="form-provider">Provider</Label>
              <Input
                id="form-provider"
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
                disabled={isEdit}
                placeholder="anthropic"
                data-testid="form-provider"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-model">Modelo</Label>
              <Input
                id="form-model"
                value={modelId}
                onChange={(e) => setModelId(e.target.value)}
                disabled={isEdit}
                placeholder="claude-sonnet-4-5"
                data-testid="form-model"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-modality">Modalidad</Label>
              <Select
                id="form-modality"
                value={modality}
                onChange={(e) => setModality(e.target.value as Modality)}
                disabled={isEdit}
                data-testid="form-modality"
              >
                {MODALITIES.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="space-y-1">
              <Label htmlFor="form-input">Input (USD)</Label>
              <Input
                id="form-input"
                type="number"
                min={0}
                step="any"
                value={inputPrice}
                onChange={(e) => setInputPrice(e.target.value)}
                data-testid="form-input-price"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-output">Output (USD)</Label>
              <Input
                id="form-output"
                type="number"
                min={0}
                step="any"
                value={outputPrice}
                onChange={(e) => setOutputPrice(e.target.value)}
                data-testid="form-output-price"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-cached">Cache input (USD, opcional)</Label>
              <Input
                id="form-cached"
                type="number"
                min={0}
                step="any"
                value={cachedInputPrice}
                onChange={(e) => setCachedInputPrice(e.target.value)}
                placeholder="~10% input"
                data-testid="form-cached-price"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="space-y-1">
              <Label htmlFor="form-unit">Unidad</Label>
              <Select
                id="form-unit"
                value={unit}
                onChange={(e) => setUnit(e.target.value as Unit)}
                data-testid="form-unit"
              >
                {UNITS.map((u) => (
                  <option key={u} value={u}>
                    {UNIT_LABEL[u]}
                  </option>
                ))}
              </Select>
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-context">Context window</Label>
              <Input
                id="form-context"
                type="number"
                min={1}
                step={1}
                value={contextWindow}
                onChange={(e) => setContextWindow(e.target.value)}
                placeholder="200000"
                data-testid="form-context-window"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="form-source">Fuente</Label>
              <Select
                id="form-source"
                value={source}
                onChange={(e) => setSource(e.target.value as Source)}
                data-testid="form-source"
              >
                {SOURCES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          {saveMutation.isError ? (
            <p className="text-destructive text-xs" data-testid="price-form-error">
              {errorText(saveMutation.error)}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="price-form-cancel">
            Cancelar
          </Button>
          <Button
            onClick={() => saveMutation.mutate()}
            disabled={!canSave || saveMutation.isPending}
            data-testid="price-form-submit"
          >
            {saveMutation.isPending ? "Guardando…" : isEdit ? "Guardar" : "Crear"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ===========================================================================
// Price history dialog — effective-dated rows + price-over-time chart
// ===========================================================================
interface PriceHistoryDialogProps {
  target: { provider: string; model_id: string; modality: string };
  onClose: () => void;
}

export function PriceHistoryDialog({ target, onClose }: PriceHistoryDialogProps) {
  const errorText = useErrorText();
  const historyQuery = useQuery({
    queryKey: ["model-prices", "history", target],
    queryFn: () => {
      const params = new URLSearchParams({
        provider: target.provider,
        model_id: target.model_id,
        modality: target.modality,
        limit: "200",
      });
      return apiFetch<ModelPrice[]>(`/model-prices?${params.toString()}`);
    },
    refetchOnWindowFocus: false,
  });

  // Oldest → newest for the timeline / chart.
  const series = useMemo(() => {
    const rows = historyQuery.data ?? [];
    return [...rows].sort(
      (a, b) => new Date(a.effective_from).getTime() - new Date(b.effective_from).getTime(),
    );
  }, [historyQuery.data]);

  return (
    <Dialog open onOpenChange={(next) => (next ? undefined : onClose())} size="2xl">
      <DialogContent data-testid="price-history-dialog">
        <DialogHeader>
          <DialogTitle>
            Histórico — {target.provider} / {target.model_id} ({target.modality})
          </DialogTitle>
        </DialogHeader>
        <DialogBody>
          {historyQuery.isLoading ? (
            <p className="text-muted-foreground text-sm" data-testid="history-loading">
              Cargando histórico…
            </p>
          ) : historyQuery.isError ? (
            <p className="text-destructive text-sm" data-testid="history-error">
              {errorText(historyQuery.error)}
            </p>
          ) : series.length === 0 ? (
            <p className="text-muted-foreground text-sm italic" data-testid="history-empty">
              Sin historial de precios para esta clave.
            </p>
          ) : (
            <>
              <PriceSparkline series={series} />
              <div className="overflow-x-auto rounded-lg border" data-testid="history-table">
                <table className="w-full text-sm">
                  <thead className="bg-muted text-muted-foreground">
                    <tr className="text-left">
                      <th className="px-3 py-2 font-medium">Desde</th>
                      <th className="px-3 py-2 font-medium">Hasta</th>
                      <th className="px-3 py-2 font-medium">Input</th>
                      <th className="px-3 py-2 font-medium">Output</th>
                      <th className="px-3 py-2 font-medium">Cache</th>
                      <th className="px-3 py-2 font-medium">Fuente</th>
                    </tr>
                  </thead>
                  <tbody>
                    {series.map((p) => (
                      <tr key={p.id} className="border-t" data-testid={`history-row-${p.id}`}>
                        <td className="px-3 py-2 text-xs">{fmtDate(p.effective_from)}</td>
                        <td className="px-3 py-2 text-xs">
                          {p.effective_to === null ? (
                            <Badge variant="success">vigente</Badge>
                          ) : (
                            fmtDate(p.effective_to)
                          )}
                        </td>
                        <td className="px-3 py-2">{fmtUsd(p.input_price)}</td>
                        <td className="px-3 py-2">{fmtUsd(p.output_price)}</td>
                        <td className="px-3 py-2">{fmtUsd(p.cached_input_price)}</td>
                        <td className="px-3 py-2">
                          <Badge variant={SOURCE_BADGE[p.source] ?? "muted"}>{p.source}</Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="history-close">
            Cerrar
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Lightweight price-over-time chart in pure SVG.
//
// No chart dependency is present (recharts is not installed). Rather than
// pull in a heavy dep just for this, we render input/output price polylines
// on a small SVG canvas — the task explicitly allows a simple alternative.
// ---------------------------------------------------------------------------
function PriceSparkline({ series }: { series: ModelPrice[] }) {
  const W = 640;
  const H = 160;
  const PAD = 24;

  const points = series.map((p, i) => ({
    i,
    input: Number(p.input_price),
    output: Number(p.output_price),
  }));

  const allValues = points.flatMap((p) => [p.input, p.output]).filter((v) => !Number.isNaN(v));
  const max = allValues.length > 0 ? Math.max(...allValues) : 1;
  const min = allValues.length > 0 ? Math.min(...allValues) : 0;
  const span = max - min || 1;
  const n = points.length;

  const x = (i: number) => (n <= 1 ? W / 2 : PAD + (i * (W - 2 * PAD)) / (n - 1));
  const y = (v: number) => H - PAD - ((v - min) / span) * (H - 2 * PAD);

  const toPath = (key: "input" | "output") =>
    points
      .map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`)
      .join(" ");

  return (
    <figure className="rounded-lg border p-3" data-testid="price-chart">
      <figcaption className="text-muted-foreground mb-2 flex items-center gap-4 text-xs">
        <span className="flex items-center gap-1">
          <span className="bg-primary inline-block h-2 w-3 rounded" /> Input
        </span>
        <span className="flex items-center gap-1">
          <span className="inline-block h-2 w-3 rounded bg-emerald-500" /> Output
        </span>
        <span>(USD, precio-en-el-tiempo)</span>
      </figcaption>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-40 w-full"
        role="img"
        aria-label="Gráfica de precio en el tiempo"
        preserveAspectRatio="none"
      >
        <path d={toPath("input")} fill="none" stroke="hsl(var(--primary))" strokeWidth={2} />
        <path d={toPath("output")} fill="none" stroke="rgb(16 185 129)" strokeWidth={2} />
        {points.map((p) => (
          <g key={p.i}>
            <circle cx={x(p.i)} cy={y(p.input)} r={2.5} fill="hsl(var(--primary))" />
            <circle cx={x(p.i)} cy={y(p.output)} r={2.5} fill="rgb(16 185 129)" />
          </g>
        ))}
      </svg>
    </figure>
  );
}
