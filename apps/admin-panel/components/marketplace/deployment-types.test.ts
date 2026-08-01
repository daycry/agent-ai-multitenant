// Espejo en cliente del validador del despliegue (ADR 0142, `task_mkt2_06`).
//
// El validador AUTORITATIVO es el del backend
// (`api_server/marketplace/config_schema.py`): sus errores llegan en
// `detail.errors` del 422 y la UI los pinta tal cual. Éste es el pre-validador
// que bloquea el submit ANTES de gastar una ida y vuelta, y por eso sus casos
// clavan la misma semántica —incluidos los dos que más muerden:
//
//   * `bool` NO satisface `integer`/`number` (en JS `true + 0 === 1`, así que un
//     `headless: true` colado en un `timeout_ms` es exactamente el fallo que el
//     backend documenta para Python);
//   * un campo `secret: true` sólo acepta un puntero `vault:`, y **el mensaje
//     no puede ecoar el valor** — un error de validación que imprime el secreto
//     lo copia al log.
//
// Devuelve códigos, no frases: el texto lo pone el diccionario i18n. Así el
// módulo es puro y el test no depende del idioma.

import { describe, expect, it } from "vitest";

import {
  VAULT_POINTER_PREFIX,
  applyDefaults,
  capabilitiesFromInstallations,
  draftBody,
  draftErrors,
  initialDraft,
  rolesFromTargets,
  schemaFields,
  validateDeploymentConfig,
  type ConfigSchema,
} from "./deployment-types";

const SCHEMA: ConfigSchema = {
  properties: {
    base_url: { type: "string", title: "Base URL", default: null },
    timeout_ms: { type: "integer", default: 30000, minimum: 1 },
    headless: { type: "boolean", default: true },
    screenshots: { type: "string", enum: ["off", "on", "only-on-failure"], default: "off" },
    browsers: {
      type: "array",
      items: { enum: ["chromium", "firefox", "webkit"] },
      minItems: 1,
      default: ["chromium"],
    },
    api_token: { type: "string", secret: true },
  },
  required: ["timeout_ms"],
};

describe("schemaFields", () => {
  it("preserva el orden de declaración del manifest", () => {
    expect(schemaFields(SCHEMA).map((f) => f.name)).toEqual([
      "base_url",
      "timeout_ms",
      "headless",
      "screenshots",
      "browsers",
      "api_token",
    ]);
  });

  it("un esquema ausente o sin `properties` no tiene campos", () => {
    expect(schemaFields(null)).toEqual([]);
    expect(schemaFields({})).toEqual([]);
  });
});

describe("applyDefaults", () => {
  it("rellena sólo lo ausente y respeta un campo presente aunque valga null", () => {
    const out = applyDefaults(SCHEMA, { base_url: null, timeout_ms: 5 });
    expect(out.base_url).toBeNull();
    expect(out.timeout_ms).toBe(5);
    expect(out.headless).toBe(true);
    expect(out.browsers).toEqual(["chromium"]);
    // Sin `default` declarado no se inventa nada.
    expect("api_token" in out).toBe(false);
  });

  it("clona los valores por defecto de tipo array (dos despliegues no comparten el mismo array)", () => {
    const a = applyDefaults(SCHEMA, {});
    const b = applyDefaults(SCHEMA, {});
    (a.browsers as string[]).push("firefox");
    expect(b.browsers).toEqual(["chromium"]);
  });
});

