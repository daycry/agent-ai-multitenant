/**
 * Tipos y lógica pura del despliegue del marketplace (ADR 0142).
 *
 * Sin JSX ni hooks: aquí vive lo que las TRES puertas de despliegue (ficha de
 * la instalación, wizard de proyecto, pestañas del proyecto) comparten, para
 * que no puedan divergir. Las formas espejan
 * `api_server/schemas/marketplace_deployments.py`.
 *
 * ## Por qué hay un validador aquí si el backend ya valida
 *
 * El validador AUTORITATIVO es `marketplace/config_schema.py`, y sus mensajes
 * llegan en `detail.errors` de un 422. Éste sólo bloquea el submit antes de
 * gastar la ida y vuelta, y clava la misma semántica en los dos puntos que
 * muerden: un booleano NO satisface `integer` (en JS `true + 0 === 1`), y un
 * campo `secret: true` sólo acepta un puntero `vault:`.
 *
 * ## Por qué devuelve códigos y no frases
 *
 * El texto lo pone el diccionario i18n (`marketplaceDeploy`). Un validador que
 * devolviera castellano obligaría a este módulo a saber de idiomas y dejaría el
 * panel en ES con el toggle en EN, que es justo la deuda que prod-16 persigue.
 *
 * Y una regla sin excepción: **el error de un campo `secret` nunca lleva el
 * valor**. Un mensaje que ecoa el secreto lo copia al log.
 */

import {
  AGENT_ROLES,
  type AgentRole,
} from "@/app/admin/projects/[id]/mcp-servers/mcp-server-types";

// ---------------------------------------------------------------------------
// El dialecto del `config_schema` (espeja marketplace/config_schema.py)
// ---------------------------------------------------------------------------

/** Un campo del `config_schema`: "JSON-Schema-ish", pensado para pintar formulario. */
export interface ConfigSchemaField {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  items?: { enum?: unknown[] };
  minItems?: number;
  minimum?: number;
  maximum?: number;
  /** `true` ⇒ el valor sólo puede ser un puntero a Vault, jamás el secreto. */
  secret?: boolean;
  widget?: string;
}

export interface ConfigSchema {
  properties?: Record<string, ConfigSchemaField>;
  required?: string[];
}

/** Prefijo obligatorio de un puntero a Vault (mismo contrato que `auth_ref`). */
export const VAULT_POINTER_PREFIX = "vault:";

// ---------------------------------------------------------------------------
// Respuestas del backend
// ---------------------------------------------------------------------------

/** Una instalación del tenant que este proyecto AÚN no tiene desplegada. */
export interface AvailableCapability {
  installation_id: string;
  listing_id: string;
  kind: string;
  name: string;
  version: string;
  description: string | null;
  trust_level: string;
  config_schema: ConfigSchema | null;
  targets: string[];
}

