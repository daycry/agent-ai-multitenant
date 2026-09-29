"use client";

/** Pestaña «Tools» de `/admin/tools`: el catálogo faceteado (ver `page.tsx`). */

import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Plus, Wrench } from "lucide-react";

import { ResourceHeader, ResourceLayout, ResourceToolbar } from "@/components/resource";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import { RoleGuard } from "@/components/ui/role-guard";
import { StateBlock } from "@/components/shared/state-block";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";

import { CATEGORY, IMPL, SECURITY, type ToolCategory } from "@/lib/tools/taxonomy";
import { useCurrentUser } from "@/lib/use-current-user";

import { ToolGroup } from "./tool-catalog-rows";
import { ToolPanel } from "./tool-panel";
import { DeleteToolDialog, ToolFormDialog } from "./tool-dialogs";
import { FacetSelect, facetLabel } from "./tool-facet-select";
import { ALL, type CatalogTool } from "./tool-types";

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export function ToolsTab() {
  const { lang } = useLang();
  const t = useT("tools");
  const tr = useT("resource");
  const [selectedId, setSelectedId] = useState<string | null>(null);
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

  const selectedTool = tools.find((x) => x.id === selectedId) ?? null;

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
    <div data-testid="tools-tab">
      <ResourceHeader
        icon={<Wrench className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("title")}
        description={t("description")}
        count={catalogQuery.data ? tools.length : undefined}
        countLabel={tr(tools.length === 1 ? "countToolsOne" : "countToolsMany", {
          count: tools.length,
        })}
        action={
          <RoleGuard min="tenant_admin">
            <Button onClick={openCreate} data-testid="tools-create-button">
              <Plus className="mr-1 h-4 w-4" />
              {t("newTool")}
            </Button>
          </RoleGuard>
        }
      />

      {/* Faceted browse: search + the three ADR-0049 facets. */}
      <div data-testid="tools-facets">
        <ResourceToolbar
          search={query}
          onSearchChange={setQuery}
          searchPlaceholder={t("searchPlaceholder")}
          searchAriaLabel={t("searchAriaLabel")}
          searchTestId="tools-search"
        >
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
        </ResourceToolbar>
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
          <ResourceLayout
            panel={
              selectedTool ? (
                <ToolPanel tool={selectedTool} lang={lang} onClose={() => setSelectedId(null)} />
              ) : undefined
            }
            list={
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
                  selectedId={selectedId}
                  onSelect={setSelectedId}
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
                  selectedId={selectedId}
                  onSelect={setSelectedId}
                  testidPrefix="custom"
                />
              </div>
            }
          />
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
