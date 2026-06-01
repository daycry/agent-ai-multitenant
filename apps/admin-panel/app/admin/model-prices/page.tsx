"use client";

/**
 * task_11_14 — Pantalla 'Modelos & Precios' del System Admin.
 *
 * El catálogo global de precios de modelos (Plan 11 Fase C). El catálogo
 * es **platform-global** (sin `tenant_id`), **USD canónico** y soporta
 * **prompt caching** (`cached_input_price`, un precio de lectura de caché
 * típicamente ~10% del input). Esta pantalla deja al System Admin:
 *
 *   - Listar el catálogo con filtros (provider / model_id / modality) y
 *     un toggle "solo vigentes" (`current_only`).
 *   - Crear / editar / superseder (cerrar) precios — SOLO System Admin
 *     (`RoleGuard min="system_admin"`); el backend gatea igualmente con
 *     `require_system_admin` sobre la sesión BYPASSRLS.
 *   - Ver el HISTÓRICO por modelo (filas con vigencia `effective_from` /
 *     `effective_to`) y una gráfica simple de precio-en-el-tiempo
 *     (input/output/cached) en SVG puro — sin añadir una dependencia de
 *     gráficas pesada (recharts no está presente; la nota de la tarea
 *     permite una alternativa ligera, así que renderizamos un sparkline
 *     SVG + tabla en vez de arrastrar una lib nueva).
 *
 * Frontera lectura/escritura (espeja el split del marketplace):
 *   - LECTURAS abiertas a cualquier llamante autenticado (RLS de lectura
 *     global de la migración 0049): GET /model-prices[/current].
 *   - ESCRITURAS solo System Admin: POST/PATCH/DELETE /admin/model-prices.
 *
 * USD-canónico: ningún campo de moneda en el wire — el catálogo es
 * USD-only. La conversión a moneda del tenant (exchange_rates /
 * Organization.display_currency) queda fuera del alcance numerado.
 *
 * Endpoints backend (routers/model_prices.py):
 *   GET    /model-prices                 — list (provider/model_id/modality/current_only + limit/offset)
 *   GET    /model-prices/current         — precio vigente de una clave
 *   GET    /model-prices/{id}            — una fila
 *   POST   /admin/model-prices          — crear periodo vigente (System Admin)
 *   PATCH  /admin/model-prices/{id}     — editar campos mutables (System Admin)
 *   DELETE /admin/model-prices/{id}     — superseder (cerrar) periodo (System Admin)
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Coins, History, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { StateBlock } from "@/components/shared/state-block";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
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
import { RoleGuard } from "@/components/ui/role-guard";
import { Select } from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { ApiError, apiFetch } from "@/lib/api";
import { useCurrentUser } from "@/lib/use-current-user";

// ---------------------------------------------------------------------------
// Types — mirror api_server.schemas.model_prices + db.model_prices enums.
// USD-canonical: `currency` is always "USD"; there is no currency knob.
// ---------------------------------------------------------------------------
type Modality = "text" | "vision" | "audio" | "embedding" | "image" | "rerank";
type Unit = "per_1m_tokens" | "per_1k_tokens";
type Source = "litellm" | "provider_api" | "manual";

interface ModelPrice {
  id: string;
  provider: string;
  model_id: string;
  modality: string;
  input_price: string;
  output_price: string;
  cached_input_price: string | null;
  unit: string;
  currency: string;
  context_window: number | null;
  source: string;
  // task_11_2_06 — association to a configured platform provider
  // (llm_providers.id). NULL when the price is not associated.
  provider_id: string | null;
  effective_from: string;
  effective_to: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
}

// task_11_2_06 — the platform-global LLM providers (ADR 0028). Read from
// the System-Admin /admin/llm-providers surface only to map provider_id ->
// a human label + populate the "filter by provider" dropdown. Mirrors
// api_server.schemas.llm_providers.LLMProviderResponse (secret-free).
interface LlmProvider {
  id: string;
  kind: string;
  display_name: string;
  is_active: boolean;
}

// task_11_16 — dry-run diff + mandatory-confirmation apply.
// Mirror api_server.schemas.price_sync.{PriceSyncDiffResponse,PriceDiffRowResponse}.
type DiffStatus = "added" | "updated" | "unchanged" | "increased" | "removed";

interface PriceDiffRow {
  provider: string;
  model_id: string;
  modality: string;
  status: DiffStatus;
  source: string;
  old_input: string | null;
  new_input: string | null;
  old_output: string | null;
  new_output: string | null;
  old_cached_input: string | null;
  new_cached_input: string | null;
  input_pct: number | null;
  output_pct: number | null;
  manual_skipped: boolean;
}

interface PriceSyncDiff {
  fetched: number;
  added: number;
  updated: number;
  unchanged: number;
  increased: number;
  removed: number;
  has_large_increase: boolean;
  rows: PriceDiffRow[];
  skipped: { model_key: string; reason: string }[];
}

interface PriceSyncResult {
  fetched: number;
  created: number;
  updated: number;
  unchanged: number;
  changed: number;
}

const MODALITIES: Modality[] = ["text", "vision", "audio", "embedding", "image", "rerank"];
const UNITS: Unit[] = ["per_1m_tokens", "per_1k_tokens"];
const SOURCES: Source[] = ["manual", "litellm", "provider_api"];

const DIFF_STATUS_BADGE: Record<DiffStatus, BadgeVariant> = {
  added: "info",
  updated: "primary",
  unchanged: "muted",
  increased: "danger",
  removed: "warning",
};

const DIFF_STATUS_LABEL: Record<DiffStatus, string> = {
  added: "nuevo",
  updated: "actualizado",
  unchanged: "sin cambios",
  increased: "subida >10%",
  removed: "descontinuado",
};

/** Format a fractional change (0.067 -> "+6.7%"); null -> "—". */
function fmtPct(value: number | null): string {
  if (value === null) return "—";
  const pct = value * 100;
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toLocaleString("en-US", { maximumFractionDigits: 1 })}%`;
}

const UNIT_LABEL: Record<string, string> = {
  per_1m_tokens: "por 1M tokens",
  per_1k_tokens: "por 1K tokens",
};

const SOURCE_BADGE: Record<string, BadgeVariant> = {
  manual: "primary",
  litellm: "info",
  provider_api: "success",
};

function errorText(err: unknown): string {
  return err instanceof ApiError ? err.body : String(err);
}

/** Canonical USD price formatting; the catalog stores `Numeric(18,10)` strings. */
function fmtUsd(value: string | null): string {
  if (value === null) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return value;
  // Up to 6 significant decimals; trim trailing zeros for readability.
  return `$${n.toLocaleString("en-US", { maximumFractionDigits: 6 })}`;
}

function fmtDate(iso: string | null): string {
  if (iso === null) return "—";
  return new Date(iso).toLocaleDateString("es-ES", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

// ===========================================================================
// Page
// ===========================================================================
export default function ModelPricesPage() {
  const queryClient = useQueryClient();
  const { isSystemAdmin } = useCurrentUser();

  const [provider, setProvider] = useState("");
  const [modelId, setModelId] = useState("");
  const [modality, setModality] = useState<"" | Modality>("");
  const [providerId, setProviderId] = useState("");
  const [currentOnly, setCurrentOnly] = useState(true);

  // Effective filter values (applied on submit so typing doesn't refetch
  // on every keystroke).
  const [applied, setApplied] = useState<{
    provider: string;
    modelId: string;
    modality: "" | Modality;
    providerId: string;
    currentOnly: boolean;
  }>({ provider: "", modelId: "", modality: "", providerId: "", currentOnly: true });

  // task_11_2_06 — platform providers, read only when the viewer is a
  // System Admin (the /admin/llm-providers surface is System-Admin only;
  // a tenant user that can read the catalog must not 403 here). Used to map
  // provider_id -> a label and to populate the "filter by provider" select.
  const providersQuery = useQuery({
    queryKey: ["llm-providers", "for-prices"],
    queryFn: () => apiFetch<LlmProvider[]>("/admin/llm-providers"),
    enabled: isSystemAdmin,
    refetchOnWindowFocus: false,
    retry: false,
  });

  const providers = useMemo(() => providersQuery.data ?? [], [providersQuery.data]);
  const providerLabel = useMemo(() => {
    const map = new Map<string, string>();
    for (const p of providers) map.set(p.id, p.display_name);
    return map;
  }, [providers]);

  const [createOpen, setCreateOpen] = useState(false);
  const [syncOpen, setSyncOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<ModelPrice | null>(null);
  const [historyTarget, setHistoryTarget] = useState<{
    provider: string;
    model_id: string;
    modality: string;
  } | null>(null);

  const queryKey = useMemo(() => ["model-prices", applied] as const, [applied]);

  const listQuery = useQuery({
    queryKey,
    queryFn: () => {
      const params = new URLSearchParams();
      if (applied.provider) params.set("provider", applied.provider);
      if (applied.modelId) params.set("model_id", applied.modelId);
      if (applied.modality) params.set("modality", applied.modality);
      if (applied.providerId) params.set("provider_id", applied.providerId);
      if (applied.currentOnly) params.set("current_only", "true");
      params.set("limit", "200");
      return apiFetch<ModelPrice[]>(`/model-prices?${params.toString()}`);
    },
    refetchOnWindowFocus: false,
  });

  const supersedeMutation = useMutation({
    mutationFn: (id: string) =>
      apiFetch<ModelPrice>(`/admin/model-prices/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["model-prices"] });
    },
  });

  function applyFilters() {
    setApplied({
      provider: provider.trim(),
      modelId: modelId.trim(),
      modality,
      providerId,
      currentOnly,
    });
  }

  function resetFilters() {
    setProvider("");
    setModelId("");
    setModality("");
    setProviderId("");
    setCurrentOnly(true);
    setApplied({ provider: "", modelId: "", modality: "", providerId: "", currentOnly: true });
  }

  const rows = listQuery.data ?? [];

  return (
    <div
      className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="model-prices-page"
    >
      <PageHeader
        icon={<Coins className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Modelos & Precios"
        description="Catálogo global de precios de modelos (USD canónico, con soporte de prompt caching). Lectura abierta; edición solo System Admin."
        data-testid="model-prices-header"
        actions={
          <RoleGuard min="system_admin">
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => setSyncOpen(true)}
                data-testid="price-sync-open"
              >
                <RefreshCw className="mr-1 h-3.5 w-3.5" />
                Sincronizar precios
              </Button>
              <Button size="sm" onClick={() => setCreateOpen(true)} data-testid="price-create-open">
                <Plus className="mr-1 h-3.5 w-3.5" />
                Nuevo precio
              </Button>
            </div>
          </RoleGuard>
        }
      />

      {/* ---------------------------------------------------------------- */}
      {/* Filters */}
      {/* ---------------------------------------------------------------- */}
      <Card className="mt-6" data-testid="price-filters">
        <CardContent className="grid grid-cols-1 gap-3 pt-5 sm:grid-cols-2 lg:grid-cols-6 lg:items-end">
          <div className="space-y-1">
            <Label htmlFor="filter-provider">Familia (provider)</Label>
            <Input
              id="filter-provider"
              placeholder="anthropic"
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              data-testid="filter-provider"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="filter-model">Modelo</Label>
            <Input
              id="filter-model"
              placeholder="claude-sonnet-4-5"
              value={modelId}
              onChange={(e) => setModelId(e.target.value)}
              data-testid="filter-model"
            />
          </div>
          <div className="space-y-1">
            <Label htmlFor="filter-modality">Modalidad</Label>
            <Select
              id="filter-modality"
              value={modality}
              onChange={(e) => setModality(e.target.value as "" | Modality)}
              data-testid="filter-modality"
            >
              <option value="">Todas</option>
              {MODALITIES.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </Select>
          </div>
          {/* task_11_2_06 — filter by associated platform provider. Only
              the System Admin can read the providers list, so this select
              is shown to them; a tenant reader still has the other filters. */}
          {isSystemAdmin ? (
            <div className="space-y-1">
              <Label htmlFor="filter-provider-id">Proveedor (plataforma)</Label>
              <Select
                id="filter-provider-id"
                value={providerId}
                onChange={(e) => setProviderId(e.target.value)}
                data-testid="filter-provider-id"
              >
                <option value="">Todos</option>
                {providers.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.display_name}
                  </option>
                ))}
              </Select>
            </div>
          ) : null}
          <div className="flex items-center gap-2 pb-2">
            <Checkbox
              id="filter-current-only"
              checked={currentOnly}
              onChange={(e) => setCurrentOnly(e.target.checked)}
              data-testid="filter-current-only"
            />
            <Label htmlFor="filter-current-only">Solo vigentes</Label>
          </div>
          <div className="flex items-center gap-2">
            <Button size="sm" onClick={applyFilters} data-testid="filter-apply">
              Filtrar
            </Button>
            <Button size="sm" variant="outline" onClick={resetFilters} data-testid="filter-reset">
              Limpiar
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* ---------------------------------------------------------------- */}
      {/* List */}
      {/* ---------------------------------------------------------------- */}
      <div className="mt-6">
        <StateBlock
          isLoading={listQuery.isLoading}
          isError={listQuery.isError}
          error={listQuery.error}
          isEmpty={rows.length === 0}
          loadingLabel="Cargando catálogo…"
          loadingTestId="prices-loading"
          errorTitle="No se pudo cargar el catálogo"
          errorTestId="prices-error"
          emptyIcon={Coins}
          emptyTitle="Catálogo vacío"
          emptyDescription="El catálogo está vacío para estos filtros."
          emptyTestId="prices-empty"
        >
          <div className="overflow-hidden rounded-xl border">
            <Table data-testid="prices-table" className="text-sm">
              <TableHeader className="bg-muted normal-case">
                <TableRow>
                  <TableHead className="px-3 py-2">Familia</TableHead>
                  <TableHead className="px-3 py-2">Modelo</TableHead>
                  <TableHead className="px-3 py-2">Modalidad</TableHead>
                  <TableHead className="px-3 py-2">Proveedor</TableHead>
                  <TableHead className="px-3 py-2">Input</TableHead>
                  <TableHead className="px-3 py-2">Output</TableHead>
                  <TableHead className="px-3 py-2">Cache</TableHead>
                  <TableHead className="px-3 py-2">Unidad</TableHead>
                  <TableHead className="px-3 py-2">Fuente</TableHead>
                  <TableHead className="px-3 py-2">Vigencia</TableHead>
                  <TableHead className="px-3 py-2 text-right">Acciones</TableHead>
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
                          <span className="text-muted-foreground text-xs italic">sin asociar</span>
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
                        {UNIT_LABEL[p.unit] ?? p.unit}
                      </TableCell>
                      <TableCell className="px-3 py-2">
                        <Badge variant={SOURCE_BADGE[p.source] ?? "muted"}>{p.source}</Badge>
                      </TableCell>
                      <TableCell className="px-3 py-2 text-xs">
                        {open ? (
                          <Badge variant="success" data-testid={`price-current-${p.id}`}>
                            vigente
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
                              setHistoryTarget({
                                provider: p.provider,
                                model_id: p.model_id,
                                modality: p.modality,
                              })
                            }
                            data-testid={`price-history-${p.id}`}
                            aria-label="Histórico"
                          >
                            <History className="h-3.5 w-3.5" />
                          </Button>
                          <RoleGuard min="system_admin">
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => setEditTarget(p)}
                              data-testid={`price-edit-${p.id}`}
                              aria-label="Editar"
                            >
                              <Pencil className="h-3.5 w-3.5" />
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => supersedeMutation.mutate(p.id)}
                              disabled={!open || supersedeMutation.isPending}
                              data-testid={`price-supersede-${p.id}`}
                              aria-label="Superseder"
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

      {createOpen ? (
        <PriceFormDialog
          mode="create"
          onClose={() => setCreateOpen(false)}
          onSaved={() => {
            setCreateOpen(false);
            void queryClient.invalidateQueries({ queryKey: ["model-prices"] });
          }}
        />
      ) : null}

      {editTarget ? (
        <PriceFormDialog
          mode="edit"
          price={editTarget}
          onClose={() => setEditTarget(null)}
          onSaved={() => {
            setEditTarget(null);
            void queryClient.invalidateQueries({ queryKey: ["model-prices"] });
          }}
        />
      ) : null}

      {historyTarget ? (
        <PriceHistoryDialog target={historyTarget} onClose={() => setHistoryTarget(null)} />
      ) : null}

      {syncOpen ? (
        <SyncDiffDialog
          onClose={() => setSyncOpen(false)}
          onApplied={() => {
            setSyncOpen(false);
            void queryClient.invalidateQueries({ queryKey: ["model-prices"] });
          }}
        />
      ) : null}
    </div>
  );
}

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
}

function SyncDiffDialog({ onClose, onApplied }: SyncDiffDialogProps) {
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

function PriceFormDialog({ mode, price, onClose, onSaved }: PriceFormDialogProps) {
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

function PriceHistoryDialog({ target, onClose }: PriceHistoryDialogProps) {
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
