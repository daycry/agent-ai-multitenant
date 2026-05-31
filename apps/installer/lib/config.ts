/**
 * Captured installer configuration — wizard steps 2-6 (Plan 15 task_15_03).
 *
 * Mirrors the backend Pydantic models in
 * `apps/installer/backend/src/installer_backend/config.py`. Steps 2-6 capture:
 *
 *   2. basics    — system config: domain + environment.
 *   3. resources — resource allocation + GPU enablement.
 *   4. storage   — data root + MinIO object storage.
 *   5. providers — the four ADR-0021 LLM providers (Claude SDK / Copilot /
 *                  Azure Foundry APIM / Ollama).
 *   6. tenant    — initial tenant (name + admin email).
 *
 * Client-side validation gives fast feedback; the backend validates again
 * (`/api/config/validate`) and is authoritative. Secrets are write-only: they
 * live in the wizard state only until POSTed, are never displayed once typed
 * back, and the backend never echoes them.
 */

import { INSTALLER_API_BASE } from "./prereqs";

export type Environment = "development" | "staging" | "production";

export interface SystemConfig {
  domain: string;
  environment: Environment;
}

export interface ResourceConfig {
  workerReplicas: number;
  workerMemoryGib: number;
  gpuEnabled: boolean;
}

export interface StorageConfig {
  dataRoot: string;
  minioBucket: string;
  minioAccessKey: string;
  /** Write-only: held only until POST, never re-displayed. */
  minioSecretKey: string;
}

export interface ClaudeSdkProvider {
  enabled: boolean;
  oauthToken: string;
}

export interface CopilotProvider {
  enabled: boolean;
  oauthToken: string;
}

export interface AzureFoundryProvider {
  enabled: boolean;
  apimEndpoint: string;
  apiKey: string;
}

export interface OllamaProvider {
  enabled: boolean;
  endpoint: string;
}

export interface ProvidersConfig {
  claudeSdk: ClaudeSdkProvider;
  copilot: CopilotProvider;
  azureFoundry: AzureFoundryProvider;
  ollama: OllamaProvider;
}

export interface TenantConfig {
  tenantName: string;
  adminEmail: string;
}

/** The full steps 2-6 capture held in wizard state. */
export interface InstallerConfig {
  system: SystemConfig;
  resources: ResourceConfig;
  storage: StorageConfig;
  providers: ProvidersConfig;
  tenant: TenantConfig;
}

/** A wizard step that captures config (2-6). */
export type ConfigStepId = "basics" | "resources" | "storage" | "providers" | "tenant";

const CONFIG_STEP_IDS: readonly ConfigStepId[] = [
  "basics",
  "resources",
  "storage",
  "providers",
  "tenant",
];

/** Type guard: is *step* one of the config-capture steps (2-6)? */
export function isConfigStep(step: string): step is ConfigStepId {
  return (CONFIG_STEP_IDS as readonly string[]).includes(step);
}

/** Fresh, empty-but-typed config to seed the wizard. */
export function emptyConfig(): InstallerConfig {
  return {
    system: { domain: "", environment: "production" },
    resources: { workerReplicas: 2, workerMemoryGib: 4, gpuEnabled: false },
    storage: {
      dataRoot: "/data/agent-platform",
      minioBucket: "agentic-platform",
      minioAccessKey: "",
      minioSecretKey: "",
    },
    providers: {
      claudeSdk: { enabled: false, oauthToken: "" },
      copilot: { enabled: false, oauthToken: "" },
      azureFoundry: { enabled: false, apimEndpoint: "", apiKey: "" },
      ollama: { enabled: false, endpoint: "" },
    },
    tenant: { tenantName: "", adminEmail: "" },
  };
}

// ---------------------------------------------------------------------------
// Client-side validation. One map of field -> message per step; an empty map
// means the step is valid. Mirrors the backend rules for fast feedback only —
// the backend is authoritative.
// ---------------------------------------------------------------------------
export type FieldErrors = Readonly<Record<string, string>>;

// Hostname/FQDN or IP, no scheme/path. Permissive but rejects spaces & schemes.
const HOSTNAME_RE =
  /^(?=.{1,253}$)(?!-)[A-Za-z0-9-]{1,63}(?<!-)(\.(?!-)[A-Za-z0-9-]{1,63}(?<!-))*$/;
