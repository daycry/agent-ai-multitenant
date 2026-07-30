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
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Coins, History, Info, Pencil, Plus, RefreshCw, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { StateBlock } from "@/components/shared/state-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
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
import { apiFetch } from "@/lib/api";
import { useCurrentUser } from "@/lib/use-current-user";
import { useErrorText } from "@/lib/use-error-text";

import { PriceFormDialog, PriceHistoryDialog, SyncDiffDialog } from "./model-price-dialogs";
import {
  MODALITIES,
  SOURCE_BADGE,
  UNIT_LABEL,
  activeFamilies,
  fmtDate,
  fmtUsd,
  type LlmProvider,
  type Modality,
  type ModelPrice,
} from "./model-price-types";

// ===========================================================================
// Page
// ===========================================================================
export default function ModelPricesPage() {
  const errorText = useErrorText();
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

  // plan price-sync-active-providers (task_psa_02) — the LiteLLM families the
  // sync will actually import, derived from the ACTIVE providers (ADR 0028 map).
  // An empty set means no active provider → the sync imports nothing.
  const syncFamilies = useMemo(() => activeFamilies(providers), [providers]);
  const hasActiveProviders = syncFamilies.length > 0;

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
      {/* Sync scope notice — plan price-sync-active-providers (task_psa_02) */}
      {/* The price sync ONLY imports the LiteLLM families of the ACTIVE     */}
      {/* providers (ADR 0028). With no active provider there is nothing to  */}
      {/* sync. System-Admin only (the providers list is System-Admin only;  */}
      {/* a tenant reader never sees this and cannot trigger the sync).      */}
      {/* ---------------------------------------------------------------- */}
      {isSystemAdmin && !providersQuery.isLoading ? (
        hasActiveProviders ? (
          <div
            className="border-border bg-muted/40 text-muted-foreground mt-6 flex items-start gap-2 rounded-lg border p-3 text-sm"
            data-testid="sync-scope-notice"
          >
            <Info className="text-primary mt-0.5 h-4 w-4 shrink-0" />
            <p>
              Sincronizando solo:{" "}
              <span className="text-foreground font-medium" data-testid="sync-scope-families">
                {syncFamilies.join(", ")}
              </span>{" "}
              <span className="text-xs">
                (familias de los proveedores LLM activos — ADR 0028). El resto del feed se omite.
              </span>
            </p>
          </div>
        ) : (
          <div
            className="border-warning/40 bg-warning/5 mt-6 flex items-start gap-2 rounded-lg border p-3 text-sm"
            data-testid="sync-scope-empty"
          >
            <AlertTriangle className="text-warning mt-0.5 h-4 w-4 shrink-0" />
            <p className="text-foreground">
              No hay proveedores LLM activos; nada que sincronizar. Activa al menos un proveedor en{" "}
              <Link href="/admin/llm-providers" className="text-primary underline">
                /admin/llm-providers
              </Link>{" "}
              para que el sync de precios traiga sus familias.
            </p>
          </div>
        )
      ) : null}

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
          syncFamilies={syncFamilies}
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