describe("validateDeploymentConfig", () => {
  it("acepta los defaults del esquema (el formulario nace válido)", () => {
    expect(validateDeploymentConfig(SCHEMA, applyDefaults(SCHEMA, {}))).toEqual([]);
  });

  it("un requerido CON default utilizable no es un hueco (el default lo llena)", () => {
    // Espeja `_has_default(spec) and spec.get("default") is not None` del
    // backend: `timeout_ms` es requerido pero trae `default: 30000`.
    expect(validateDeploymentConfig(SCHEMA, { timeout_ms: null })).toEqual([]);
  });

  it("señala el requerido cuyo default es null (opcional-por-defecto declarado requerido)", () => {
    // `base_url: { default: null }` es el «opcional, vacío» de Playwright: si el
    // manifest además lo declara requerido, el hueco es real.
    const schema: ConfigSchema = { ...SCHEMA, required: ["base_url"] };
    expect(validateDeploymentConfig(schema, { base_url: null })).toEqual([
      { field: "base_url", code: "required" },
    ]);
    expect(validateDeploymentConfig(schema, {})).toEqual([{ field: "base_url", code: "required" }]);
  });

  it("un booleano NO satisface integer", () => {
    const errors = validateDeploymentConfig(SCHEMA, { timeout_ms: true });
    expect(errors).toEqual([{ field: "timeout_ms", code: "type", detail: "integer" }]);
  });

  it("rechaza el campo desconocido en vez de ignorarlo", () => {
    // Un `base_ur1` mal escrito que se ignora en silencio produce un despliegue
    // que apunta a otro sitio.
    const errors = validateDeploymentConfig(SCHEMA, { timeout_ms: 1, base_ur1: "x" });
    expect(errors).toEqual([{ field: "base_ur1", code: "unknown" }]);
  });

  it("aplica enum, minItems y minimum", () => {
    const errors = validateDeploymentConfig(SCHEMA, {
      timeout_ms: 0,
      screenshots: "sometimes",
      browsers: [],
    });
    expect(errors).toContainEqual({ field: "timeout_ms", code: "min", detail: "1" });
    expect(errors).toContainEqual({
      field: "screenshots",
      code: "enum",
      detail: "off, on, only-on-failure",
    });
    expect(errors).toContainEqual({ field: "browsers", code: "minItems", detail: "1" });
  });

  it("rechaza una entrada de array fuera de `items.enum`", () => {
    const errors = validateDeploymentConfig(SCHEMA, { timeout_ms: 1, browsers: ["lynx"] });
    expect(errors).toContainEqual({
      field: "browsers",
      code: "itemEnum",
      detail: "chromium, firefox, webkit",
    });
  });

  it("un campo secret exige puntero a Vault y el error NO ecoa el valor", () => {
    const secret = "sk-live-01234567890";
    const errors = validateDeploymentConfig(SCHEMA, { timeout_ms: 1, api_token: secret });
    expect(errors).toEqual([{ field: "api_token", code: "secretNotVaultPointer" }]);
    expect(JSON.stringify(errors)).not.toContain(secret);
  });

  it("acepta el puntero a Vault y rechaza el puntero vacío", () => {
    expect(
      validateDeploymentConfig(SCHEMA, { timeout_ms: 1, api_token: `${VAULT_POINTER_PREFIX}kv/x` }),
    ).toEqual([]);
    expect(
      validateDeploymentConfig(SCHEMA, { timeout_ms: 1, api_token: VAULT_POINTER_PREFIX }),
    ).toEqual([{ field: "api_token", code: "secretPointerEmpty" }]);
  });

  it("sin `config_schema`, mandar configuración es un error (y no mandarla, no)", () => {
    expect(validateDeploymentConfig(null, {})).toEqual([]);
    expect(validateDeploymentConfig(null, { base_url: "x" })).toEqual([
      { field: "base_url", code: "unknown" },
    ]);
  });
});

