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
 * La tarjeta-fila REUTILIZA el patrón visual de `agent-tools-section`
 * (`<ToolCatalogRow>`): borde + tinte primary cuando está seleccionada en la
 * asignación; aquí, sin checkbox, mantiene el mismo layout (nombre +
 * descripción a la izquierda, badges de las tres facetas a la derecha) y los
 * MISMOS resolvers de taxonomía, de modo que una tool dada se ve idéntica en
 * el catálogo, en la asignación y en el diagnóstico.
 *
 * Una tool sin motor en el runtime (`is_runtime_wired === false`, ADR 0049)
 * se marca "No disponible aún" para no engañar al operador.
 */

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Info, Pencil, Plus, Search, Shield, Trash2, Wrench, X } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { Select } from "@/components/ui/select";
import { Tooltip, TooltipTrigger } from "@/components/ui/tooltip";
import { StateBlock } from "@/components/shared/state-block";
import { ApiError, apiFetch } from "@/lib/api";
import { useLang, type Lang } from "@/lib/lang-context";
import {
  CATEGORY,
  IMPL,
  SECURITY,
  resolveCategory,
  resolveImpl,
  resolveSecurity,
  type ImplementationType,
  type SecurityLevel,
  type ToolCategory,
} from "@/lib/tools/taxonomy";
import { useCurrentUser } from "@/lib/use-current-user";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Types — mirror api_server.schemas.catalog.ToolResponse / *Request
// ---------------------------------------------------------------------------
interface CatalogTool {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  category: string;
  implementation_type: string;
  implementation_ref: string | null;
  security_level: string;
  is_builtin: boolean;
  is_runtime_wired: boolean;
}

interface ToolFormValue {
  name: string;
  description: string;
  category: ToolCategory;
  implementation_type: ImplementationType;
  implementation_ref: string;
  security_level: SecurityLevel;
}

const EMPTY_FORM: ToolFormValue = {
  name: "",
  description: "",
  category: "custom",
  implementation_type: "http_endpoint",
  security_level: "safe",
  implementation_ref: "",
};

