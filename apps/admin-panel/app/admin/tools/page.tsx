"use client";

/**
 * Catálogo de tools navegable — `/admin/tools` (Plan 06.18 task_06_18_11).
 *
 * Resuelve dos quejas concretas del operador:
 *   - "no existe un catálogo": antes solo se veían las tools desde la ficha
 *     de cada agente, sin un sitio donde explorarlas/gestionarlas.
 *   - "tools duplicadas": el alta de custom valida contra el backend
 *     (`POST /tools` devuelve 409 si el nombre colisiona con una built-in o
 *     con otra tool del tenant — task_06_18_04), así que no aparecen dos
 *     filas idénticas.
 *
 * Faceted browse por las TRES facetas de ADR 0049 (Función / Seguridad /
 * Origen) usando los value-sets + resolvers de `@/lib/tools/taxonomy`
 * (fuente única de labels/variants, task_06_18_10) + búsqueda libre. Las
 * built-in son de SOLO LECTURA (badge + sin acciones de editar/borrar); las
 * custom del tenant se crean/editan/borran (admin only, vía <RoleGuard>).
 *
 * Una tool sin motor en el runtime (`is_runtime_wired === false`, ADR 0049)
 * se marca "No disponible aún" para no engañar al operador.
 *
 * Troceada en piezas colocadas (prod-16 `task_prod16_08`): `tool-types.ts`,
 * `tool-facet-select.tsx`, `tool-catalog-rows.tsx` y `tool-dialogs.tsx`.
 * Aquí queda el estado de la pantalla —filtros, búsqueda y qué diálogo está
 * abierto— y nada más.
 */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, Search, Wrench } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { RoleGuard } from "@/components/ui/role-guard";
import { StateBlock } from "@/components/shared/state-block";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import { CATEGORY, IMPL, SECURITY, type ToolCategory } from "@/lib/tools/taxonomy";
import { useCurrentUser } from "@/lib/use-current-user";

