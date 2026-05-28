"use client";

/**
 * task_05_15 — Panel diagnóstico de tools por agente.
 *
 * Llama `GET /projects/{id}/agent-tools-diagnostic` (task_05_15
 * backend) y renderiza, por cada agente project-scoped del
 * proyecto:
 *
 *   - card del agente con name + role + scope
 *   - lista de Tool rows wired al agente (a través del junction
 *     `agent_tools`), con un badge por `implementation_type`
 *     (builtin / mcp_tool / http_endpoint / python_function /
 *     docker_command) + el security_level y timeout
 *
 * Y aparte, en una segunda card al inicio, los MCP servers
 * configurados a nivel proyecto — esos son compartidos entre los
 * agentes, no se duplican por entrada.
 *
 * Es read-only: no edita nada. La idea es responder a "por que el
 * agente X esta llamando Y, o por que NO tiene acceso a Z" sin
 * mirar tablas.
 */

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Bot, Plug, Wrench } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { ProjectBreadcrumb } from "@/components/layout/breadcrumb";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError, apiFetch } from "@/lib/api";

// --------------------------------------------------------------------------
// Types (mirror api_server.routers.tools_diagnostic)
// --------------------------------------------------------------------------
interface ToolDiagnostic {
  id: string;
  name: string;
  description: string | null;
  category: string;
  implementation_type: string;
  security_level: string;
  timeout_seconds: number;
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

const IMPL_BADGE: Record<string, BadgeVariant> = {
  builtin: "muted",
  mcp_tool: "success",
  http_endpoint: "info",
  python_function: "warning",
  docker_command: "danger",
};

const SECURITY_BADGE: Record<string, BadgeVariant> = {
  safe: "success",
  sensitive: "warning",
  privileged: "danger",
};

// --------------------------------------------------------------------------
// Page
// --------------------------------------------------------------------------
export default function AgentToolsDiagnosticPage() {
  const params = useParams<{ id: string }>();
  const projectId = params.id;

  const diagnosticQuery = useQuery({
    queryKey: ["project-agent-tools-diagnostic", projectId],
    queryFn: () =>
      apiFetch<AgentToolsDiagnosticResponse>(`/projects/${projectId}/agent-tools-diagnostic`),
    refetchOnWindowFocus: false,
    enabled: Boolean(projectId),
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
        description="Lectura read-only de qué tools (builtin, MCP, http_endpoint, python_function, docker_command) ve cada agente del proyecto."
        data-testid="agent-tools-diagnostic-header"
      />

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
          <AgentsSection agents={diagnosticQuery.data?.agents ?? []} />
        </div>
      )}
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
function AgentsSection({ agents }: { agents: AgentDiagnostic[] }) {
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
        <AgentCard key={agent.id} agent={agent} />
      ))}
    </div>
  );
}

function AgentCard({ agent }: { agent: AgentDiagnostic }) {
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
          {agent.tools.length} tool{agent.tools.length === 1 ? "" : "s"}
        </span>
      </CardHeader>
      <CardContent>
        {agent.tools.length === 0 ? (
          <p
            className="text-muted-foreground text-sm italic"
            data-testid={`diagnostic-agent-tools-empty-${agent.id}`}
          >
            Este agente no tiene tools wired. (Los builtin como echo, http_request o memory_recall
            están disponibles siempre; sólo aparecen aquí los Tool rows wired vía
            <code>agent_tools</code>.)
          </p>
        ) : (
          <ul className="space-y-1.5" data-testid={`diagnostic-agent-tools-list-${agent.id}`}>
            {agent.tools.map((tool) => (
              <ToolRow key={tool.id} tool={tool} />
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function ToolRow({ tool }: { tool: ToolDiagnostic }) {
  const implVariant = IMPL_BADGE[tool.implementation_type] ?? "muted";
  const secVariant = SECURITY_BADGE[tool.security_level] ?? "muted";
  return (
    <li
      className="border-muted flex items-center justify-between gap-3 rounded border px-3 py-2 text-sm"
      data-testid={`diagnostic-tool-${tool.id}`}
    >
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono">{tool.name}</span>
          <Badge variant={implVariant}>{tool.implementation_type}</Badge>
          <Badge variant={secVariant}>{tool.security_level}</Badge>
          <span className="text-muted-foreground text-xs">
            timeout {tool.timeout_seconds}s · {tool.category}
          </span>
        </div>
        {tool.description ? (
          <p className="text-muted-foreground mt-0.5 text-xs">{tool.description}</p>
        ) : null}
      </div>
    </li>
  );
}
