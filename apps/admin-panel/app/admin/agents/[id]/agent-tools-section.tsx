"use client";

/**
 * Sub-sección "Tools del agente" del detalle de agente
 * (Plan 06.15 task_06_15_03; UX-friendly pass Plan 06.15 gate).
 *
 * Sigue el patrón de `<AgentKbsSection>` (Plan 06.9): TanStack-Query
 * para leer/escribir, RoleGuard implícito vía `isReadOnly`, estados
 * vacío/cargando/error, layout shadcn/ui consistente.
 *
 * Dos pestañas con la taxonomía DERIVADA (Plan 06.15 / ADR 0044 — sin
 * columna nueva en `Tool`):
 *   - BÁSICAS   : tools de plataforma → `is_builtin = true` (cualquier
 *                 `implementation_type`: builtin, docker_command, …).
 *   - AVANZADAS : `is_builtin = false` → custom del tenant + MCP.
 * El `implementation_type` y el `security_level` son SOLO badges
 * informativos, NO el criterio de clasificación.
 *
 * UX: dentro de cada pestaña las tools se agrupan por categoría con
 * etiquetas humanas + icono + "seleccionar todo" por grupo; buscador;
 * badge de seguridad con tooltip en lenguaje llano. La selección se
 * guarda declarativamente con `PUT /agents/{id}/tools` (set completo).
 * Lista vacía → backward-compatible (sin restricción por agente).
 *
 * Read-only para agentes `global_builtin` y para usuarios no `tenant_admin`.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  BookOpen,
  Check,
  FileText,
  GitBranch,
  Globe,
  Info,
  type LucideIcon,
  ScanSearch,
  Search,
  Shield,
  Terminal,
  TerminalSquare,
  Wrench,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Tooltip, TooltipTrigger } from "@/components/ui/tooltip";
import { apiFetch } from "@/lib/api";
import { pickLang, useT } from "@/lib/i18n";
import { useLang, type Lang } from "@/lib/lang-context";
import { resolveCategory, resolveImpl, resolveSecurity } from "@/lib/tools/taxonomy";
import { useCurrentUser } from "@/lib/use-current-user";
import { cn } from "@/lib/utils";
import { useErrorText } from "@/lib/use-error-text";

// ---------------------------------------------------------------------------
// Types (mirror api_server.schemas.catalog.ToolResponse +
// api_server.schemas.agents.AgentToolResponse)
// ---------------------------------------------------------------------------
interface CatalogTool {
  id: string;
  name: string;
  description: string | null;
  category: string;
  implementation_type: string;
  security_level: string;
  is_builtin: boolean;
  // ADR 0049 / F3: si el runtime puede EJECUTARLA de verdad. false = el agente
  // la vería en su prompt pero moriría como `unknown tool`.
  is_runtime_wired: boolean;
}

interface AgentToolRow {
  tool_id: string;
  name: string;
  description: string | null;
  category: string;
  implementation_type: string;
  security_level: string;
  is_builtin: boolean;
  config_override: Record<string, unknown> | null;
}

interface AgentToolsSectionProps {
  agentId: string;
  /** Read-only when the agent is `global_builtin` (platform-managed). */
  isReadOnly: boolean;
  /**
   * Project of a `project_local` agent (task_06_15_04). When present we
   * surface a link to the read-only project diagnostic so an operator can
   * verify "why does agent X see tool Y" right after assigning.
   */
  projectId?: string | null;
}

// ---------------------------------------------------------------------------
// Presentation-only helpers. Labels / variants / help for the THREE facets
// (Función / Seguridad / Origen) live in the SHARED taxonomy module
// (`@/lib/tools/taxonomy`) so the same tool renders identically here and in the
// read-only diagnostic. Only the per-category ICON + display ORDER are
// UI-specific to this grouped list and stay local.
// ---------------------------------------------------------------------------
const CATEGORY_ICON: Record<string, LucideIcon> = {
  file: FileText,
  git: GitBranch,
  runtime: TerminalSquare,
  network: Globe,
  knowledge: BookOpen,
  notification: Bell,
  command: Terminal,
  shell: Terminal,
};

// Order categories sensibly; unknown ones fall to the end (alpha).
const CATEGORY_ORDER = [
  "command",
  "shell",
  "runtime",
  "file",
  "git",
  "network",
  "knowledge",
  "notification",
];

/**
 * Nombre legible de una categoria en el idioma activo.
 *
 * El label llega en DATOS bilingues (`{labelEs, labelEn}` de
 * `lib/tools/taxonomy`), no del diccionario: el catalogo lo alimenta el
 * backend. Por eso lo elige `pickLang`, que ademas cae al otro idioma si el
 * pedido viene vacio -- antes era un ternario a mano.
 */
