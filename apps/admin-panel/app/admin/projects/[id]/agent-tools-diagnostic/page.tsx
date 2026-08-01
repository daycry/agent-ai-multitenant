"use client";

/**
 * task_05_15 + Plan 06.18 task_06_18_10 — Panel diagnóstico de tools por
 * agente (read-only).
 *
 * Estructura de datos (dos fuentes, ambas tenant-scoped):
 *
 *   1. `GET /projects/{id}/agent-tools-diagnostic` — enumera los agentes
 *      project-scoped + los MCP servers del proyecto (contexto compartido).
 *   2. `GET /agents/{id}/effective-tools` (task_06_18_07) — por agente, el
 *      conjunto HONESTO que el runtime ejecuta de verdad: cada asignación con
 *      su `executable_in_runtime`, el set efectivo y los avisos legibles
 *      ("asignada pero no ejecutable", shell_exec sin allowed_commands…).
 *
 * Por qué effective-tools y no solo el snapshot del proyecto: la verificación
 * tiene que responder "qué ejecuta REALMENTE el agente", no solo "qué filas
 * agent_tools existen". El endpoint cruza agente ∩ runtime-wired y expone los
 * avisos; el snapshot de proyecto solo lista asignaciones.
 *
 * Taxonomía visual: importada de `@/lib/tools/taxonomy` (fuente ÚNICA, ADR
 * 0049) — la MISMA tool muestra idéntico label/variant aquí y en la pantalla
 * de asignación. NUNCA se renderiza el enum crudo en inglés.
 *
 * Es read-only: no edita nada. Banner "Solo lectura — verificación" arriba.
 */

import { useParams } from "next/navigation";
import { useQueries, useQuery } from "@tanstack/react-query";
import { Bot, Eye, Plug, Wrench } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { AvailableCapabilitiesSection } from "@/components/marketplace/available-capabilities-section";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";
import { useLang, type Lang } from "@/lib/lang-context";
import { resolveCategory, resolveImpl, resolveSecurity } from "@/lib/tools/taxonomy";

// --------------------------------------------------------------------------
// Types — project snapshot (mirror api_server.routers.tools_diagnostic)
// --------------------------------------------------------------------------
interface ToolDiagnostic {
  id: string;
  name: string;
  description: string | null;
  category: string;
  implementation_type: string;
  security_level: string;
  timeout_seconds: number;
  executable_in_runtime: boolean;
}

interface McpServerDiagnostic {
  name: string;
  transport: string;
  has_auth: boolean;
}

interface AgentDiagnostic {
  id: string;
  name: string;
  role: string;
  scope: string;
  tools: ToolDiagnostic[];
}

interface AgentToolsDiagnosticResponse {
  project_id: string;
  agents: AgentDiagnostic[];
  mcp_servers: McpServerDiagnostic[];
}

// --------------------------------------------------------------------------
// Types — per-agent effective-tools (mirror api_server.routers.agents
// EffectiveToolEntry / EffectiveToolsResponse, task_06_18_07)
// --------------------------------------------------------------------------
interface EffectiveToolEntry {
  tool_id: string;
  name: string;
  canonical_names: string[];
  category: string;
  implementation_type: string;
  security_level: string;
  is_builtin: boolean;
  executable_in_runtime: boolean;
}

