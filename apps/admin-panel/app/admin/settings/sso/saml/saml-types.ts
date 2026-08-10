/**
 * Contratos de la pantalla SAML — espejo de `api_server.schemas.sso` (mitad SAML).
 *
 * Compartidos desde el troceo de `task_prod16_08` por la página, la tarjeta de
 * metadatos del SP, la ficha y el diálogo.
 */

export type KeySource = "vault" | "encrypted";

export interface SamlConfig {
  id: string;
  provider: string;
  display_name: string | null;
  enabled: boolean;
  idp_entity_id: string;
  idp_sso_url: string;
  idp_x509_cert: string;
  name_id_format: string;
  attribute_mappings: Record<string, string>;
  sp_x509_cert: string | null;
  has_sp_private_key: boolean;
  sp_private_key_source: KeySource | null;
  authn_requests_signed: boolean;
  want_assertions_signed: boolean;
  want_assertions_encrypted: boolean;
  want_name_id_encrypted: boolean;
  created_at: string;
  updated_at: string;
}

export interface SpMetadata {
  sp_entity_id: string;
  acs_url: string;
}

export interface ParsedIdpMetadata {
  entity_id: string;
  sso_url: string;
  x509_cert: string;
  name_id_format: string | null;
}

// Body of POST/PUT /auth/sso/saml/config. `sp_private_key` is omitted on
// an edit that keeps the existing key.
export interface UpsertBody {
  display_name: string | null;
  enabled: boolean;
  idp_entity_id: string;
  idp_sso_url: string;
  idp_x509_cert: string;
  name_id_format: string;
  attribute_mappings: Record<string, string>;
  sp_x509_cert: string | null;
  sp_private_key?: string;
  authn_requests_signed: boolean;
  want_assertions_signed: boolean;
  want_assertions_encrypted: boolean;
  want_name_id_encrypted: boolean;
}

export const KEY_SOURCE_LABEL: Record<KeySource, string> = {
  vault: "Vault",
  encrypted: "cifrado en reposo",
};

// The closed NameID-format picker the UI offers (the API accepts any
// non-empty URN, but these cover the overwhelming majority of IdPs).
export const NAME_ID_FORMATS: { value: string; label: string }[] = [
  {
    value: "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
    label: "emailAddress (recomendado)",
  },
  {
    value: "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent",
    label: "persistent",
  },
  {
    value: "urn:oasis:names:tc:SAML:2.0:nameid-format:transient",
    label: "transient",
  },
  {
    value: "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified",
    label: "unspecified",
  },
];

export interface FormState {
  display_name: string;
  idp_entity_id: string;
  idp_sso_url: string;
  idp_x509_cert: string;
  name_id_format: string;
  attribute_email: string;
  attribute_full_name: string;
  sp_x509_cert: string;
  sp_private_key: string;
  authn_requests_signed: boolean;
  want_assertions_signed: boolean;
  want_assertions_encrypted: boolean;
  want_name_id_encrypted: boolean;
  enabled: boolean;
}

export const DEFAULT_NAME_ID = "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress";