// "Todas" sentinel for a facet filter (no narrowing).
const ALL = "__all__";

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function ToolsCatalogPage() {
  const { lang } = useLang();
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
        title="Catálogo de tools"
        description="Explora las tools de la plataforma y gestiona las personalizadas de tu tenant. Las built-in son de solo lectura."
        actions={
          <RoleGuard min="tenant_admin">
            <Button onClick={openCreate} data-testid="tools-create-button">
              <Plus className="mr-1 h-4 w-4" />
              Nueva tool
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
            placeholder="Buscar por nombre o descripción…"
            aria-label="Buscar tool por nombre o descripción"
            className="pl-9"
            data-testid="tools-search"
          />
        </div>

        <FacetSelect
          id="tools-facet-category"
          label="Función"
          value={category}
          onChange={setCategory}
          options={Object.keys(CATEGORY)}
          terms={CATEGORY}
          lang={lang}
          testid="tools-facet-category"
        />
        <FacetSelect
          id="tools-facet-security"
          label="Seguridad"
          value={security}
          onChange={setSecurity}
          options={Object.keys(SECURITY)}
          terms={SECURITY}
          lang={lang}
          testid="tools-facet-security"
        />
        <FacetSelect
          id="tools-facet-impl"
          label="Origen"
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
        errorTitle="No se pudo cargar el catálogo de tools"
        errorTestId="tools-catalog-error"
      >
        {filtered.length === 0 ? (
          <EmptyState
            icon={Wrench}
            title={
              filtersActive
                ? "Ninguna tool coincide con los filtros"
                : "No hay tools en el catálogo"
            }
            description={
              filtersActive
                ? "Ajusta o limpia los filtros para ver más resultados."
                : "Crea una tool personalizada para empezar."
            }
            action={
              filtersActive ? (
                <Button variant="outline" onClick={resetFilters} data-testid="tools-clear-filters">
                  Limpiar filtros
                </Button>
              ) : undefined
            }
            data-testid="tools-catalog-empty"
          />
        ) : (
          <div className="space-y-8">
            <ToolGroup
              title="De plataforma (built-in)"
              hint="Mantenidas por la plataforma · solo lectura"
              tools={builtins}
              lang={lang}
              canEdit={false}
              onEdit={openEdit}
              onDelete={setConfirmDelete}
              testidPrefix="builtin"
            />
            <ToolGroup
              title="Personalizadas del tenant"
              hint={isTenantAdmin ? "Editables · custom + MCP" : "Custom + MCP"}
              tools={custom}
              lang={lang}
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

// ---------------------------------------------------------------------------
// Facet <Select> — "Todas" + one option per closed-set term (label, never slug)
// ---------------------------------------------------------------------------
interface BilingualTermLike {
  labelEs: string;
  labelEn: string;
}

function facetLabel(term: BilingualTermLike | undefined, slug: string, lang: Lang): string {
  if (!term) return slug;
  return lang === "es" ? term.labelEs : term.labelEn;
}

function FacetSelect({
  id,
  label,
  value,
  onChange,
  options,
  terms,
  lang,
  testid,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (next: string) => void;
  options: string[];
  terms: Record<string, BilingualTermLike>;
  lang: Lang;
  testid: string;
}) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="text-muted-foreground text-xs uppercase tracking-wide">
        {label}
      </Label>
      <Select id={id} value={value} onChange={(e) => onChange(e.target.value)} data-testid={testid}>
        <option value={ALL}>Todas</option>
        {options.map((slug) => (
          <option key={slug} value={slug}>
            {facetLabel(terms[slug], slug, lang)}
          </option>
        ))}
      </Select>
    </div>
  );
}

// ---------------------------------------------------------------------------
// A titled group of tool rows (built-in vs custom)
// ---------------------------------------------------------------------------
function ToolGroup({
  title,
  hint,
  tools,
  lang,
  canEdit,
  onEdit,
  onDelete,
  testidPrefix,
}: {
  title: string;
  hint: string;
  tools: CatalogTool[];
  lang: Lang;
  canEdit: boolean;
  onEdit: (tool: CatalogTool) => void;
  onDelete: (tool: CatalogTool) => void;
  testidPrefix: string;
}) {
  return (
    <section data-testid={`tools-group-${testidPrefix}`}>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h2 className="text-sm font-semibold uppercase tracking-wide">{title}</h2>
        <span className="text-muted-foreground text-xs">
          {hint} · {tools.length}
        </span>
      </div>
      {tools.length === 0 ? (
        <p
          className="text-muted-foreground py-3 text-sm italic"
          data-testid={`tools-group-${testidPrefix}-empty`}
        >
          Ninguna tool en este grupo con los filtros actuales.
        </p>
      ) : (
        <ul className="space-y-2" data-testid={`tools-group-${testidPrefix}-list`}>
          {tools.map((tool) => (
            <ToolCatalogRow
              key={tool.id}
              tool={tool}
              lang={lang}
              canEdit={canEdit}
              onEdit={onEdit}
              onDelete={onDelete}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// Tool row — same card-row pattern as agent-tools-section's <ToolRow>:
// name + description left, the THREE facet badges right (shared resolvers).
// ---------------------------------------------------------------------------
function ToolCatalogRow({
  tool,
  lang,
  canEdit,
  onEdit,
  onDelete,
}: {
  tool: CatalogTool;
  lang: Lang;
  canEdit: boolean;
  onEdit: (tool: CatalogTool) => void;
  onDelete: (tool: CatalogTool) => void;
}) {
  const cat = resolveCategory(tool.category, lang);
  const sec = resolveSecurity(tool.security_level, lang);
  const imp = resolveImpl(tool.implementation_type, lang);
  const catLabel = lang === "es" ? cat.labelEs : cat.labelEn;
  const secLabel = lang === "es" ? sec.labelEs : sec.labelEn;
  const implLabel = lang === "es" ? imp.labelEs : imp.labelEn;

  return (
    <li
      className={cn("rounded border transition-colors", "border-border hover:bg-muted/40")}
      data-testid={`tool-row-${tool.id}`}
      data-builtin={tool.is_builtin ? "true" : "false"}
    >
      <div className="flex items-start gap-3 p-3">
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="text-sm font-medium">{tool.name}</span>
            {!tool.is_runtime_wired && (
              <Tooltip content="Sin motor en el runtime todavía: asignarla no la haría ejecutable.">
                <TooltipTrigger
                  aria-label="No disponible aún: sin motor en el runtime."
                  data-testid={`tool-unwired-badge-${tool.id}`}
                >
                  <Badge variant="warning">No disponible aún</Badge>
                </TooltipTrigger>
              </Tooltip>
            )}
          </span>
          {tool.description && (
            <span className="text-muted-foreground mt-0.5 line-clamp-2 block text-xs">
              {tool.description}
            </span>
          )}
        </span>

        {/* The THREE facet badges (Función / Seguridad / Origen), flat +
            tooltip on hover AND keyboard focus — same resolvers as the
            assignment screen, so a tool reads identically everywhere. */}
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          <Tooltip content={cat.help}>
            <TooltipTrigger
              aria-label={`Función: ${catLabel}. ${cat.help}`}
              data-testid={`tool-category-badge-${tool.id}`}
            >
              <Badge variant={cat.variant} className="gap-1">
                <Wrench aria-hidden="true" className="h-3 w-3" />
                {catLabel}
              </Badge>
            </TooltipTrigger>
          </Tooltip>
          <Tooltip content={sec.help}>
            <TooltipTrigger
              aria-label={`Seguridad: ${secLabel}. ${sec.help}`}
              data-testid={`tool-security-badge-${tool.id}`}
            >
              <Badge variant={sec.variant} className="gap-1">
                <Shield aria-hidden="true" className="h-3 w-3" />
                {secLabel}
              </Badge>
            </TooltipTrigger>
          </Tooltip>
          <Tooltip content={imp.help}>
            <TooltipTrigger
              aria-label={`Origen: ${implLabel}. ${imp.help}`}
              data-testid={`tool-impl-badge-${tool.id}`}
            >
              <Badge variant={imp.variant} className="gap-1">
                <Info aria-hidden="true" className="h-3 w-3" />
                {implLabel}
              </Badge>
            </TooltipTrigger>
          </Tooltip>

          {tool.is_builtin ? (
            <Badge variant="muted" data-testid={`tool-readonly-badge-${tool.id}`}>
              Solo lectura
            </Badge>
          ) : (
            canEdit && (
              <div className="flex items-center gap-1">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 px-0"
                  onClick={() => onEdit(tool)}
                  aria-label={`Editar ${tool.name}`}
                  data-testid={`tool-edit-${tool.id}`}
                >
                  <Pencil className="h-4 w-4" />
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-8 w-8 px-0"
                  onClick={() => onDelete(tool)}
                  aria-label={`Borrar ${tool.name}`}
                  data-testid={`tool-delete-${tool.id}`}
                >
                  <Trash2 className="text-destructive h-4 w-4" />
                </Button>
              </div>
            )
          )}
        </div>
      </div>
    </li>
  );
}

// ---------------------------------------------------------------------------
// Create / edit dialog — custom tools only (built-in are read-only)
// ---------------------------------------------------------------------------
function ToolFormDialog({
  open,
  onOpenChange,
  editing,
  lang,
}: {
  open: boolean;
  onOpenChange: (next: boolean) => void;
  editing: CatalogTool | null;
  lang: Lang;
}) {
  const queryClient = useQueryClient();
  const isEdit = editing !== null;

  const [form, setForm] = useState<ToolFormValue>(() =>
    editing
      ? {
          name: editing.name,
          description: editing.description ?? "",
          category: (editing.category as ToolCategory) ?? "custom",
          implementation_type:
            (editing.implementation_type as ImplementationType) ?? "http_endpoint",
          implementation_ref: editing.implementation_ref ?? "",
          security_level: (editing.security_level as SecurityLevel) ?? "safe",
        }
      : EMPTY_FORM,
  );

  const mutation = useMutation<CatalogTool, ApiError, void>({
    mutationFn: () => {
      const body = {
        name: form.name,
        description: form.description.trim() === "" ? null : form.description,
        category: form.category,
        implementation_type: form.implementation_type,
        implementation_ref: form.implementation_ref.trim() === "" ? null : form.implementation_ref,
        security_level: form.security_level,
      };
      if (isEdit && editing) {
        return apiFetch<CatalogTool>(`/tools/${editing.id}`, { method: "PUT", body });
      }
      return apiFetch<CatalogTool>("/tools", { method: "POST", body });
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tools-catalog"] });
      onOpenChange(false);
    },
  });

  const set = <K extends keyof ToolFormValue>(key: K, value: ToolFormValue[K]) =>
    setForm((prev) => ({ ...prev, [key]: value }));

  // 409 from the backend = duplicate / collides with a built-in (task_06_18_04).
  const errorMessage =
    mutation.error?.status === 409
      ? "Ya existe una tool con ese nombre (o colisiona con una built-in)."
      : (mutation.error?.body ?? mutation.error?.message ?? null);

  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="lg">
      <DialogContent data-testid="tool-form-dialog">
        <DialogHeader>
          <DialogTitle>{isEdit ? "Editar tool" : "Nueva tool personalizada"}</DialogTitle>
          <DialogDescription>
            Las built-in las mantiene la plataforma; aquí gestionas las custom de tu tenant. El
            nombre se normaliza a slug y debe ser único.
          </DialogDescription>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            mutation.mutate();
          }}
        >
          <DialogBody>
            <div className="space-y-1.5">
              <Label htmlFor="tool-form-name">Nombre</Label>
              <Input
                id="tool-form-name"
                value={form.name}
                onChange={(e) => set("name", e.target.value)}
                placeholder="p. ej. deploy_preview"
                required
                data-testid="tool-form-name"
              />
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="tool-form-description">Descripción</Label>
              <Input
                id="tool-form-description"
                value={form.description}
                onChange={(e) => set("description", e.target.value)}
                placeholder="Qué hace y cuándo usarla"
                data-testid="tool-form-description"
              />
            </div>

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label htmlFor="tool-form-category">Función</Label>
                <Select
                  id="tool-form-category"
                  value={form.category}
                  onChange={(e) => set("category", e.target.value as ToolCategory)}
                  data-testid="tool-form-category"
                >
                  {Object.keys(CATEGORY).map((slug) => (
                    <option key={slug} value={slug}>
                      {facetLabel(CATEGORY[slug as ToolCategory], slug, lang)}
                    </option>
                  ))}
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="tool-form-impl">Origen</Label>
                <Select
                  id="tool-form-impl"
                  value={form.implementation_type}
                  onChange={(e) => set("implementation_type", e.target.value as ImplementationType)}
                  data-testid="tool-form-impl"
                >
                  {Object.keys(IMPL)
                    // `builtin` is platform-only; a tenant tool is never built-in.
                    .filter((slug) => slug !== "builtin")
                    .map((slug) => (
                      <option key={slug} value={slug}>
                        {facetLabel(IMPL[slug as ImplementationType], slug, lang)}
                      </option>
                    ))}
                </Select>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="tool-form-security">Seguridad</Label>
                <Select
                  id="tool-form-security"
                  value={form.security_level}
                  onChange={(e) => set("security_level", e.target.value as SecurityLevel)}
                  data-testid="tool-form-security"
                >
                  {Object.keys(SECURITY).map((slug) => (
                    <option key={slug} value={slug}>
                      {facetLabel(SECURITY[slug as SecurityLevel], slug, lang)}
                    </option>
                  ))}
                </Select>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="tool-form-ref">Referencia de implementación</Label>
              <Input
                id="tool-form-ref"
                value={form.implementation_ref}
                onChange={(e) => set("implementation_ref", e.target.value)}
                placeholder="URL del endpoint, dotted path de la función, comando…"
                data-testid="tool-form-ref"
              />
            </div>

            {errorMessage && (
              <p
                className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
                data-testid="tool-form-error"
              >
                {errorMessage}
              </p>
            )}
          </DialogBody>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              data-testid="tool-form-cancel"
            >
              Cancelar
            </Button>
            <Button
              type="submit"
              disabled={mutation.isPending || form.name.trim() === ""}
              data-testid="tool-form-submit"
            >
              {mutation.isPending ? "Guardando…" : isEdit ? "Guardar cambios" : "Crear tool"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Delete confirmation
// ---------------------------------------------------------------------------
function DeleteToolDialog({ tool, onClose }: { tool: CatalogTool; onClose: () => void }) {
  const queryClient = useQueryClient();
  const mutation = useMutation<void, ApiError, void>({
    mutationFn: () => apiFetch<void>(`/tools/${tool.id}`, { method: "DELETE" }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["tools-catalog"] });
      onClose();
    },
  });

  return (
    <Dialog open onOpenChange={(next) => !next && onClose()} size="sm">
      <DialogContent data-testid="tool-delete-dialog">
        <DialogHeader>
          <DialogTitle>Borrar tool</DialogTitle>
          <DialogDescription>
            Se eliminará <strong>{tool.name}</strong> del catálogo del tenant. Esta acción no se
            puede deshacer.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} data-testid="tool-delete-cancel">
            Cancelar
          </Button>
          <Button
            variant="destructive"
            onClick={() => mutation.mutate()}
            disabled={mutation.isPending}
            data-testid="tool-delete-confirm"
          >
            <X className="mr-1 h-4 w-4" />
            {mutation.isPending ? "Borrando…" : "Borrar"}
          </Button>
        </DialogFooter>
        {mutation.isError && (
          <p
            className="text-danger-soft-foreground px-6 pb-4 text-xs"
            data-testid="tool-delete-error"
          >
            {mutation.error?.body ?? mutation.error?.message ?? "Error al borrar"}
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
