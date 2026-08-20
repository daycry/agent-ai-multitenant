"use client";

/**
 * Editor OPCIONAL de la política rol→tool de las MCP del proyecto (ADR 0128,
 * fase 4).
 *
 * Troceado desde `mcp-server-sections.tsx` en prod-16 `task_prod16_08`. Su test
 * (`mcp-tool-roles-section.test.tsx`) ya existía y no se ha tocado.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ShieldCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";
import {
  AGENT_ROLES,
  ROLE_LABEL,
  isMcpTool,
  mcpServerPrefix,
  type CatalogToolLite,
  type ProjectResponse,
} from "./mcp-server-types";

// --------------------------------------------------------------------------
// ADR 0128 fase 4 — editor OPCIONAL de la política rol→tool de las MCP del
// proyecto.
//
// Las tools MCP las aporta el PROYECTO en runtime (no se conceden por-agente):
// cualquier agente del proyecto puede usar las tools de los servers MCP que el
// proyecto declara. Este editor deja restringir CADA tool MCP a un subconjunto
// de roles de agente. Vacío (sin roles marcados) = abierta a TODOS (default).
//
// Persistencia: `PUT /projects/{id}` con `{ mcp_tool_roles }` (set completo).
// `{}` borra la política y vuelve al default "todos los agentes, todas las MCP".
// --------------------------------------------------------------------------
export function McpToolRolePolicySection({ projectId }: { projectId: string }) {
  const errorText = useErrorText();
  const t = useT("mcpServers");
  const tRole = useT("agentRole");
  const tCommon = useT("common");
  const queryClient = useQueryClient();

  // Comparte la caché de la página (misma queryKey) — el PUT la invalida.
  const projectQuery = useQuery({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<ProjectResponse>(`/projects/${projectId}`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
  });

  // Catálogo de tools del tenant — de aquí salen las tools MCP importadas.
  const toolsQuery = useQuery({
    queryKey: ["tools-catalog"],
    queryFn: () => apiFetch<CatalogToolLite[]>("/tools?limit=500"),
    refetchOnWindowFocus: false,
    staleTime: 5 * 60_000,
    enabled: Boolean(projectId),
  });

  // Los servers MCP declarados por el proyecto (prefijo de namespacing).
  const declaredServers = useMemo(
    () =>
      new Set(
        (projectQuery.data?.mcp_servers ?? [])
          .map((s) => s.name)
          .filter((n): n is string => Boolean(n)),
      ),
    [projectQuery.data?.mcp_servers],
  );

  // Tools MCP del PROYECTO: tools MCP del catálogo cuyo `<server>` esté declarado.
  const mcpTools = useMemo(
    () =>
      (toolsQuery.data ?? [])
        .filter((t) => isMcpTool(t))
        .filter((t) => {
          const prefix = mcpServerPrefix(t.name);
          return prefix !== null && declaredServers.has(prefix);
        })
        .sort((a, b) => a.name.localeCompare(b.name)),
    [toolsQuery.data, declaredServers],
  );

  // Política editable localmente: tool name → roles autorizados.
  const [policy, setPolicy] = useState<Record<string, string[]>>({});
  const [dirty, setDirty] = useState(false);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  useEffect(() => {
    if (projectQuery.data) {
      setPolicy({ ...(projectQuery.data.mcp_tool_roles ?? {}) });
      setDirty(false);
      setSavedAt(null);
    }
  }, [projectQuery.data]);

  const saveMutation = useMutation({
    mutationFn: (next: Record<string, string[]>) =>
      apiFetch<ProjectResponse>(`/projects/${projectId}`, {
        method: "PUT",
        body: { mcp_tool_roles: next },
      }),
    onSuccess: () => {
      setDirty(false);
      setSavedAt(Date.now());
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  function toggleRole(toolName: string, role: string) {
    setPolicy((prev) => {
      const roles = new Set(prev[toolName] ?? []);
      if (roles.has(role)) roles.delete(role);
      else roles.add(role);
      const next = { ...prev };
      // Vacío = sin entrada (abierta a todos) — mantiene el JSON mínimo.
      if (roles.size === 0) delete next[toolName];
      else next[toolName] = AGENT_ROLES.filter((r) => roles.has(r));
      return next;
    });
    setDirty(true);
    setSavedAt(null);
  }

  function reset() {
    setPolicy({ ...(projectQuery.data?.mcp_tool_roles ?? {}) });
    setDirty(false);
    setSavedAt(null);
  }

  const isLoading = projectQuery.isLoading || toolsQuery.isLoading;

  return (
    <Card className="mt-8" data-testid="mcp-tool-roles-section">
      <CardHeader className="flex flex-row items-start justify-between gap-3 pb-3">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <ShieldCheck className="h-4 w-4" />
            {t("rolesTitle")}
            <Badge variant="muted">{t("rolesOptional")}</Badge>
          </CardTitle>
          <p className="text-muted-foreground mt-1 text-xs">
            {t("rolesHelpBefore")} <strong>{t("rolesHelpStrong")}</strong> {t("rolesHelpAfter")}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {dirty && (
            <Button
              variant="outline"
              size="sm"
              onClick={reset}
              disabled={saveMutation.isPending}
              data-testid="mcp-tool-roles-reset"
            >
              {t("rolesDiscard")}
            </Button>
          )}
          <Button
            size="sm"
            onClick={() => saveMutation.mutate(policy)}
            disabled={!dirty || saveMutation.isPending}
            data-testid="mcp-tool-roles-save"
          >
            {saveMutation.isPending ? t("saving") : t("rolesSave")}
          </Button>
          {!saveMutation.isPending && savedAt !== null && !dirty && (
            <span
              className="text-success-soft-foreground inline-flex items-center gap-1 text-sm"
              data-testid="mcp-tool-roles-saved"
            >
              <Check className="h-4 w-4" />
              {t("rolesSaved")}
            </span>
          )}
        </div>
      </CardHeader>
      <CardContent>
        {saveMutation.isError ? (
          <p
            className="bg-danger-soft text-danger-soft-foreground mb-3 rounded p-2 text-xs"
            data-testid="mcp-tool-roles-save-error"
          >
            {errorText(saveMutation.error)}
          </p>
        ) : null}

        {isLoading ? (
          <p className="text-muted-foreground text-sm" data-testid="mcp-tool-roles-loading">
            {tCommon("loading")}
          </p>
        ) : mcpTools.length === 0 ? (
          <p className="text-muted-foreground text-sm italic" data-testid="mcp-tool-roles-empty">
            {t("rolesEmptyBefore")} <strong>“{t("testButton")}”</strong> {t("rolesEmptyAfter")}
          </p>
        ) : (
          <ul className="space-y-3" data-testid="mcp-tool-roles-list">
            {mcpTools.map((tool) => {
              const selected = new Set(policy[tool.name] ?? []);
              const openToAll = selected.size === 0;
              return (
                <li
                  key={tool.id}
                  className="rounded border p-3"
                  data-testid={`mcp-tool-roles-tool-${tool.name}`}
                >
                  <div className="mb-2 flex items-center justify-between gap-2">
                    <span className="min-w-0">
                      <Badge variant="success" className="mr-1 align-middle">
                        MCP
                      </Badge>
                      <code className="font-mono text-sm">{tool.name}</code>
                      {tool.description ? (
                        <span className="text-muted-foreground block text-xs">
                          {tool.description}
                        </span>
                      ) : null}
                    </span>
                    {openToAll ? (
                      <Badge variant="muted" data-testid={`mcp-tool-roles-open-${tool.name}`}>
                        {t("rolesOpenToAll")}
                      </Badge>
                    ) : (
                      <Badge variant="info">{t("rolesCount", { count: selected.size })}</Badge>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-x-4 gap-y-2">
                    {AGENT_ROLES.map((role) => {
                      const id = `mcp-tool-roles-role-${tool.name}-${role}`;
                      return (
                        <label
                          key={role}
                          htmlFor={id}
                          className="text-foreground inline-flex cursor-pointer items-center gap-1.5 text-xs"
                        >
                          <Checkbox
                            id={id}
                            checked={selected.has(role)}
                            onChange={() => toggleRole(tool.name, role)}
                            data-testid={id}
                            aria-label={t("roleCanUse", {
                              role: tRole(ROLE_LABEL[role]),
                              tool: tool.name,
                            })}
                          />
                          {tRole(ROLE_LABEL[role])}
                        </label>
                      );
                    })}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
