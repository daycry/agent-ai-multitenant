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
 *
 * **Partición** (prod-16 `task_prod16_06`): esta pantalla tenía 514 líneas y el
 * objetivo del plan son < 400, con ninguna sección por encima de 500. Los
 * filtros viven ahora en `price-filters.tsx` y la tabla en `price-table.tsx`;
 * los tres diálogos, que compartían un solo fichero de 686 líneas, están en
 * `sync-diff-dialog.tsx`, `price-form-dialog.tsx` y `price-history-dialog.tsx`.
 * Aquí quedan la cabecera, el aviso de alcance del sync y el cableado.
 */

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Coins, Info, Plus, RefreshCw } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { RoleGuard } from "@/components/ui/role-guard";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useCurrentUser } from "@/lib/use-current-user";

import { activeFamilies, type LlmProvider, type ModelPrice } from "./model-price-types";
import { EMPTY_FILTERS, PriceFilters, type AppliedPriceFilters } from "./price-filters";
import { PriceFormDialog } from "./price-form-dialog";
import { PriceHistoryDialog } from "./price-history-dialog";
import { PriceTable, type HistoryTarget } from "./price-table";
import { SyncDiffDialog } from "./sync-diff-dialog";

// ===========================================================================
// Page
// ===========================================================================
export default function ModelPricesPage() {
  const t = useT("modelPrices");
  const queryClient = useQueryClient();
  const { isSystemAdmin } = useCurrentUser();

  // Sólo los filtros YA aplicados: el estado del formulario vive en
  // `PriceFilters` para que teclear no refetchee en cada pulsación.
  const [applied, setApplied] = useState<AppliedPriceFilters>(EMPTY_FILTERS);

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
  const [historyTarget, setHistoryTarget] = useState<HistoryTarget | null>(null);

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

  const rows = listQuery.data ?? [];

  return (
    <div
      className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="model-prices-page"
    >
      <PageHeader
        icon={<Coins className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
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
                {t("syncOpen")}
              </Button>
              <Button size="sm" onClick={() => setCreateOpen(true)} data-testid="price-create-open">
                <Plus className="mr-1 h-3.5 w-3.5" />
                {t("create")}
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
              {t("scopeLead")}{" "}
              <span className="text-foreground font-medium" data-testid="sync-scope-families">
                {syncFamilies.join(", ")}
              </span>{" "}
              <span className="text-xs">{t("scopeTail")}</span>
            </p>
          </div>
        ) : (
          <div
            className="border-warning/40 bg-warning/5 mt-6 flex items-start gap-2 rounded-lg border p-3 text-sm"
            data-testid="sync-scope-empty"
          >
            <AlertTriangle className="text-warning mt-0.5 h-4 w-4 shrink-0" />
            <p className="text-foreground">
              {t("scopeEmptyLead")}{" "}
              <Link href="/admin/llm-providers" className="text-primary underline">
                /admin/llm-providers
              </Link>{" "}
              {t("scopeEmptyTail")}
            </p>
          </div>
        )
      ) : null}

      <PriceFilters providers={providers} isSystemAdmin={isSystemAdmin} onApply={setApplied} />

      <PriceTable
        rows={rows}
        providerLabel={providerLabel}
        isLoading={listQuery.isLoading}
        isError={listQuery.isError}
        error={listQuery.error}
        onHistory={setHistoryTarget}
        onEdit={setEditTarget}
      />

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
