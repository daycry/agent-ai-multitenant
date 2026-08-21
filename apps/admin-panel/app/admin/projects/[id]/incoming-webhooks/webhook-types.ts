/**
 * Contratos y catálogos de los webhooks ENTRANTES del proyecto — espejo de
 * `api_server.schemas.incoming_webhooks`.
 *
 * Viven aparte desde prod-16 (`task_prod16_03`) por la misma razón que los de
 * `settings/sso`: la pantalla y el diálogo comparten UNA definición, y cuando
 * cada pieza redeclara la suya el drift contra el backend no lo ve nadie.
 */

import type { BadgeVariant } from "@/components/ui/badge";

export type Origin = "github" | "gitlab" | "jira" | "sentry" | "linear" | "generic";
export type ActionKind = "create_task" | "comment" | "escalate";

export interface ActionMappingRule {
  event_type: string;
  action: ActionKind;
  title_template: string | null;
  body_template: string | null;
  target_task_id: string | null;
}

export interface WebhookConfig {
  id: string;
  project_id: string;
  origin: Origin;
  name: string;
  enabled: boolean;
  action_mappings: ActionMappingRule[];
  last_event_at: string | null;
  created_at: string;
  updated_at: string;
  incoming_path: string;
}

export interface WebhookConfigWithSecret extends WebhookConfig {
  signing_secret: string;
}

export interface WebhookDelivery {
  id: string;
  origin: string;
  delivery_id: string | null;
  event_type: string | null;
  verified: boolean;
  received_at: string;
}

// El `value` es el enum del backend y viaja en la URL pública del webhook: no
// se traduce. Lo que se traduce es la etiqueta, y por eso aquí vive su CLAVE
// del diccionario — así un origen nuevo no puede olvidarse el inglés.
export type OriginLabelKey =
  | "originGithub"
  | "originGitlab"
  | "originJira"
  | "originSentry"
  | "originLinear"
  | "originGeneric";

export const ORIGINS: { value: Origin; labelKey: OriginLabelKey }[] = [
  { value: "github", labelKey: "originGithub" },
  { value: "gitlab", labelKey: "originGitlab" },
  { value: "jira", labelKey: "originJira" },
  { value: "sentry", labelKey: "originSentry" },
  { value: "linear", labelKey: "originLinear" },
  { value: "generic", labelKey: "originGeneric" },
];

export const ORIGIN_BADGE: Record<Origin, BadgeVariant> = {
  github: "info",
  gitlab: "warning",
  jira: "info",
  sentry: "danger",
  linear: "muted",
  generic: "muted",
};

export const ACTIONS: {
  value: ActionKind;
  labelKey: "actionCreateTask" | "actionComment" | "actionEscalate";
}[] = [
  { value: "create_task", labelKey: "actionCreateTask" },
  { value: "comment", labelKey: "actionComment" },
  { value: "escalate", labelKey: "actionEscalate" },
];

export function emptyConfigForm(): {
  origin: Origin;
  name: string;
  enabled: boolean;
  action_mappings: ActionMappingRule[];
} {
  return { origin: "github", name: "", enabled: true, action_mappings: [] };
}

export function emptyRule(): ActionMappingRule {
  return {
    event_type: "*",
    action: "create_task",
    title_template: null,
    body_template: null,
    target_task_id: null,
  };
}
