"use client";

/**
 * Sub-sección "Tools del agente" del detalle de agente
 * (Plan 06.15 task_06_15_03).
 *
 * Sigue el patrón de `<AgentKbsSection>` (Plan 06.9): TanStack-Query
 * para leer/escribir, RoleGuard implícito vía `isReadOnly`, estados
 * vacío/cargando/error, layout shadcn/ui consistente.
 *
 * Dos pestañas con la taxonomía DERIVADA (Plan 06.15 — sin columna
 * nueva en `Tool`):
 *   - BÁSICAS   : tools con `is_builtin = true`.
 *   - AVANZADAS : `is_builtin = false` (custom) o `implementation_type`
 *                 ∈ {mcp_tool, http_endpoint, python_function,
 *                    docker_command}.
 *
 * Cada fila: nombre + descripción + badge de `security_level` + badge
 * de `implementation_type` + un checkbox. La selección se guarda
 * declarativamente con `PUT /agents/{id}/tools` (set completo). Una
 * lista vacía limpia todas las asignaciones → comportamiento
 * backward-compatible (sin restricción por agente) en el runtime.
 *
 * Read-only para agentes `global_builtin` (los gestiona la plataforma —
 * el backend rechazaría con 403) y para usuarios no `tenant_admin`.
 *
 * Endpoints consumidos:
 *   - GET /tools                  catálogo (builtins + custom del tenant)
 *   - GET /agents/{id}/tools      asignaciones actuales
 *   - PUT /agents/{id}/tools      set declarativo {tools: [{tool_id}]}
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Wrench } from "lucide-react";

import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
}

// Badge maps mirror the agent-tools-diagnostic panel for visual parity.
const IMPL_BADGE: Record<string, BadgeVariant> = {
  builtin: "muted",
  mcp_tool: "success",
  http_endpoint: "info",
  python_function: "warning",
  docker_command: "danger",
};

const SECURITY_BADGE: Record<string, BadgeVariant> = {
  safe: "success",
  sandboxed: "warning",
  privileged: "danger",
};

/** Derived taxonomy: básica = builtin; todo lo demás es avanzada. */
function isBasic(tool: { is_builtin: boolean; implementation_type: string }): boolean {
  return tool.is_builtin && tool.implementation_type === "builtin";
}

export function AgentToolsSection({ agentId, isReadOnly }: AgentToolsSectionProps) {
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

  const assignedIds = useMemo(
    () => (assignedQuery.data ?? []).map((r) => r.tool_id).sort(),
    [assignedQuery.data],
  );

  // Reseed the local selection whenever the server set changes (initial
  // load or after a successful save invalidates the query). Resetting
  // `dirty` keeps the Save button disabled until the user edits again.
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
      if (next.has(toolId)) {
        next.delete(toolId);
      } else {
        next.add(toolId);
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
  const basicTools = useMemo(
    () => catalog.filter((t) => isBasic(t)).sort((a, b) => a.name.localeCompare(b.name)),
    [catalog],
  );
  const advancedTools = useMemo(
    () => catalog.filter((t) => !isBasic(t)).sort((a, b) => a.name.localeCompare(b.name)),
    [catalog],
  );

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
            Marca las tools que este agente puede usar. Sin ninguna marcada, el agente conserva el
            comportamiento por defecto (sin restricción por agente).
          </p>
        </div>
        {canEdit && (
          <div className="flex shrink-0 items-center gap-2">
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
          </div>
        )}
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
            <Tabs defaultValue="basic">
              <TabsList data-testid="agent-tools-tabs">
                <TabsTrigger value="basic" data-testid="agent-tools-tab-basic">
                  Básicas ({basicTools.length})
                </TabsTrigger>
                <TabsTrigger value="advanced" data-testid="agent-tools-tab-advanced">
                  Avanzadas ({advancedTools.length})
                </TabsTrigger>
              </TabsList>

              <TabsContent value="basic">
                <ToolList
                  tools={basicTools}
                  selected={selected}
                  canEdit={canEdit}
                  onToggle={toggle}
                  emptyMessage="No hay tools básicas (builtin) en el catálogo."
                  testidPrefix="basic"
                />
              </TabsContent>

              <TabsContent value="advanced">
                <ToolList
                  tools={advancedTools}
                  selected={selected}
                  canEdit={canEdit}
                  onToggle={toggle}
                  emptyMessage="No hay tools avanzadas (custom · MCP · ejecutores). Crea una en el catálogo /tools o configura un MCP server en el proyecto."
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
// One tab's list of selectable tools
// ---------------------------------------------------------------------------
function ToolList({
  tools,
  selected,
  canEdit,
  onToggle,
  emptyMessage,
  testidPrefix,
}: {
  tools: CatalogTool[];
  selected: Set<string>;
  canEdit: boolean;
  onToggle: (toolId: string) => void;
  emptyMessage: string;
  testidPrefix: string;
}) {
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
    <ul className="space-y-2" data-testid={`agent-tools-${testidPrefix}-list`}>
      {tools.map((tool) => {
        const checked = selected.has(tool.id);
        const implVariant = IMPL_BADGE[tool.implementation_type] ?? "muted";
        const secVariant = SECURITY_BADGE[tool.security_level] ?? "muted";
        const inputId = `agent-tool-${tool.id}`;
        return (
          <li
            key={tool.id}
            className="flex items-start gap-3 rounded border p-3"
            data-testid={`agent-tool-row-${tool.id}`}
          >
            <input
              id={inputId}
              type="checkbox"
              className="border-input text-primary focus-visible:ring-ring mt-0.5 h-4 w-4 rounded focus-visible:outline-none focus-visible:ring-2"
              checked={checked}
              disabled={!canEdit}
              onChange={() => onToggle(tool.id)}
              data-testid={`agent-tool-checkbox-${tool.id}`}
            />
            <label htmlFor={inputId} className="min-w-0 flex-1 cursor-pointer">
              <div className="flex flex-wrap items-center gap-2">
                <span className="font-mono text-sm font-medium">{tool.name}</span>
                <Badge variant={secVariant}>{tool.security_level}</Badge>
                <Badge variant={implVariant}>{tool.implementation_type}</Badge>
                <span className="text-muted-foreground text-xs">{tool.category}</span>
              </div>
              {tool.description && (
                <p className="text-muted-foreground mt-0.5 line-clamp-2 text-xs">
                  {tool.description}
                </p>
              )}
            </label>
          </li>
        );
      })}
    </ul>
  );
}
