/**
 * Contratos de la pantalla de SSO OIDC — espejo de `api_server.schemas.sso`.
 *
 * Viven aparte desde el troceo de `task_prod16_08` para que la página, la
 * tarjeta de callback, la ficha y el diálogo compartan UNA definición: cuando
 * cada pieza redeclaraba la suya, el drift contra el backend no lo veía nadie.
 */

// --------------------------------------------------------------------------
// Types — mirror api_server.schemas.sso
// --------------------------------------------------------------------------
export type SecretSource = "vault" | "encrypted";

export interface SsoConfig {
  id: string;
  provider: string;
  display_name: string | null;
  enabled: boolean;
  issuer: string;
  client_id: string;
  scopes: string[];
  claim_mappings: Record<string, string>;
  has_client_secret: boolean;
  client_secret_source: SecretSource | null;
  created_at: string;
  updated_at: string;
}

export interface OidcTemplate {
  template_id: string;
  display_name: string;
  issuer_template: string;
  default_scopes: string[];
  claim_mappings: Record<string, string>;
  required_params: string[];
  notes: string | null;
}

export interface CallbackUrl {
  callback_url: string;
}

// Body of POST/PUT /auth/sso/config. `client_secret` is omitted on an
// edit that keeps the existing secret.
export interface UpsertBody {
  display_name: string | null;
  enabled: boolean;
  issuer: string;
  client_id: string;
  client_secret?: string;
  scopes: string[];
  claim_mappings: Record<string, string>;
}

export const SECRET_SOURCE_LABEL: Record<SecretSource, string> = {
  vault: "Vault",
  encrypted: "cifrado en reposo",
};

// --------------------------------------------------------------------------
// Callback URL card — the redirect URI to register at the IdP
// --------------------------------------------------------------------------
export interface PublicBaseUrl {
  base_url: string;
  is_override: boolean;
  env_default: string;
}

export interface ApiPathPrefix {
  prefix: string;
  is_override: boolean;
  env_default: string;
}

/** Estado del formulario de alta/edición (el diálogo lo mantiene en local). */
export interface FormState {
  display_name: string;
  issuer: string;
  client_id: string;
  client_secret: string;
  scopes: string;
  enabled: boolean;
}