interface EffectiveToolsResponse {
  agent_id: string;
  mode: string | null;
  assigned: EffectiveToolEntry[];
  effective: string[];
  unrestricted: boolean;
  shell_exec_effective: boolean;
  warnings: string[];
}

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function AgentToolsDiagnosticPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;
  const { lang } = useLang();

  const diagnosticQuery = useQuery({
    queryKey: ["project-agent-tools-diagnostic", projectId],
    queryFn: () =>
      apiFetch<AgentToolsDiagnosticResponse>(`/projects/${projectId}/agent-tools-diagnostic`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
  });

  const agents = diagnosticQuery.data?.agents ?? [];

  // One effective-tools call per agent. `useQueries` keeps them parallel and
  // individually cached; a failing agent doesn't blank the whole page.
  const effectiveQueries = useQueries({
    queries: agents.map((agent) => ({
      queryKey: ["agent-effective-tools", agent.id],
      queryFn: () => apiFetch<EffectiveToolsResponse>(`/agents/${agent.id}/effective-tools`),
      refetchOnWindowFocus: false,
      enabled: Boolean(agent.id),
    })),
  });

  const effectiveByAgent = new Map<string, EffectiveToolsResponse>();
  agents.forEach((agent, i) => {
    const data = effectiveQueries[i]?.data;
    if (data) effectiveByAgent.set(agent.id, data);
  });

  return (
    <div
      className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="agent-tools-diagnostic-page"
    >
      <ProjectBreadcrumb projectId={projectId} current="Tools por agente" />
      <PageHeader
        icon={<Wrench className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Diagnóstico de tools por agente"
        description="Lectura read-only de qué tools (builtin, MCP, http_endpoint, python_function, docker_command) ejecuta de verdad cada agente del proyecto."
        data-testid="agent-tools-diagnostic-header"
      />

      <ReadOnlyBanner />

      {diagnosticQuery.isLoading ? (
        <p className="text-muted-foreground mt-6 text-sm">Cargando…</p>
      ) : diagnosticQuery.isError ? (
        <p className="text-destructive mt-6 text-sm" data-testid="agent-tools-diagnostic-error">
          {diagnosticQuery.error instanceof ApiError
            ? diagnosticQuery.error.body
            : String(diagnosticQuery.error)}
        </p>
      ) : (
        <div className="mt-6 space-y-6">
          <McpServersCard servers={diagnosticQuery.data?.mcp_servers ?? []} />
          <AgentsSection agents={agents} effectiveByAgent={effectiveByAgent} lang={lang} />
        </div>
      )}

      {/* ADR 0142 (D4): la ÚNICA parte de esta pantalla que escribe. Va debajo
          del diagnóstico y con su propia cabecera para que no se confunda con
          él; el banner de arriba acota su «solo lectura» a lo diagnóstico. */}
      <AvailableCapabilitiesSection projectId={projectId} kinds={["tool", "skill"]} />
    </div>
  );
}

// --------------------------------------------------------------------------
// Read-only verification banner
// --------------------------------------------------------------------------
function ReadOnlyBanner() {
  return (
    <div
      role="note"
      className="bg-info-soft text-info-soft-foreground mt-4 flex items-center gap-2 rounded-md border px-3 py-2 text-sm"
      data-testid="agent-tools-diagnostic-readonly-banner"
    >
      <Eye aria-hidden="true" className="h-4 w-4 shrink-0" />
      <span>
        <span className="font-medium">Solo lectura — verificación.</span> El diagnóstico refleja lo
        que el runtime ejecuta de verdad; para cambiar asignaciones edita las tools en la ficha del
        agente. (La sección del marketplace, al final de la página, sí activa capacidades en este
        proyecto.)
      </span>
    </div>
  );
}

// --------------------------------------------------------------------------
// MCP servers — one shared card at the top
// --------------------------------------------------------------------------
function McpServersCard({ servers }: { servers: McpServerDiagnostic[] }) {
  return (
    <Card data-testid="diagnostic-mcp-servers-card">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Plug className="h-4 w-4" />
          MCP servers del proyecto
        </CardTitle>
      </CardHeader>
      <CardContent>
        {servers.length === 0 ? (
          <p className="text-muted-foreground text-sm italic" data-testid="diagnostic-mcp-empty">
            Este proyecto no tiene MCP servers configurados.
          </p>
        ) : (
          <ul className="space-y-1.5" data-testid="diagnostic-mcp-list">
            {servers.map((s) => (
              <li
                key={s.name}
                className="border-muted flex items-center justify-between gap-2 rounded border px-3 py-2 text-sm"
                data-testid={`diagnostic-mcp-${s.name}`}
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono">{s.name}</span>
                  <Badge variant="info">{s.transport}</Badge>
                  {s.has_auth ? <Badge variant="muted">vault</Badge> : null}
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

// --------------------------------------------------------------------------
// Agents section — one card per project-local agent
// --------------------------------------------------------------------------
function AgentsSection({
  agents,
  effectiveByAgent,
  lang,
}: {
  agents: AgentDiagnostic[];
  effectiveByAgent: Map<string, EffectiveToolsResponse>;
  lang: Lang;
}) {
  if (agents.length === 0) {
    return (
      <Card>
        <CardContent className="py-10 text-center">
          <p className="text-muted-foreground text-sm italic" data-testid="diagnostic-agents-empty">
            Este proyecto no tiene agentes project-scoped declarados. Los agentes globales del
            tenant se pueden usar pero no aparecen aquí.
          </p>
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-3" data-testid="diagnostic-agents-list">
      {agents.map((agent) => (
        <AgentCard
          key={agent.id}
          agent={agent}
          effective={effectiveByAgent.get(agent.id)}
          lang={lang}
        />
      ))}
    </div>
  );
}

function AgentCard({
  agent,
  effective,
  lang,
}: {
  agent: AgentDiagnostic;
  effective: EffectiveToolsResponse | undefined;
  lang: Lang;
}) {
  // Prefer the honest effective-tools entries; fall back to the project
  // snapshot until that per-agent call resolves.
  const entries: ToolDiagnostic[] = effective
    ? effective.assigned.map((e) => {
        const snapshot = agent.tools.find((t) => t.id === e.tool_id);
        return {
          id: e.tool_id,
          name: e.name,
          description: snapshot?.description ?? null,
          category: e.category,
          implementation_type: e.implementation_type,
          security_level: e.security_level,
          timeout_seconds: snapshot?.timeout_seconds ?? 0,
          executable_in_runtime: e.executable_in_runtime,
        };
      })
    : agent.tools;

  return (
    <Card data-testid={`diagnostic-agent-card-${agent.id}`}>
      <CardHeader className="flex flex-row items-start justify-between gap-4">
        <div className="min-w-0">
          <CardTitle className="flex items-center gap-2">
            <Bot className="h-4 w-4" />
            <span>{agent.name}</span>
            <Badge variant="info">{agent.role}</Badge>
            <Badge variant="muted">{agent.scope}</Badge>
          </CardTitle>
        </div>
        <span
          className="text-muted-foreground text-xs"
          data-testid={`diagnostic-agent-tool-count-${agent.id}`}
        >
          {entries.length} tool{entries.length === 1 ? "" : "s"}
        </span>
      </CardHeader>
      <CardContent>
        {effective?.unrestricted ? (
          <p
            className="text-muted-foreground text-sm italic"
            data-testid={`diagnostic-agent-unrestricted-${agent.id}`}
          >
            Este agente no tiene tools asignadas vía <code>agent_tools</code>. Sin asignaciones, el
            agente conserva el comportamiento por defecto del runtime (sin restricción por agente);
            no significa que ejecute todo el catálogo.
          </p>
        ) : entries.length === 0 ? (
          <p
            className="text-muted-foreground text-sm italic"
            data-testid={`diagnostic-agent-tools-empty-${agent.id}`}
          >
            Este agente no tiene tools asignadas vía <code>agent_tools</code>. Sin asignaciones, el
            agente conserva el comportamiento por defecto del runtime (sin restricción por agente);
            no significa que ejecute todo el catálogo.
          </p>
        ) : (
          <ul className="space-y-1.5" data-testid={`diagnostic-agent-tools-list-${agent.id}`}>
            {entries.map((tool) => (
              <ToolRow key={tool.id} tool={tool} lang={lang} />
            ))}
          </ul>
        )}

        {effective && effective.warnings.length > 0 && (
          <ul
            className="text-warning-soft-foreground mt-3 space-y-1 text-xs"
            data-testid={`diagnostic-agent-warnings-${agent.id}`}
          >
            {effective.warnings.map((w, i) => (
              <li key={i} className="flex gap-1.5">
                <span aria-hidden="true">•</span>
                <span>{w}</span>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ToolRow({ tool, lang }: { tool: ToolDiagnostic; lang: Lang }) {
  // SINGLE source of truth (ADR 0049): same resolvers as the assignment
  // screen, so the same tool shows identical label/variant in both — and the
  // raw enum is NEVER rendered.
  const impl = resolveImpl(tool.implementation_type, lang);
  const sec = resolveSecurity(tool.security_level, lang);
  const cat = resolveCategory(tool.category, lang);
  const implLabel = lang === "es" ? impl.labelEs : impl.labelEn;
  const secLabel = lang === "es" ? sec.labelEs : sec.labelEn;
  const catLabel = lang === "es" ? cat.labelEs : cat.labelEn;
  return (
    <li
      className="border-muted flex items-center justify-between gap-3 rounded border px-3 py-2 text-sm"
      data-testid={`diagnostic-tool-${tool.id}`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{tool.name}</span>
          <Badge variant={impl.variant} title={impl.help}>
            {implLabel}
          </Badge>
          <Badge variant={sec.variant} title={sec.help}>
            {secLabel}
          </Badge>
          {tool.executable_in_runtime ? null : (
            <Badge variant="warning" data-testid={`diagnostic-tool-not-wired-${tool.id}`}>
              No disponible aún
            </Badge>
          )}
          {tool.timeout_seconds > 0 ? (
            <span className="text-muted-foreground text-xs">
              timeout {tool.timeout_seconds}s · {catLabel}
            </span>
          ) : (
            <span className="text-muted-foreground text-xs">{catLabel}</span>
          )}
        </div>
        {tool.description ? (
          <p className="text-muted-foreground mt-0.5 text-xs">{tool.description}</p>
        ) : null}
      </div>
    </li>
  );
}