/** Una fila de `marketplace_deployments`. */
export interface Deployment {
  id: string;
  tenant_id: string;
  installation_id: string;
  project_id: string;
  config: Record<string, unknown>;
  role_map: Record<string, unknown>;
  deployed_version: string;
  status: string;
  created_refs: Record<string, unknown>;
  deployed_by: string | null;
  retired_at: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * Lo que devuelve un despliegue. `warnings` y `oauth_pending` NO son adorno: un
 * despliegue que no encontró agentes del rol destino, o cuyo servidor MCP exige
 * «Conectar», tiene que decirlo. Enseñar sólo el 201 convertiría un
 * no-entregado en un éxito aparente — el modo de fallo que este plan cierra.
 */
export interface DeploymentCreateResponse {
  deployment: Deployment;
  already_deployed: boolean;
  warnings: string[];
  oauth_pending: boolean;
}

/** El cuerpo de `POST /marketplace/installations/{id}/deployments`. */
export interface DeploymentCreateBody {
  project_id: string;
  config: Record<string, unknown>;
  /** Lista de roles: `normalize_role_map` la traduce a `{"*": roles}`. */
  role_map: string[];
}

// ---------------------------------------------------------------------------
// Errores de validación (códigos, no frases)
// ---------------------------------------------------------------------------

export type ConfigErrorCode =
  | "required"
  | "type"
  | "enum"
  | "itemEnum"
  | "minItems"
  | "min"
  | "max"
  | "secretNotVaultPointer"
  | "secretPointerEmpty"
  | "unknown";

export interface ConfigError {
  field: string;
  code: ConfigErrorCode;
  /** Dato auxiliar para el mensaje (el tipo esperado, los valores admitidos…). */
  detail?: string;
}

// ---------------------------------------------------------------------------
// Lectura del esquema
// ---------------------------------------------------------------------------

/**
 * Los campos declarados, **en el orden del manifest**.
 *
 * El orden importa: es el que el autor del listing eligió para el formulario, y
 * reordenarlo alfabéticamente rompería agrupaciones pensadas (lo básico antes
 * que lo avanzado).
 */
export function schemaFields(
  schema: ConfigSchema | null | undefined,
): { name: string; spec: ConfigSchemaField }[] {
  const props = schema?.properties;
  if (!props || typeof props !== "object") return [];
  return Object.entries(props)
    .filter(([, spec]) => spec !== null && typeof spec === "object")
    .map(([name, spec]) => ({ name, spec }));
}

function requiredNames(schema: ConfigSchema | null | undefined): string[] {
  const raw = schema?.required;
  return Array.isArray(raw) ? raw.filter((n): n is string => typeof n === "string") : [];
}

function hasDefault(spec: ConfigSchemaField): boolean {
  // `default: null` CUENTA como declarado: el `config_schema` de Playwright
  // emite `base_url: { default: null }` para decir «opcional, vacío».
  return Object.prototype.hasOwnProperty.call(spec, "default");
}

/** Copia superficial del default, para que dos formularios no compartan el mismo array. */
function cloneDefault(value: unknown): unknown {
  if (Array.isArray(value)) return [...value];
  if (value !== null && typeof value === "object") return { ...(value as object) };
  return value;
}

/**
 * Los valores con los defaults del esquema aplicados a lo que falte.
 *
 * No valida (eso es {@link validateDeploymentConfig}); sólo rellena. Un campo
 * presente —aunque valga `null`— se respeta: quien vacía un campo con default
 * no quiere que se le vuelva a llenar solo.
 */
export function applyDefaults(
  schema: ConfigSchema | null | undefined,
  values: Record<string, unknown> | null | undefined,
): Record<string, unknown> {
  const out: Record<string, unknown> = { ...(values ?? {}) };
  for (const { name, spec } of schemaFields(schema)) {
    if (name in out) continue;
    if (hasDefault(spec)) out[name] = cloneDefault(spec.default);
  }
  return out;
}

// ---------------------------------------------------------------------------
// Validación
// ---------------------------------------------------------------------------

const TYPE_OK: Record<string, (value: unknown) => boolean> = {
  string: (v) => typeof v === "string",
  // `boolean` NO satisface integer/number: aceptarlo convertiría un
  // `headless: true` en `timeout_ms: 1`.
  integer: (v) => typeof v === "number" && Number.isInteger(v),
  number: (v) => typeof v === "number" && Number.isFinite(v),
  boolean: (v) => typeof v === "boolean",
  array: (v) => Array.isArray(v),
  object: (v) => v !== null && typeof v === "object" && !Array.isArray(v),
};

function checkField(name: string, spec: ConfigSchemaField, value: unknown): ConfigError[] {
  const errors: ConfigError[] = [];

  if (spec.secret) {
    // El único contrato de un campo secreto, y el mensaje NUNCA lleva el valor.
    if (typeof value !== "string" || !value.startsWith(VAULT_POINTER_PREFIX)) {
      return [{ field: name, code: "secretNotVaultPointer" }];
    }
    if (value.trim() === VAULT_POINTER_PREFIX) {
      return [{ field: name, code: "secretPointerEmpty" }];
    }
    return [];
  }

  const declared = spec.type;
  if (typeof declared === "string" && declared in TYPE_OK && !TYPE_OK[declared](value)) {
    // Un tipo que no casa hace inútil el resto de comprobaciones del campo.
    return [{ field: name, code: "type", detail: declared }];
  }

  if (Array.isArray(spec.enum) && spec.enum.length > 0 && !spec.enum.includes(value)) {
    errors.push({ field: name, code: "enum", detail: spec.enum.map(String).join(", ") });
  }

  if (Array.isArray(value)) {
    const allowed = spec.items?.enum;
    if (Array.isArray(allowed) && allowed.length > 0) {
      const detail = allowed.map(String).join(", ");
      // Un solo error por campo aunque fallen varias entradas: el formulario
      // pinta el campo, no una lista de sub-errores.
      if (value.some((entry) => !allowed.includes(entry))) {
        errors.push({ field: name, code: "itemEnum", detail });
      }
    }
    if (typeof spec.minItems === "number" && value.length < spec.minItems) {
      errors.push({ field: name, code: "minItems", detail: String(spec.minItems) });
    }
  }

  if (typeof value === "number" && Number.isFinite(value)) {
    if (typeof spec.minimum === "number" && value < spec.minimum) {
      errors.push({ field: name, code: "min", detail: String(spec.minimum) });
    }
    if (typeof spec.maximum === "number" && value > spec.maximum) {
      errors.push({ field: name, code: "max", detail: String(spec.maximum) });
    }
  }

  return errors;
}

/**
 * Los errores de `values` contra `schema`. Lista vacía = válido para el cliente.
 *
 * Un esquema ausente significa «esta capacidad no pide configuración»: mandar
 * valores es entonces un error (un typo del cliente, o un manifest que cambió
 * sin actualizar el despliegue), exactamente como decide el backend.
 */
export function validateDeploymentConfig(
  schema: ConfigSchema | null | undefined,
  values: Record<string, unknown> | null | undefined,
): ConfigError[] {
  const given = values ?? {};
  const fields = schemaFields(schema);
  const known = new Set(fields.map((f) => f.name));

  const errors: ConfigError[] = [];

  for (const name of Object.keys(given).sort()) {
    if (!known.has(name)) errors.push({ field: name, code: "unknown" });
  }
  if (fields.length === 0) return errors;

  const specByName = new Map(fields.map((f) => [f.name, f.spec]));
  for (const name of requiredNames(schema)) {
    const spec = specByName.get(name);
    if (name in given && given[name] !== null && given[name] !== undefined) continue;
    // Un requerido con default no es un hueco: el default lo llena.
    if (spec && hasDefault(spec) && spec.default !== null && spec.default !== undefined) continue;
    errors.push({ field: name, code: "required" });
  }

  for (const { name, spec } of fields) {
    if (!(name in given)) continue;
    const value = given[name];
    // NULL explícito: válido salvo que sea requerido (ya cazado arriba).
    if (value === null || value === undefined) continue;
    errors.push(...checkField(name, spec, value));
  }

  return errors;
}

// ---------------------------------------------------------------------------
// Roles
// ---------------------------------------------------------------------------

/**
 * Los roles que se pre-marcan a partir de los `targets` del manifest (D5).
 *
 * Descarta lo que no esté en el vocabulario del panel y devuelve el orden
 * canónico de `AGENT_ROLES`, para que dos despliegues del mismo listing manden
 * la misma lista y el diff de una auditoría no sea ruido de ordenación.
 */
export function rolesFromTargets(targets: string[] | null | undefined): AgentRole[] {
  const wanted = new Set(targets ?? []);
  return AGENT_ROLES.filter((role) => wanted.has(role));
}

// ---------------------------------------------------------------------------
// Lo instalado en el tenant, cuando NO se puede preguntar por proyecto
// ---------------------------------------------------------------------------

/** Lo mínimo de una instalación (`GET /marketplace/installations`). */
export interface InstallationLite {
  id: string;
  listing_id: string;
  version: string;
  status: string;
}

/** Lo mínimo de un listing del catálogo (`GET /marketplace/listings`). */
export interface ListingLite {
  id: string;
  kind: string;
  name: string;
  version: string;
  description: string | null;
  trust_level: string;
  manifest: Record<string, unknown>;
}

/** El `config_schema` y los `targets` que declara un manifest (ambos opcionales). */
export function capabilityFromManifest(
  manifest: Record<string, unknown> | null | undefined,
): CapabilityShape {
  const rawSchema = manifest?.["config_schema"];
  const rawTargets = manifest?.["targets"];
  return {
    config_schema:
      rawSchema !== null && typeof rawSchema === "object" && !Array.isArray(rawSchema)
        ? (rawSchema as ConfigSchema)
        : null,
    targets: Array.isArray(rawTargets)
      ? rawTargets.filter((t): t is string => typeof t === "string")
      : [],
  };
}

/**
 * Lo instalado y habilitado del tenant, con su esquema y sus targets.
 *
 * Existe porque el paso «Capacidades» del wizard **no puede** llamar a
 * `GET /projects/{id}/marketplace/available`: el proyecto todavía no existe, que
 * es literalmente la decisión D2 («instalar no es el momento del cableado»). Se
 * juntan en cliente las dos listas —instalaciones y catálogo—, que son dos
 * peticiones y no una por instalación.
 *
 * Una instalación cuyo listing ya no es visible (un share revocado por el tenant
 * dueño) **no se esconde**: se ofrece sin esquema y con su id por nombre. Un
 * elemento que desaparece de la UI sin explicación es peor que uno feo.
 */
export function capabilitiesFromInstallations(
  installations: InstallationLite[],
  listings: ListingLite[],
): AvailableCapability[] {
  const byId = new Map(listings.map((listing) => [listing.id, listing]));
  const out: AvailableCapability[] = [];
  for (const installation of installations) {
    if (installation.status !== "enabled") continue;
    const listing = byId.get(installation.listing_id);
    const capability = capabilityFromManifest(listing?.manifest);
    out.push({
      installation_id: installation.id,
      listing_id: installation.listing_id,
      kind: listing?.kind ?? "",
      name: listing?.name ?? installation.listing_id,
      version: installation.version,
      description: listing?.description ?? null,
      trust_level: listing?.trust_level ?? "",
      config_schema: capability.config_schema,
      targets: capability.targets,
    });
  }
  return out;
}

// ---------------------------------------------------------------------------
// El borrador de un despliegue: estado PLANO, sin hooks
// ---------------------------------------------------------------------------

/** Lo mínimo que hace falta de una capacidad para pintar y validar su formulario. */
export interface CapabilityShape {
  config_schema: ConfigSchema | null;
  targets: string[];
}

/** Lo que el operador ha rellenado: valores + roles destino. */
export interface DeploymentDraft {
  values: Record<string, unknown>;
  roles: AgentRole[];
}

/**
 * El borrador inicial de una capacidad: defaults del esquema + `targets` (D5).
 *
 * Es una FUNCIÓN pura y no un hook a conciencia: el paso «Capacidades» del
 * wizard lleva N capacidades marcadas a la vez, y un hook por capacidad sería
 * llamar hooks en un bucle. Con estado plano, el padre guarda un mapa
 * `installation_id → borrador` y las tres puertas comparten la misma lógica.
 */
export function initialDraft(capability: CapabilityShape): DeploymentDraft {
  return {
    values: applyDefaults(capability.config_schema, {}),
    roles: rolesFromTargets(capability.targets),
  };
}

/** Los errores del borrador contra el esquema de su capacidad. */
export function draftErrors(capability: CapabilityShape, draft: DeploymentDraft): ConfigError[] {
  return validateDeploymentConfig(capability.config_schema, draft.values);
}

/**
 * El cuerpo de `POST …/deployments`.
 *
 * `role_map` viaja como LISTA: `normalize_role_map` la traduce a
 * `{"*": roles}`, que es lo correcto para un `mcp_server` cuyas tools no se
 * conocen hasta importarlas. Mandar el mapa ya expandido desde el cliente sería
 * una cuarta oportunidad de divergir.
 */
export function draftBody(projectId: string, draft: DeploymentDraft): DeploymentCreateBody {
  return { project_id: projectId, config: draft.values, role_map: [...draft.roles] };
}

/** El listado de roles que la UI ofrece, en su orden canónico. */
export { AGENT_ROLES, type AgentRole };