describe("el borrador de un despliegue (estado plano, sin hooks)", () => {
  // Estado PLANO a propósito: el wizard de proyecto lleva N capacidades marcadas
  // a la vez y no puede llamar a un hook por cada una. Con datos puros, el
  // padre guarda un mapa `installation_id → borrador` y las tres puertas
  // comparten exactamente la misma lógica.
  const CAP = { config_schema: SCHEMA, targets: ["backend_dev", "qa"] };

  it("nace con los defaults del esquema y los targets pre-marcados", () => {
    const draft = initialDraft(CAP);
    expect(draft.values.timeout_ms).toBe(30000);
    expect(draft.values.headless).toBe(true);
    expect(draft.roles).toEqual(["backend_dev", "qa"]);
  });

  it("una capacidad sin esquema ni targets nace vacía y válida", () => {
    const draft = initialDraft({ config_schema: null, targets: [] });
    expect(draft.values).toEqual({});
    expect(draft.roles).toEqual([]);
    expect(draftErrors({ config_schema: null, targets: [] }, draft)).toEqual([]);
  });

  it("el cuerpo del POST manda `role_map` como lista (normalize_role_map la acepta)", () => {
    const body = draftBody("proj-1", initialDraft(CAP));
    expect(body).toEqual({
      project_id: "proj-1",
      config: initialDraft(CAP).values,
      role_map: ["backend_dev", "qa"],
    });
  });

  it("los errores del borrador son los del esquema (el submit se bloquea con ellos)", () => {
    const draft = { ...initialDraft(CAP), values: { ...initialDraft(CAP).values, browsers: [] } };
    expect(draftErrors(CAP, draft)).toContainEqual({
      field: "browsers",
      code: "minItems",
      detail: "1",
    });
  });
});

describe("capabilitiesFromInstallations", () => {
  // El wizard de proyecto no puede llamar a `GET /projects/{id}/marketplace/
  // available` porque el proyecto AÚN NO EXISTE — que es exactamente la
  // decisión D2. Así que junta en cliente lo instalado con el catálogo, que son
  // dos peticiones y no N+1.
  const INSTALLATIONS = [
    { id: "i1", listing_id: "l1", version: "1.0.0", status: "enabled" },
    { id: "i2", listing_id: "l2", version: "2.0.0", status: "disabled" },
    { id: "i3", listing_id: "l-missing", version: "3.0.0", status: "enabled" },
  ];
  const LISTINGS = [
    {
      id: "l1",
      kind: "mcp_server",
      name: "Jira MCP",
      version: "1.0.0",
      description: "Issues",
      trust_level: "verified",
      manifest: { config_schema: SCHEMA, targets: ["backend_dev"] },
    },
    {
      id: "l2",
      kind: "tool",
      name: "Deshabilitada",
      version: "2.0.0",
      description: null,
      trust_level: "community",
      manifest: {},
    },
  ];

  it("sólo ofrece lo HABILITADO y trae su esquema y sus targets", () => {
    const out = capabilitiesFromInstallations(INSTALLATIONS, LISTINGS);
    expect(out.map((c) => c.installation_id)).toEqual(["i1", "i3"]);
    expect(out[0].name).toBe("Jira MCP");
    expect(out[0].config_schema).toEqual(SCHEMA);
    expect(out[0].targets).toEqual(["backend_dev"]);
  });

  it("una instalación cuyo listing ya no es visible se ofrece SIN esquema, no se esconde", () => {
    // Un listing compartido por otro tenant y luego revocado deja la
    // instalación sin catálogo visible. Esconderla haría desaparecer de la UI
    // algo que el tenant sí tiene instalado.
    const orphan = capabilitiesFromInstallations(INSTALLATIONS, LISTINGS)[1];
    expect(orphan.installation_id).toBe("i3");
    expect(orphan.config_schema).toBeNull();
    expect(orphan.targets).toEqual([]);
    expect(orphan.name).toContain("l-missing");
  });
});

describe("rolesFromTargets", () => {
  it("pre-marca los targets del manifest que existen en el vocabulario", () => {
    expect(rolesFromTargets(["backend_dev", "qa"])).toEqual(["backend_dev", "qa"]);
  });

  it("descarta un rol inventado y devuelve el orden canónico del vocabulario", () => {
    expect(rolesFromTargets(["qa", "wizard", "backend_dev"])).toEqual(["backend_dev", "qa"]);
  });

  it("sin targets no pre-marca nada (decisión D5: el manifest sugiere, no impone)", () => {
    expect(rolesFromTargets([])).toEqual([]);
    expect(rolesFromTargets(undefined)).toEqual([]);
  });
});