import { ToolGroup } from "./tool-catalog-rows";
import { DeleteToolDialog, ToolFormDialog } from "./tool-dialogs";
import { FacetSelect, facetLabel } from "./tool-facet-select";
import { ALL, type CatalogTool } from "./tool-types";

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function ToolsCatalogPage() {
  const { lang } = useLang();
  const t = useT("tools");
  const { isTenantAdmin } = useCurrentUser();

  const catalogQuery = useQuery<CatalogTool[], ApiError>({
    queryKey: ["tools-catalog"],
    queryFn: () => apiFetch<CatalogTool[]>("/tools?limit=500"),
    refetchOnWindowFocus: false,
  });

  // Facet filters (the three ADR-0049 facets) + free-text search.
  const [category, setCategory] = useState<string>(ALL);
  const [security, setSecurity] = useState<string>(ALL);
  const [impl, setImpl] = useState<string>(ALL);
  const [query, setQuery] = useState("");

  // Create / edit dialog state.
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<CatalogTool | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<CatalogTool | null>(null);

  const tools = useMemo(() => catalogQuery.data ?? [], [catalogQuery.data]);

  const q = query.trim().toLowerCase();
  const filtered = useMemo(
    () =>
      tools.filter((t) => {
        if (category !== ALL && t.category !== category) return false;
        if (security !== ALL && t.security_level !== security) return false;
        if (impl !== ALL && t.implementation_type !== impl) return false;
        if (q === "") return true;
        return (
          t.name.toLowerCase().includes(q) ||
          (t.description ?? "").toLowerCase().includes(q) ||
          facetLabel(CATEGORY[t.category as ToolCategory], t.category, lang)
            .toLowerCase()
            .includes(q)
        );
      }),
    [tools, category, security, impl, q, lang],
  );

  const builtins = useMemo(() => filtered.filter((t) => t.is_builtin), [filtered]);
  const custom = useMemo(() => filtered.filter((t) => !t.is_builtin), [filtered]);

  const filtersActive = category !== ALL || security !== ALL || impl !== ALL || q !== "";
  function resetFilters() {
    setCategory(ALL);
    setSecurity(ALL);
    setImpl(ALL);
    setQuery("");
  }

  function openCreate() {
    setEditing(null);
    setDialogOpen(true);
  }
  function openEdit(tool: CatalogTool) {
    setEditing(tool);
    setDialogOpen(true);
  }

  return (
    <div
      className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="tools-catalog-page"
    >
      <PageHeader
        icon={<Wrench className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        actions={
          <RoleGuard min="tenant_admin">
            <Button onClick={openCreate} data-testid="tools-create-button">
              <Plus className="mr-1 h-4 w-4" />
              {t("newTool")}
            </Button>
          </RoleGuard>
        }
      />

      {/* Faceted browse: search + the three ADR-0049 facets. */}
      <div
        className="mb-6 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4"
        data-testid="tools-facets"
      >
        <div className="relative sm:col-span-2 lg:col-span-1">
          <Search
            className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            aria-hidden="true"
          />
          <Input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={t("searchPlaceholder")}
            aria-label={t("searchAriaLabel")}
            className="pl-9"
            data-testid="tools-search"
          />
        </div>

        <FacetSelect
          id="tools-facet-category"
          label={t("facetCategory")}
          allLabel={t("facetAll")}
          value={category}
          onChange={setCategory}
          options={Object.keys(CATEGORY)}
          terms={CATEGORY}
          lang={lang}
          testid="tools-facet-category"
        />
        <FacetSelect
          id="tools-facet-security"
          label={t("facetSecurity")}
          allLabel={t("facetAll")}
          value={security}
          onChange={setSecurity}
          options={Object.keys(SECURITY)}
          terms={SECURITY}
          lang={lang}
          testid="tools-facet-security"
        />
        <FacetSelect
          id="tools-facet-impl"
          label={t("facetImpl")}
          allLabel={t("facetAll")}
          value={impl}
          onChange={setImpl}
          options={Object.keys(IMPL)}
          terms={IMPL}
          lang={lang}
          testid="tools-facet-impl"
        />
      </div>

      <StateBlock
        isLoading={catalogQuery.isLoading}
        loadingSkeleton
        skeletonRows={5}
        loadingTestId="tools-catalog-loading"
        isError={catalogQuery.isError}
        error={catalogQuery.error}
        errorTitle={t("errorTitle")}
        errorTestId="tools-catalog-error"
      >
        {filtered.length === 0 ? (
          <EmptyState
            icon={Wrench}
            title={filtersActive ? t("emptyFiltered") : t("emptyCatalog")}
            description={filtersActive ? t("emptyFilteredHelp") : t("emptyCatalogHelp")}
            action={
              filtersActive ? (
                <Button variant="outline" onClick={resetFilters} data-testid="tools-clear-filters">
                  {t("clearFilters")}
                </Button>
              ) : undefined
            }
            data-testid="tools-catalog-empty"
          />
        ) : (
          <div className="space-y-8">
            <ToolGroup
              title={t("groupBuiltin")}
              hint={t("groupBuiltinHint")}
              tools={builtins}
              lang={lang}
              t={t}
              canEdit={false}
              onEdit={openEdit}
              onDelete={setConfirmDelete}
              testidPrefix="builtin"
            />
            <ToolGroup
              title={t("groupCustom")}
              hint={isTenantAdmin ? t("groupCustomHintEditable") : t("groupCustomHint")}
              tools={custom}
              lang={lang}
              t={t}
              canEdit={isTenantAdmin}
              onEdit={openEdit}
              onDelete={setConfirmDelete}
              testidPrefix="custom"
            />
          </div>
        )}
      </StateBlock>

      {dialogOpen && (
        <ToolFormDialog
          open={dialogOpen}
          onOpenChange={setDialogOpen}
          editing={editing}
          lang={lang}
        />
      )}

      {confirmDelete && (
        <DeleteToolDialog tool={confirmDelete} onClose={() => setConfirmDelete(null)} />
      )}
    </div>
  );
}