const IPV4_RE = /^(\d{1,3}\.){3}\d{1,3}$/;
const POSIX_ABS_PATH_RE = /^\/[^\0]*$/;
const BUCKET_RE = /^[a-z0-9][a-z0-9-]{1,61}[a-z0-9]$/;
const HTTP_URL_RE = /^https?:\/\/\S+$/i;
// Pragmatic email check; the backend uses a full validator.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function isValidHost(value: string): boolean {
  return HOSTNAME_RE.test(value) || IPV4_RE.test(value);
}

export function validateSystem(system: SystemConfig): FieldErrors {
  const errors: Record<string, string> = {};
  const domain = system.domain.trim();
  if (!domain) {
    errors.domain = "El dominio es obligatorio.";
  } else if (!isValidHost(domain.toLowerCase())) {
    errors.domain = "Debe ser un host válido (p. ej. agentic.example.com), sin esquema ni ruta.";
  }
  return errors;
}

export function validateResources(resources: ResourceConfig): FieldErrors {
  const errors: Record<string, string> = {};
  if (
    !Number.isInteger(resources.workerReplicas) ||
    resources.workerReplicas < 1 ||
    resources.workerReplicas > 64
  ) {
    errors.workerReplicas = "Las réplicas de worker deben estar entre 1 y 64.";
  }
  if (
    !Number.isInteger(resources.workerMemoryGib) ||
    resources.workerMemoryGib < 1 ||
    resources.workerMemoryGib > 512
  ) {
    errors.workerMemoryGib = "La memoria por worker debe estar entre 1 y 512 GiB.";
  }
  return errors;
}

export function validateStorage(storage: StorageConfig): FieldErrors {
  const errors: Record<string, string> = {};
  if (!POSIX_ABS_PATH_RE.test(storage.dataRoot.trim())) {
    errors.dataRoot = "La ruta de datos debe ser absoluta (p. ej. /data/agent-platform).";
  }
  if (!BUCKET_RE.test(storage.minioBucket.trim().toLowerCase())) {
    errors.minioBucket = "Bucket inválido: 3-63 caracteres, minúsculas, dígitos y guiones.";
  }
  if (storage.minioAccessKey.trim().length < 3) {
    errors.minioAccessKey = "La access key de MinIO es obligatoria (mín. 3 caracteres).";
  }
  if (storage.minioSecretKey.length < 8) {
    errors.minioSecretKey = "La secret key de MinIO debe tener al menos 8 caracteres.";
  }
  return errors;
}

export function validateProviders(providers: ProvidersConfig): FieldErrors {
  const errors: Record<string, string> = {};
  const anyEnabled =
    providers.claudeSdk.enabled ||
    providers.copilot.enabled ||
    providers.azureFoundry.enabled ||
    providers.ollama.enabled;
  if (!anyEnabled) {
    errors.providers = "Debes habilitar al menos un proveedor LLM (ADR-0021).";
  }
  if (providers.claudeSdk.enabled && !providers.claudeSdk.oauthToken.trim()) {
    errors["claudeSdk.oauthToken"] = "Claude SDK requiere un token OAuth.";
  }
  if (providers.copilot.enabled && !providers.copilot.oauthToken.trim()) {
    errors["copilot.oauthToken"] = "GitHub Copilot requiere un token OAuth.";
  }
  if (providers.azureFoundry.enabled) {
    if (!HTTP_URL_RE.test(providers.azureFoundry.apimEndpoint.trim())) {
      errors["azureFoundry.apimEndpoint"] =
        "Azure AI Foundry requiere un endpoint APIM http(s) válido.";
    }
    if (!providers.azureFoundry.apiKey.trim()) {
      errors["azureFoundry.apiKey"] = "Azure AI Foundry requiere la API key del APIM.";
    }
  }
  if (providers.ollama.enabled && !HTTP_URL_RE.test(providers.ollama.endpoint.trim())) {
    errors["ollama.endpoint"] = "Ollama requiere un endpoint http(s) válido.";
  }
  return errors;
}