function categoryLabel(cat: string, lang: Lang): string {
  const d = resolveCategory(cat, lang);
  return pickLang(lang, { es: d.labelEs, en: d.labelEn });
}

function categoryRank(cat: string): number {
  const i = CATEGORY_ORDER.indexOf(cat);
  return i === -1 ? CATEGORY_ORDER.length : i;
}

/**
 * Derived taxonomy (ADR 0044): básica = tool de plataforma (`is_builtin`),
 * con CUALQUIER `implementation_type`. Avanzada = todo lo demás (custom + MCP).
 */
function isBasic(tool: { is_builtin: boolean }): boolean {
  return tool.is_builtin;
}

/**
 * ADR 0128: las tools MCP las aporta el PROYECTO en runtime, NO se conceden
 * por-agente. Se excluyen de la lista asignable de "Avanzadas" (que sigue
 * gestionando las tools custom NO-MCP: http_endpoint / python_function /
 * docker_command). Una tool es MCP por `implementation_type` o por `category`.
 */
function isMcp(tool: { implementation_type: string; category: string }): boolean {
  return tool.implementation_type === "mcp_tool" || tool.category === "mcp";
}

export function AgentToolsSection({ agentId, isReadOnly, projectId }: AgentToolsSectionProps) {
  const errorText = useErrorText();
  const t = useT("agents");
  const queryClient = useQueryClient();
  const { lang } = useLang();
  const { isTenantAdmin, isLoading: roleLoading } = useCurrentUser();

  // Non-admins (tenant_user) get a read-only view too — the backend
  // would reject the PUT with 403 anyway, so we hide the affordance.
  const canEdit = !isReadOnly && isTenantAdmin;

  const catalogQuery = useQuery({
    queryKey: ["tools-catalog"],
    queryFn: () => apiFetch<CatalogTool[]>("/tools?limit=500"),
    refetchOnWindowFocus: false,
  });

  const assignedQuery = useQuery({
    queryKey: ["agent-tools", agentId],
    queryFn: () => apiFetch<AgentToolRow[]>(`/agents/${agentId}/tools`),
    refetchOnWindowFocus: false,
  });

  // Local selection (set of tool_ids). Seeded from the server once the
  // assignments load; the user edits it freely and saves the whole set.
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [dirty, setDirty] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [query, setQuery] = useState("");

  const assignedIds = useMemo(
    () => (assignedQuery.data ?? []).map((r) => r.tool_id).sort(),
    [assignedQuery.data],
  );

  useEffect(() => {
    if (assignedQuery.data) {
      setSelected(new Set(assignedIds));
      setDirty(false);
    }
  }, [assignedQuery.data, assignedIds]);

  const saveMutation = useMutation({
    mutationFn: (toolIds: string[]) =>
      apiFetch<AgentToolRow[]>(`/agents/${agentId}/tools`, {
        method: "PUT",
        body: { tools: toolIds.map((tool_id) => ({ tool_id })) },
      }),
    onSuccess: () => {
      setSaveError(null);
      setSavedAt(Date.now());
      void queryClient.invalidateQueries({ queryKey: ["agent-tools", agentId] });
    },
    onError: (err) => {
      setSaveError(errorText(err));
    },
  });

  const toggle = (toolId: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(toolId)) next.delete(toolId);
      else next.add(toolId);
      return next;
    });
    setDirty(true);
    setSavedAt(null);
  };

  const toggleMany = (toolIds: string[], on: boolean) => {
    setSelected((prev) => {
      const next = new Set(prev);
      for (const id of toolIds) {
        if (on) next.add(id);
        else next.delete(id);
      }
      return next;
    });
    setDirty(true);
    setSavedAt(null);
  };

  const reset = () => {
    setSelected(new Set(assignedIds));
    setDirty(false);
    setSaveError(null);
    setSavedAt(null);
  };

  const isLoading = catalogQuery.isLoading || assignedQuery.isLoading || roleLoading;
  const isError = catalogQuery.isError || assignedQuery.isError;
  const errorMsg =
    (catalogQuery.error instanceof Error && catalogQuery.error.message) ||
    (assignedQuery.error instanceof Error && assignedQuery.error.message) ||
    "error desconocido";

  const catalog = useMemo(() => catalogQuery.data ?? [], [catalogQuery.data]);

  const q = query.trim().toLowerCase();
  const matches = useMemo(
    () =>
      catalog.filter(
        (t) =>
          q === "" ||
          t.name.toLowerCase().includes(q) ||
          (t.description ?? "").toLowerCase().includes(q) ||
          categoryLabel(t.category, lang).toLowerCase().includes(q),
      ),
    [catalog, q, lang],
  );
  const basicTools = useMemo(() => matches.filter(isBasic), [matches]);
  // ADR 0128: "Avanzadas" = custom NO-MCP (las MCP las aporta el proyecto).
  const advancedTools = useMemo(() => matches.filter((t) => !isBasic(t) && !isMcp(t)), [matches]);

  const totalBasic = useMemo(() => catalog.filter(isBasic).length, [catalog]);
  const totalAdvanced = useMemo(
    () => catalog.filter((t) => !isBasic(t) && !isMcp(t)).length,
    [catalog],
  );

  return (
    <Card data-testid="agent-tools-section">
      <CardHeader className="flex flex-row items-start justify-between gap-3 pb-2">
        <div className="min-w-0">
          <CardTitle className="text-base">
            <span className="inline-flex items-center gap-2">
              <Wrench className="h-4 w-4" /> {t("toolsTitle")}
            </span>
          </CardTitle>
          <p className="text-muted-foreground mt-1 text-xs">
            {t("toolsHelp")}
            <span className="ml-1 font-medium">
              {t("toolsSelectedCount", { n: selected.size })}
            </span>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {projectId && (
            <Button
              asChild
              variant="outline"
              size="sm"
              title={t("toolsDiagnosticTitle")}
              data-testid="agent-tools-diagnostic-link"
            >
              <Link href={`/admin/projects/${projectId}/agent-tools-diagnostic`}>
                <ScanSearch className="mr-1 h-4 w-4" />
                {t("toolsDiagnostic")}
              </Link>
            </Button>
          )}
          {canEdit && (
            <>
              {dirty && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={reset}
                  disabled={saveMutation.isPending}
                  data-testid="agent-tools-reset"
                >
                  {t("discard")}
                </Button>
              )}
              <Button
                size="sm"
                onClick={() => saveMutation.mutate(Array.from(selected))}
                disabled={!dirty || saveMutation.isPending}
                data-testid="agent-tools-save"
              >
                {saveMutation.isPending ? t("saving") : t("save")}
              </Button>
              {!saveMutation.isPending && savedAt !== null && !dirty && (
                <span
                  className="text-success-soft-foreground inline-flex items-center gap-1 text-sm"
                  data-testid="agent-tools-saved"
                >
                  <Check className="h-4 w-4" />
                  {t("saved")}
                </span>
              )}
            </>
          )}
        </div>
      </CardHeader>

      <CardContent>
        {isLoading && (
          <div className="flex justify-center p-4" data-testid="agent-tools-loading">
            <Spinner />
          </div>
        )}

        {!isLoading && isError && (
          <p className="text-danger-soft-foreground text-sm" data-testid="agent-tools-error">
            {t("toolsLoadError", { detail: errorMsg })}
          </p>
        )}

        {!isLoading && !isError && (
          <>
            {saveError && (
              <p
                className="bg-danger-soft text-danger-soft-foreground mb-3 rounded p-2 text-xs"
                data-testid="agent-tools-save-error"
              >
                {saveError}
              </p>
            )}

            <div className="relative mb-3">
              <Search
                className="text-muted-foreground pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
                aria-hidden="true"
              />
              <Input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={t("toolsSearchPlaceholder")}
                aria-label={t("toolsSearchLabel")}
                className="pl-9"
                data-testid="agent-tools-search"
              />
            </div>

            <Tabs defaultValue="basic">
              <TabsList data-testid="agent-tools-tabs">
                <TabsTrigger value="basic" data-testid="agent-tools-tab-basic">
                  {t("toolsTabBasic")} ({totalBasic})
                </TabsTrigger>
                <TabsTrigger value="advanced" data-testid="agent-tools-tab-advanced">
                  {t("toolsTabAdvanced")} ({totalAdvanced})
                </TabsTrigger>
              </TabsList>

              <TabsContent value="basic">
                <GroupedToolList
                  tools={basicTools}
                  selected={selected}
                  canEdit={canEdit}
                  lang={lang}
                  onToggle={toggle}
                  onToggleMany={toggleMany}
                  emptyMessage={q ? t("toolsEmptyBasicSearch") : t("toolsEmptyBasic")}
                  testidPrefix="basic"
                />
              </TabsContent>

              <TabsContent value="advanced">
                {/* ADR 0128: las tools MCP las aporta el PROYECTO, no se asignan
                    por-agente. Se excluyen de esta lista y se explica dónde van. */}
                <div
                  className="border-info/30 bg-info-soft text-info-soft-foreground mb-3 flex items-start gap-2 rounded-md border p-3 text-xs"
                  data-testid="agent-tools-mcp-project-note"
                >
                  <Info aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>
                    {t("toolsMcpNoteLead")}{" "}
                    <span className="font-medium">{t("toolsMcpNoteStrong")}</span>
                    {t("toolsMcpNoteTail")}
                  </span>
                </div>
                <GroupedToolList
                  tools={advancedTools}
                  selected={selected}
                  canEdit={canEdit}
                  lang={lang}
                  onToggle={toggle}
                  onToggleMany={toggleMany}
                  emptyMessage={
                    q ? (
                      t("toolsEmptyAdvancedSearch")
                    ) : (
                      <>
                        {t("toolsEmptyAdvancedLead")}{" "}
                        <Link
                          href="/admin/tools"
                          className="text-primary font-medium underline-offset-4 hover:underline"
                          data-testid="agent-tools-catalog-link"
                        >
                          {t("toolsCatalogLink")}
                        </Link>
                        {t("toolsEmptyAdvancedTail")}
                      </>
                    )
                  }
                  testidPrefix="advanced"
                />
              </TabsContent>
            </Tabs>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// A tab's tools, grouped by category (friendly header + per-group select-all)
// ---------------------------------------------------------------------------
function GroupedToolList({
  tools,
  selected,
  canEdit,
  lang,
  onToggle,
  onToggleMany,
  emptyMessage,
  testidPrefix,
}: {
  tools: CatalogTool[];
  selected: Set<string>;
  canEdit: boolean;
  lang: Lang;
  onToggle: (toolId: string) => void;
  onToggleMany: (toolIds: string[], on: boolean) => void;
  emptyMessage: ReactNode;
  testidPrefix: string;
}) {
  const t = useT("agents");
  const groups = useMemo(() => {
    const byCat = new Map<string, CatalogTool[]>();
    for (const t of tools) {
      const arr = byCat.get(t.category) ?? [];
      arr.push(t);
      byCat.set(t.category, arr);
    }
    return Array.from(byCat.entries())
      .map(([cat, items]) => ({
        cat,
        items: items.sort((a, b) => a.name.localeCompare(b.name)),
      }))
      .sort((a, b) => categoryRank(a.cat) - categoryRank(b.cat) || a.cat.localeCompare(b.cat));
  }, [tools]);

  if (tools.length === 0) {
    return (
      <p
        className="text-muted-foreground py-4 text-sm italic"
        data-testid={`agent-tools-${testidPrefix}-empty`}
      >
        {emptyMessage}
      </p>
    );
  }

  return (
    <div className="space-y-4" data-testid={`agent-tools-${testidPrefix}-list`}>
      {groups.map(({ cat, items }) => {
        const Icon = CATEGORY_ICON[cat] ?? Wrench;
        const ids = items.map((t) => t.id);
        const selectedCount = ids.filter((id) => selected.has(id)).length;
        const allOn = selectedCount === ids.length;
        const noneOn = selectedCount === 0;
        const indeterminate = !allOn && !noneOn;
        const bulkId = `agent-tools-group-toggle-${cat}`;
        return (
          <section key={cat} data-testid={`agent-tools-group-${cat}`}>
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <h4 className="text-muted-foreground inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide">
                <Icon className="h-3.5 w-3.5" />
                {categoryLabel(cat, lang)}
                <span className="text-muted-foreground/70 font-normal normal-case">
                  ({selectedCount}/{ids.length})
                </span>
              </h4>
              {canEdit && (
                <label
                  htmlFor={bulkId}
                  className="text-muted-foreground hover:text-foreground inline-flex cursor-pointer items-center gap-1.5 text-xs"
                >
                  <Checkbox
                    id={bulkId}
                    checked={allOn}
                    indeterminate={indeterminate}
                    onChange={() => onToggleMany(ids, !allOn)}
                    aria-label={
                      allOn
                        ? t("toolsUnselectAllAria", { category: categoryLabel(cat, lang) })
                        : t("toolsSelectAllAria", { category: categoryLabel(cat, lang) })
                    }
                    data-testid={bulkId}
                  />
                  {allOn ? t("toolsUnselectAll") : t("toolsSelectAll")}
                </label>
              )}
            </div>
            <ul className="space-y-2">
              {items.map((tool) => (
                <ToolRow
                  key={tool.id}
                  tool={tool}
                  checked={selected.has(tool.id)}
                  canEdit={canEdit}
                  lang={lang}
                  onToggle={onToggle}
                />
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

function ToolRow({
  tool,
  checked,
  canEdit,
  lang,
  onToggle,
}: {
  tool: CatalogTool;
  checked: boolean;
  canEdit: boolean;
  lang: Lang;
  onToggle: (toolId: string) => void;
}) {
  const t = useT("agents");
  const inputId = `agent-tool-${tool.id}`;
  // SINGLE source: the same shared resolvers the diagnostic uses, so a tool
  // shows identical label/variant in both screens (never the raw enum).
  const sec = resolveSecurity(tool.security_level, lang);
  const impl = resolveImpl(tool.implementation_type, lang);
  const secVariant = sec.variant;
  const implVariant = impl.variant;
  // Mismo caso que `categoryLabel`: label bilingue que viene en datos.
  const secLabel = pickLang(lang, { es: sec.labelEs, en: sec.labelEn });
  const secHelp = sec.help;
  const implLabel = pickLang(lang, { es: impl.labelEs, en: impl.labelEn });
  const implHelp = impl.help;

  return (
    <li
      // Strong, glance-readable selected state: the whole row tints and
      // gets a primary border. Hover affordance only when editable, and
      // the highlighted area === the toggle area (the <label> is full-bleed).
      className={cn(
        "rounded border transition-colors",
        checked ? "border-primary/60 bg-primary/5" : "border-border",
        canEdit && "hover:bg-muted/40",
        checked && canEdit && "hover:bg-primary/10",
      )}
      data-testid={`agent-tool-row-${tool.id}`}
      data-selected={checked ? "true" : "false"}
    >
      <div className="flex items-start gap-3 p-3">
        {/* Toggle area: the label fills the row, so clicking the name /
            description / blank space all flip the same checkbox. Read-only
            rows use the default cursor and the checkbox is disabled, so
            nothing pretends to be clickable. */}
        <label
          htmlFor={inputId}
          className={cn(
            "flex min-w-0 flex-1 items-start gap-3",
            canEdit ? "cursor-pointer" : "cursor-default",
          )}
        >
          <Checkbox
            id={inputId}
            className="mt-0.5"
            checked={checked}
            disabled={!canEdit}
            onChange={() => onToggle(tool.id)}
            data-testid={`agent-tool-checkbox-${tool.id}`}
          />
          <span className="min-w-0 flex-1">
            <span className="text-sm font-medium">{tool.name}</span>
            {tool.description && (
              <span className="text-muted-foreground mt-0.5 line-clamp-2 block text-xs">
                {tool.description}
              </span>
            )}
          </span>
        </label>

        {/* Informative badges live OUTSIDE the toggle <label> so a click on
            a badge (or its tooltip trigger) never flips the checkbox. They
            are flat (no border / no button affordance) but carry an icon
            and an accessible tooltip that opens on hover AND keyboard focus. */}
        <div className="flex shrink-0 flex-wrap items-center justify-end gap-1.5">
          <Tooltip content={secHelp}>
            <TooltipTrigger
              aria-label={t("toolSecurityAria", { label: secLabel, help: secHelp })}
              data-testid={`agent-tool-security-badge-${tool.id}`}
            >
              <Badge variant={secVariant} className="gap-1">
                <Shield aria-hidden="true" className="h-3 w-3" />
                {secLabel}
              </Badge>
            </TooltipTrigger>
          </Tooltip>
          <Tooltip content={implHelp}>
            <TooltipTrigger
              aria-label={t("toolImplAria", { label: implLabel, help: implHelp })}
              data-testid={`agent-tool-impl-badge-${tool.id}`}
            >
              <Badge variant={implVariant} className="gap-1">
                <Info aria-hidden="true" className="h-3 w-3" />
                {implLabel}
              </Badge>
            </TooltipTrigger>
          </Tooltip>
          {tool.is_runtime_wired === false && (
            <Tooltip content={t("toolNotWiredTooltip")}>
              <TooltipTrigger
                aria-label={t("toolNotWiredAria")}
                data-testid={`agent-tool-not-wired-badge-${tool.id}`}
              >
                <Badge variant="warning" className="gap-1">
                  <Info aria-hidden="true" className="h-3 w-3" />
                  {t("toolNotWiredBadge")}
                </Badge>
              </TooltipTrigger>
            </Tooltip>
          )}
        </div>
      </div>
    </li>
  );
}
