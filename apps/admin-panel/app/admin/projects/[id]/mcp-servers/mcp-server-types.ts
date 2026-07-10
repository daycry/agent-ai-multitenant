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
  // ...other Project fields exist; we don't touch them
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