export function validateTenant(tenant: TenantConfig): FieldErrors {
  const errors: Record<string, string> = {};
  if (tenant.tenantName.trim().length < 2) {
    errors.tenantName = "El nombre del tenant debe tener al menos 2 caracteres.";
  }
  if (!EMAIL_RE.test(tenant.adminEmail.trim())) {
    errors.adminEmail = "El email del administrador no es válido.";
  }
  return errors;
}

/** Validate one config step. Returns the (possibly empty) field-error map. */
export function validateStep(step: ConfigStepId, config: InstallerConfig): FieldErrors {
  switch (step) {
    case "basics":
      return validateSystem(config.system);
    case "resources":
      return validateResources(config.resources);
    case "storage":
      return validateStorage(config.storage);
    case "providers":
      return validateProviders(config.providers);
    case "tenant":
      return validateTenant(config.tenant);
  }
}

export function hasErrors(errors: FieldErrors): boolean {
  return Object.keys(errors).length > 0;
}

// ---------------------------------------------------------------------------
// Backend validation response (secret-free) + POST helper.
// ---------------------------------------------------------------------------
export interface BackendFieldError {
  readonly field: string;
  readonly message: string;
}

export interface ProvidersSummary {
  readonly claude_sdk_enabled: boolean;
  readonly claude_sdk_token_set: boolean;
  readonly copilot_enabled: boolean;
  readonly copilot_token_set: boolean;
  readonly azure_foundry_enabled: boolean;
  readonly azure_foundry_key_set: boolean;
  readonly ollama_enabled: boolean;
}

export interface ConfigValidationResponse {
  readonly valid: boolean;
  readonly errors: readonly BackendFieldError[];
  readonly normalized: Readonly<Record<string, unknown>>;
  readonly providers: ProvidersSummary | null;
}

/**
 * Serialise the wizard config to the backend wire shape (snake_case, with the
 * provider sub-objects). Secrets travel here ONCE on POST and are never read
 * back from the response.
 */
export function toWireConfig(config: InstallerConfig): Record<string, unknown> {
  return {
    system: { domain: config.system.domain.trim(), environment: config.system.environment },
    resources: {
      worker_replicas: config.resources.workerReplicas,
      worker_memory_gib: config.resources.workerMemoryGib,
      gpu_enabled: config.resources.gpuEnabled,
    },
    storage: {
      data_root: config.storage.dataRoot.trim(),
      minio_bucket: config.storage.minioBucket.trim(),
      minio_access_key: config.storage.minioAccessKey.trim(),
      minio_secret_key: config.storage.minioSecretKey,
    },
    providers: {
      claude_sdk: providerWire(config.providers.claudeSdk.enabled, {
        oauth_token: config.providers.claudeSdk.oauthToken,
      }),
      copilot: providerWire(config.providers.copilot.enabled, {
        oauth_token: config.providers.copilot.oauthToken,
      }),
      azure_foundry: providerWire(config.providers.azureFoundry.enabled, {
        apim_endpoint: config.providers.azureFoundry.apimEndpoint.trim() || null,
        api_key: config.providers.azureFoundry.apiKey,
      }),
      ollama: providerWire(config.providers.ollama.enabled, {
        endpoint: config.providers.ollama.endpoint.trim() || null,
      }),
    },
    tenant: {
      tenant_name: config.tenant.tenantName.trim(),
      admin_email: config.tenant.adminEmail.trim(),
    },
  };
}

/** Only send credential fields when the provider is enabled + the value set. */
function providerWire(
  enabled: boolean,
  fields: Record<string, string | null>,
): Record<string, unknown> {
  const wire: Record<string, unknown> = { enabled };
  for (const [key, value] of Object.entries(fields)) {
    if (enabled && value !== null && value !== "") {
      wire[key] = value;
    }
  }
  return wire;
}

/** POST the captured config to the backend for authoritative validation. */
export async function postConfigValidate(
  config: InstallerConfig,
  signal?: AbortSignal,
): Promise<ConfigValidationResponse> {
  const resp = await fetch(`${INSTALLER_API_BASE}/api/config/validate`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify(toWireConfig(config)),
    signal,
  });
  if (!resp.ok && resp.status !== 422) {
    throw new Error(`config validation failed: HTTP ${resp.status}`);
  }
  return (await resp.json()) as ConfigValidationResponse;
}
