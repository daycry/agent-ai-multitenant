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

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  BookOpen,
  FileText,
  GitBranch,
  Globe,
  type LucideIcon,
  ScanSearch,
  Search,
  Terminal,
  TerminalSquare,
  Wrench,
} from "lucide-react";

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Spinner } from "@/components/ui/spinner";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, apiFetch } from "@/lib/api";
import { useCurrentUser } from "@/lib/use-current-user";

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
// Human-friendly labels (no raw enums in the UI — Plan 06.15 UX requirement)
// ---------------------------------------------------------------------------
const CATEGORY_LABEL: Record<string, string> = {
  file: "Archivos",
  git: "Git",
  runtime: "Ejecución / Tests",
  network: "Red",
  knowledge: "Conocimiento",
  notification: "Notificaciones",
  command: "Comandos shell",
  shell: "Comandos shell",
};

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

const SECURITY_LABEL: Record<string, string> = {
  safe: "Segura",
  sandboxed: "Aislada",
  privileged: "Privilegiada",
};

const SECURITY_HELP: Record<string, string> = {
  safe: "Solo lectura / sin efectos secundarios — sin riesgo.",
  sandboxed: "Modifica dentro del sandbox de la tarea (worktree/contenedor efímero).",
  privileged: "Capacidad potente (p. ej. ejecutar comandos): asígnala con criterio.",
};

const SECURITY_BADGE: Record<string, BadgeVariant> = {
  safe: "success",
  sandboxed: "warning",
  privileged: "danger",
};

const IMPL_LABEL: Record<string, string> = {
  builtin: "Nativa",
  mcp_tool: "MCP",
  http_endpoint: "HTTP",
  python_function: "Python",
  docker_command: "Contenedor",
};

const IMPL_BADGE: Record<string, BadgeVariant> = {
  builtin: "muted",
  mcp_tool: "success",
  http_endpoint: "info",
  python_function: "warning",
  docker_command: "info",
};

