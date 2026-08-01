/**
 * Tipos y constantes de la configuración de notificaciones (prod-16
 * `task_prod16_08`).
 *
 * Extracción verbatim del `page.tsx` de 831 líneas. Espejo de
 * `api_server.schemas.notifications`: si el backend cambia un campo, esto es lo
 * único que hay que tocar en el panel.
 */

export type ChannelScope = "tenant" | "user";
export type SecretSource = "vault" | "encrypted";

export interface PlatformChannelTypes {
  enabled: string[];
  available: string[];
}

export interface NotificationChannel {
  id: string;
  scope: string;
  channel_type: string;
  name: string;
  enabled: boolean;
  config: Record<string, unknown>;
  owner_user_id: string | null;
  has_secret: boolean;
  secret_source: SecretSource | null;
  created_at: string;
  updated_at: string;
}

export interface NotificationPreference {
  id: string;
  scope: string;
  event_type: string;
  channel_type: string;
  enabled: boolean;
  owner_user_id: string | null;
  quiet_hours_start: number | null;
  quiet_hours_end: number | null;
  quiet_hours_tz: string | null;
}

export interface ChannelCreateBody {
  scope: ChannelScope;
  channel_type: string;
  name: string;
  enabled: boolean;
  config: Record<string, unknown>;
  secret?: string;
}

export interface ChannelUpdateBody {
  name?: string;
  enabled?: boolean;
  config?: Record<string, unknown>;
  secret?: string;
}

export interface PreferenceUpsertBody {
  scope: ChannelScope;
  event_type: string;
  channel_type: string;
  enabled: boolean;
}

export const SECRET_SOURCE_LABEL: Record<SecretSource, string> = {
  vault: "Vault",
  encrypted: "cifrado en reposo",
};

// NOTIF-3: el catálogo de eventos se sirve desde el backend
// (GET /notifications/event-catalog, en sync con el EVENT_REGISTRY real del
// dispatcher vía test). El hardcode anterior ofrecía 4 eventos, uno inexistente
// (review_needed). Fallback mínimo por si el endpoint falla.
export interface EventCatalogEntry {
  event_type: string;
  label_es: string;
  label_en: string;
}

export const EVENT_CATALOG_FALLBACK: EventCatalogEntry[] = [
  { event_type: "task_blocked", label_es: "Tarea bloqueada", label_en: "Task blocked" },
  { event_type: "budget_alert", label_es: "Alerta de presupuesto", label_en: "Budget alert" },
];

export interface ChannelFormState {
  scope: ChannelScope;
  channel_type: string;
  name: string;
  enabled: boolean;
  config: string;
  secret: string;
}

/** Estado inicial del formulario: alta en blanco, o el canal que se edita. */
export function channelToForm(
  channel: NotificationChannel | null,
  enabledTypes: string[],
): ChannelFormState {
  if (channel === null) {
    return {
      scope: "tenant",
      channel_type: enabledTypes[0] ?? "telegram",
      name: "",
      enabled: true,
      config: "{}",
      secret: "",
    };
  }
  return {
    scope: (channel.scope as ChannelScope) ?? "tenant",
    channel_type: channel.channel_type,
    name: channel.name,
    enabled: channel.enabled,
    config: JSON.stringify(channel.config ?? {}, null, 2),
    secret: "",
  };
}
