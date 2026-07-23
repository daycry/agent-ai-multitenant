// Tipos y helpers puros de los MCP servers por proyecto (tramo #9, extracción
// verbatim del monolito page.tsx — auditoría 2026-07-10). Espejan
// api_server.mcp.config.MCPServerConfigModel y el catálogo de templates
// (shared_mcp.catalog vía GET /mcp-catalog). Sin JSX ni hooks.

import { type BadgeVariant } from "@/components/ui/badge";

// --------------------------------------------------------------------------
// Types (mirror api_server.mcp.config.MCPServerConfigModel)
// --------------------------------------------------------------------------
export type Transport = "stdio" | "sse" | "streamable_http";

export interface McpServerConfig {
  name: string;
  transport: Transport;
  command: string | null;
  args: string[];
  env: Record<string, string>;
  url: string | null;
  headers: Record<string, string>;
  auth_ref: string | null;
  timeout_s: number;
}

export interface ProjectResponse {
  id: string;
  name: string;
  mcp_servers: McpServerConfig[];
  // ADR 0128 fase 2/4: política OPCIONAL rol→tool de las MCP del proyecto.
  // Mapea nombre de tool MCP (`<server>.<tool>`) → roles de agente autorizados.
  // `{}` (default) = sin política: todo agente del proyecto ve toda tool MCP.
  // Ausente en respuestas antiguas → se trata como `{}`.
  mcp_tool_roles?: Record<string, string[]>;
  // ...other Project fields exist; we don't touch them
}

// ---------------------------------------------------------------------------
// ADR 0128 fase 4 — editor de política rol→tool de las MCP del proyecto
// ---------------------------------------------------------------------------
//
// Las tools MCP las aporta el PROYECTO en runtime (no se conceden por-agente).
// El operador puede opcionalmente restringir CADA tool MCP a un subconjunto de
// roles de agente; sin entrada, la tool queda abierta a todos los roles.

/** Roles de agente elegibles en la política (espeja ROLE_OPTIONS del Hub de agente). */
export const AGENT_ROLES = [
  "project_manager",
  "architect",
  "backend_dev",
  "frontend_dev",
  "qa",
  "reviewer",
  "devops",
  "security",
  "technical_writer",
  "specialist",
] as const;

export type AgentRole = (typeof AGENT_ROLES)[number];

/** Etiqueta humana (ES) de cada rol para el multi-select. */
export const ROLE_LABEL: Record<AgentRole, string> = {
  project_manager: "Project Manager",
  architect: "Arquitecto",
  backend_dev: "Backend Dev",
  frontend_dev: "Frontend Dev",
  qa: "QA",
  reviewer: "Reviewer",
  devops: "DevOps",
  security: "Security",
  technical_writer: "Technical Writer",
  specialist: "Especialista",
};

/**
 * Forma mínima de una tool del catálogo (`GET /tools`) que el editor de
 * política necesita. Espeja `api_server.schemas.catalog.ToolResponse` en lo
 * imprescindible: identificamos las tools MCP del proyecto por
 * `implementation_type === "mcp_tool"` (o `category === "mcp"`) cuyo prefijo de
 * namespacing `<server>` coincida con un server declarado en el proyecto.
 */
export interface CatalogToolLite {
  id: string;
  name: string;
  description: string | null;
  category: string;
  implementation_type: string;
}

/** True si la tool del catálogo es una tool MCP (aportada por un server MCP). */
export function isMcpTool(tool: { implementation_type: string; category: string }): boolean {
  return tool.implementation_type === "mcp_tool" || tool.category === "mcp";
}

/** El prefijo `<server>` de un nombre MCP namespaced `<server>.<tool>`, o null. */
export function mcpServerPrefix(toolName: string): string | null {
  return toolName.includes(".") ? toolName.split(".", 1)[0] : null;
}

export const TRANSPORT_LABEL: Record<Transport, string> = {
  stdio: "stdio (subprocess)",
  sse: "sse (HTTP stream)",
  streamable_http: "streamable_http",
};

export const TRANSPORT_BADGE: Record<Transport, BadgeVariant> = {
  stdio: "info",
  sse: "warning",
  streamable_http: "success",
};

// Empty MCPServerConfigModel — used to seed the dialog form for "create".
export function emptyServer(): McpServerConfig {
  return {
    name: "",
    transport: "stdio",
    command: "",
    args: [],
    env: {},
    url: null,
    headers: {},
    auth_ref: null,
    timeout_s: 30,
  };
}

// ---------------------------------------------------------------------------
// Catálogo de plantillas — viene del backend (`GET /mcp-catalog`) que
// proyecta `shared_mcp.catalog.CATALOG` (22 templates verificadas:
// GitHub, GitLab, Jira, Confluence, Google Drive, Gmail, Calendar,
// Slack, Teams, Discord, Notion, PostgreSQL, Sentry, Grafana, Brave,
// Tavily, Puppeteer, Memory, Sequential Thinking, etc.).
//
// El backend es la fuente de verdad — añadir/quitar templates es un
// ADR + cambio en `catalog.py`, no se hardcoded aquí.
// ---------------------------------------------------------------------------
export interface McpCatalogEntry {
  id: string;
  display_name: string;
  description: string;
  transport: Transport;
  command: string | null;
  args: string[];
  url: string | null;
  secret_keys: string[];
  vault_path_template: string | null;
  default_timeout_s: number;
  static_env: Record<string, string>;
  static_headers: Record<string, string>;
  maintainer: string;
  repo_url: string;
  docs_url: string;
  category: string;
  requires_auth: boolean;
}

export const CATEGORY_LABEL: Record<string, string> = {
  docs: "Documentos",
  scm: "Control de versiones",
  data: "Bases de datos",
  files: "Archivos",
  comms: "Comunicación",
  issues: "Issue trackers",
  observability: "Observabilidad",
  search: "Búsqueda web",
  browser: "Navegador",
  meta: "Meta / Agent helpers",
  other: "Otros",
};

/**
 * Render the template's `vault_path_template` against the project's
 * UUID. Mirrors `shared_mcp.catalog.render_vault_path` on the backend —
 * the substitution is just `{project_id} → projectId`. We pre-fill
 * `auth_ref` with this rendered path when the template declares
 * secrets, so the operator sees the exact place where their Vault
 * admin needs to drop the credential (instead of typing it from
 * scratch and risking a typo against the validator).
 */
export function templateToConfig(entry: McpCatalogEntry, projectId: string): McpServerConfig {
  const authRef =
    entry.vault_path_template !== null
      ? entry.vault_path_template.replace("{project_id}", projectId)
      : null;
  return {
    name: entry.id,
    transport: entry.transport,
    command: entry.command,
    args: [...entry.args],
    env: { ...entry.static_env },
    url: entry.url,
    headers: { ...entry.static_headers },
    auth_ref: authRef,
    timeout_s: Math.round(entry.default_timeout_s),
  };
}