function categoryLabel(cat: string): string {
  return CATEGORY_LABEL[cat] ?? cat.charAt(0).toUpperCase() + cat.slice(1);
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

export function AgentToolsSection({ agentId, isReadOnly, projectId }: AgentToolsSectionProps) {
  const queryClient = useQueryClient();
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
      void queryClient.invalidateQueries({ queryKey: ["agent-tools", agentId] });
    },
    onError: (err) => {
      setSaveError(err instanceof ApiError ? err.body : String(err));
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
  };

  const reset = () => {
    setSelected(new Set(assignedIds));
    setDirty(false);
    setSaveError(null);
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
          categoryLabel(t.category).toLowerCase().includes(q),
      ),
    [catalog, q],
  );
  const basicTools = useMemo(() => matches.filter(isBasic), [matches]);
  const advancedTools = useMemo(() => matches.filter((t) => !isBasic(t)), [matches]);

  const totalBasic = useMemo(() => catalog.filter(isBasic).length, [catalog]);
  const totalAdvanced = useMemo(() => catalog.filter((t) => !isBasic(t)).length, [catalog]);

  return (
    <Card data-testid="agent-tools-section">
      <CardHeader className="flex flex-row items-start justify-between gap-3 pb-2">
        <div className="min-w-0">
          <CardTitle className="text-base">
            <span className="inline-flex items-center gap-2">
              <Wrench className="h-4 w-4" /> Tools del agente
            </span>
          </CardTitle>
          <p className="text-muted-foreground mt-1 text-xs">
            Marca las tools que este agente puede usar. Sin ninguna marcada, conserva el
            comportamiento por defecto (sin restricción por agente).
            <span className="ml-1 font-medium">{selected.size} seleccionadas.</span>
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {projectId && (
            <Button
              asChild
              variant="outline"
              size="sm"
              title="Verificación read-only: qué tools ve cada agente del proyecto."
              data-testid="agent-tools-diagnostic-link"
            >
              <Link href={`/admin/projects/${projectId}/agent-tools-diagnostic`}>
                <ScanSearch className="mr-1 h-4 w-4" />
                Diagnóstico
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
                  Descartar
                </Button>
              )}
              <Button
                size="sm"
                onClick={() => saveMutation.mutate(Array.from(selected))}
                disabled={!dirty || saveMutation.isPending}
                data-testid="agent-tools-save"
              >
                {saveMutation.isPending ? "Guardando…" : "Guardar"}
              </Button>
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
            No se pudieron cargar las tools: {errorMsg}.
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
                placeholder="Buscar tool por nombre, descripción o categoría…"
                aria-label="Buscar tool por nombre, descripción o categoría"
                className="pl-9"
                data-testid="agent-tools-search"
              />
            </div>

            <Tabs defaultValue="basic">
              <TabsList data-testid="agent-tools-tabs">
                <TabsTrigger value="basic" data-testid="agent-tools-tab-basic">
                  Básicas ({totalBasic})
                </TabsTrigger>
                <TabsTrigger value="advanced" data-testid="agent-tools-tab-advanced">
                  Avanzadas ({totalAdvanced})
                </TabsTrigger>
              </TabsList>

              <TabsContent value="basic">
                <GroupedToolList
                  tools={basicTools}
                  selected={selected}
                  canEdit={canEdit}
                  onToggle={toggle}
                  onToggleMany={toggleMany}
                  emptyMessage={
                    q
                      ? "Ninguna tool básica coincide con la búsqueda."
                      : "No hay tools básicas (de plataforma) en el catálogo."
                  }
                  testidPrefix="basic"
                />
              </TabsContent>

              <TabsContent value="advanced">
                <GroupedToolList
                  tools={advancedTools}
                  selected={selected}
                  canEdit={canEdit}
                  onToggle={toggle}
                  onToggleMany={toggleMany}
                  emptyMessage={
                    q
                      ? "Ninguna tool avanzada coincide con la búsqueda."
                      : "No hay tools avanzadas (custom · MCP). Crea una en el catálogo /tools o configura un MCP server en el proyecto."
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
  onToggle,
  onToggleMany,
  emptyMessage,
  testidPrefix,
}: {
  tools: CatalogTool[];
  selected: Set<string>;
  canEdit: boolean;
  onToggle: (toolId: string) => void;
  onToggleMany: (toolIds: string[], on: boolean) => void;
  emptyMessage: string;
  testidPrefix: string;
}) {
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
        return (
          <section key={cat} data-testid={`agent-tools-group-${cat}`}>
            <div className="mb-1.5 flex items-center justify-between gap-2">
              <h4 className="text-muted-foreground inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide">
                <Icon className="h-3.5 w-3.5" />
                {categoryLabel(cat)}
                <span className="text-muted-foreground/70 font-normal normal-case">
                  ({selectedCount}/{ids.length})
                </span>
              </h4>
              {canEdit && (
                <button
                  type="button"
                  onClick={() => onToggleMany(ids, !allOn)}
                  className="text-primary text-xs hover:underline"
                  data-testid={`agent-tools-group-toggle-${cat}`}
                >
                  {allOn ? "Quitar todas" : "Seleccionar todas"}
                </button>
              )}
            </div>
            <ul className="space-y-2">
              {items.map((tool) => (
                <ToolRow
                  key={tool.id}
                  tool={tool}
                  checked={selected.has(tool.id)}
                  canEdit={canEdit}
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
  onToggle,
}: {
  tool: CatalogTool;
  checked: boolean;
  canEdit: boolean;
  onToggle: (toolId: string) => void;
}) {
  const secVariant = SECURITY_BADGE[tool.security_level] ?? "muted";
  const implVariant = IMPL_BADGE[tool.implementation_type] ?? "muted";
  const inputId = `agent-tool-${tool.id}`;
  return (
    <li
      className="hover:bg-muted/40 flex items-start gap-3 rounded border p-3 transition-colors"
      data-testid={`agent-tool-row-${tool.id}`}
    >
      <Checkbox
        id={inputId}
        className="mt-0.5"
        checked={checked}
        disabled={!canEdit}
        onChange={() => onToggle(tool.id)}
        data-testid={`agent-tool-checkbox-${tool.id}`}
      />
      <label htmlFor={inputId} className="min-w-0 flex-1 cursor-pointer">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-medium">{tool.name}</span>
          <Badge variant={secVariant} title={SECURITY_HELP[tool.security_level] ?? ""}>
            {SECURITY_LABEL[tool.security_level] ?? tool.security_level}
          </Badge>
          <Badge variant={implVariant}>
            {IMPL_LABEL[tool.implementation_type] ?? tool.implementation_type}
          </Badge>
        </div>
        {tool.description && (
          <p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">{tool.description}</p>
        )}
      </label>
    </li>
  );
}
