/**
 * Contrato de `GET /projects/{id}/capabilities` en lo que añade `task_ui_20`
 * (espeja `api_server.capabilities_project`). Los tres campos son opcionales en el
 * contrato general del Hub (agente y equipo no los traen); para un proyecto vienen
 * siempre. NUNCA inventar campos: el dominio no guarda un «último probado».
 */

export type McpOriginKind = "declared" | "marketplace";

export interface McpOrigin {
  kind: McpOriginKind;
  deployment_id: string | null;
  listing_id: string | null;
  listing_name: string | null;
  version: string | null;
}

export interface McpServerCapability {
  name: string;
  transport: string | null;
  url: string | null;
  origin: McpOrigin;
  oauth: { required: boolean; connected: boolean | null };
  imported_tools: number;
  egress: { blocked: boolean; host: string | null; code: string | null };
}

export interface DeployedCapability {
  deployment_id: string;
  listing_id: string;
  name: string;
  version: string;
  agents: number;
}

export interface ProjectCapabilities {
  mcp?: McpServerCapability[] | null;
  tools?: DeployedCapability[] | null;
  skills?: DeployedCapability[] | null;
}

export type CapabilityTab = "mcp" | "tools" | "skills";

export const CAPABILITY_TABS: readonly CapabilityTab[] = ["mcp", "tools", "skills"];

/** Los `kind` de listing del marketplace que gobierna cada pestaña. */
export const LISTING_KINDS: Record<CapabilityTab, string[]> = {
  mcp: ["mcp_server"],
  tools: ["tool"],
  skills: ["skill"],
};

/** Pestaña pedida por `?tab=`; cualquier otra cosa cae en MCP. */
export function parseTab(raw: string | null | undefined): CapabilityTab {
  return CAPABILITY_TABS.find((tab) => tab === raw) ?? "mcp";
}

/**
 * Procedencia de un servidor como datos de presentación: `declared`, o el listing y
 * la versión desplegada. Una fila `marketplace` sin nombre de listing degrada a
 * `declared` (no se enseña una procedencia a medias).
 */
export function provenanceOf(
  origin: McpOrigin,
): { kind: "declared" } | { kind: "marketplace"; listing: string; version: string } {
  if (origin.kind === "marketplace" && origin.listing_name) {
    return { kind: "marketplace", listing: origin.listing_name, version: origin.version ?? "?" };
  }
  return { kind: "declared" };
}
