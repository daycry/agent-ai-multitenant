/**
 * Tipos y constantes del catálogo de proveedores LLM (ADR 0021/0028).
 *
 * Extraído de `page.tsx` al partir la pantalla (prod-16 `task_prod16_08`): la
 * página tenía 996 líneas y estos tipos los comparten las tres piezas (tabla,
 * diálogo de alta/edición y diálogo del Device Flow), así que vivir en el
 * fichero de una de ellas obligaría a las otras dos a importar de un hermano.
 *
 * Aquí NO hay texto de UI traducible salvo `KIND_LABEL`, y eso es deliberado:
 * "Claude Agent SDK", "GitHub Copilot", "Azure AI Foundry (APIM)" y "Ollama"
 * son nombres de producto, no cadenas que se traduzcan. Lo que sí se traduce
 * (columnas, badges, etiquetas de estado) vive en el namespace `llmProviders`
 * del diccionario.
 */

import type { BadgeVariant } from "@/components/ui/badge";
import type { MessageKey } from "@/lib/i18n";

// ---------------------------------------------------------------------------
// Types — mirror api_server.schemas.llm_providers + db.llm_providers enums.
// ---------------------------------------------------------------------------
export type ProviderKind = "claude_sdk" | "copilot" | "azure_foundry" | "ollama";

export interface LlmProvider {
  id: string;
  kind: string;
  slug: string;
  display_name: string;
  base_url: string | null;
  is_active: boolean;
  config: Record<string, unknown>;
  secret_vault_path: string | null;
  has_credential: boolean;
  created_at: string;
  updated_at: string;
}

// Mirror api_server.schemas.llm_providers.LLMProviderTestResponse +
// llm_providers.liveness.LivenessStatus.
export interface ProviderTestResult {
  ok: boolean;
  status: string;
  detail: string;
}

// Mirror api_server.schemas.copilot_device_flow.{DeviceFlowStartResponse,DeviceFlowPollResponse}.
export interface DeviceFlowStart {
  provider_id: string;
  device_code: string;
  user_code: string;
  verification_uri: string;
  expires_in: number;
  interval: number;
}

export interface DeviceFlowPoll {
  status: string;
  authorized: boolean;
  interval: number | null;
}

export const KINDS: ProviderKind[] = ["claude_sdk", "copilot", "azure_foundry", "ollama"];

/** Nombres de producto: no se traducen, por eso no están en el diccionario. */
export const KIND_LABEL: Record<ProviderKind, string> = {
  claude_sdk: "Claude Agent SDK",
  copilot: "GitHub Copilot",
  azure_foundry: "Azure AI Foundry (APIM)",
  ollama: "Ollama",
};

export const KIND_BADGE: Record<string, BadgeVariant> = {
  claude_sdk: "primary",
  copilot: "info",
  azure_foundry: "success",
  ollama: "warning",
};

/**
 * `status` clasificado por el backend → clave del diccionario que lo describe.
 *
 * El `status` en sí NO se traduce: es el identificador que viaja por la API y
 * el que aparece en logs. Lo que se traduce es la etiqueta que se pinta, y un
 * status desconocido cae a mostrar el identificador crudo (mejor pista de un
 * backend nuevo que un texto genérico que lo esconda).
 */
export const TEST_STATUS_KEY: Record<string, MessageKey<"llmProviders">> = {
  ok: "statusOk",
  auth_error: "statusAuthError",
  connection_error: "statusConnectionError",
  config_error: "statusConfigError",
  upstream_error: "statusUpstreamError",
};

export function isKind(value: string): value is ProviderKind {
  return (KINDS as string[]).includes(value);
}
