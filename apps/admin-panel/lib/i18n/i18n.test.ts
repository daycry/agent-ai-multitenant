import { describe, expect, it } from "vitest";

import { dictionary } from "./dictionary";
import { interpolate, pickLang, translate } from "./translate";
import { LANGS, type Lang } from "./types";

/** Pares (namespace, clave) del diccionario, para recorrerlo entero. */
function everyEntry(): { ns: string; key: string; texts: Record<Lang, string> }[] {
  const out: { ns: string; key: string; texts: Record<Lang, string> }[] = [];
  for (const [ns, entries] of Object.entries(dictionary)) {
    for (const [key, texts] of Object.entries(entries as Record<string, Record<Lang, string>>)) {
      out.push({ ns, key, texts });
    }
  }
  return out;
}

describe("types", () => {
  it("LANGS es exactamente ES+EN (principio 12 de CLAUDE.md)", () => {
    expect(LANGS).toEqual(["es", "en"]);
  });
});

describe("dictionary — invariantes", () => {
  it("toda clave tiene texto en los dos idiomas y ninguno vacío", () => {
    const entries = everyEntry();
    // Guarda contra el envejecimiento: si el descubrimiento deja de encontrar
    // claves, este test pasaría vacío (verificar-antes-de-implementar §4).
    expect(entries.length).toBeGreaterThanOrEqual(8);

    const broken = entries.filter(({ texts }) =>
      LANGS.some((lang) => typeof texts[lang] !== "string" || texts[lang].trim() === ""),
    );
    expect(broken.map((b) => `${b.ns}.${b.key}`)).toEqual([]);
  });

  it("los marcadores {x} coinciden entre ES y EN", () => {
    const placeholders = (text: string) => (text.match(/\{[a-zA-Z0-9_]+\}/g) ?? []).sort();
    const mismatched = everyEntry().filter(
      ({ texts }) => placeholders(texts.es).join(",") !== placeholders(texts.en).join(","),
    );
    expect(mismatched.map((m) => `${m.ns}.${m.key}`)).toEqual([]);
  });

  it("ninguna traducción es un copia-pega del castellano sin traducir", () => {
    // Palabras que legítimamente se escriben igual en los dos idiomas: términos
    // que la UI castellana ya usaba en inglés (Dashboard, Runs, Settings…) y
    // nombres de producto. Esta lista sólo debe crecer con casos así; si crece
    // con verdaderas traducciones pendientes, el test deja de servir para nada.
    const identicalOnPurpose = new Set([
      "login.emailLabel",
      "nav.dashboard",
      "nav.runs",
      "nav.knowledgeBases",
      "nav.guardrails",
      "nav.marketplace",
      "nav.settings",
      "nav.ollama",
      "nav.sso",
      "nav.backup",
      "users.colEmail",
      "users.colTenant",
      // Nombres de rol del backend: se muestran tal cual a propósito.
      "users.typeSystemAdmin",
      "users.roleTenantAdmin",
      "users.roleTenantUser",
      "users.roleSystemOperator",
      // ADR 0134 — mismas dos razones que arriba: "Email" se escribe igual, y
      // los nombres de rol del backend se muestran tal cual.
      "acceptInvite.emailLabel",
      "invitations.emailLabel",
      "invitations.colEmail",
      "invitations.roleTenantAdmin",
      "invitations.roleTenantUser",
      "invitations.roleSystemOperator",
      // prod-16 `task_prod16_03` — módulo backup. Vocabulario técnico que no se
      // traduce (nombres de campo de S3/SFTP/rclone, marcas y siglas), más el
      // "No" que se escribe igual en los dos idiomas.
      "backup.roCron",
      "backupDestinations.testOk",
      "backupDestinations.testFail",
      "backupDestinations.typeB2",
      "backupDestinations.typeSftp",
      "backupDestinations.fieldBucket",
      "backupDestinations.fieldEndpointUrl",
      "backupDestinations.fieldHost",
      "backupDestinations.fieldPath",
      "backupRestore.previewBackup",
      "backupRestore.no",
      "backupRestore.tenantIdLabel",
      // prod-16 `task_prod16_03` — tenant-stats. Jerga de la plataforma que la
      // UI castellana ya usaba en inglés (run, token, verdict, timestamp, plan).
      "tenantStats.runs",
      "tenantStats.tokensBreakdown",
      "tenantStats.tokensSuffix",
      "tenantStats.colTimestamp",
      "tenantStats.colPlan",
      "tenantStats.colTokens",
      "tenantStats.colVerdict",
      // prod-16 `task_prod16_04` — llm-providers. Vocabulario que no se traduce:
      // "Slug" y "Endpoint" son los términos que la UI castellana ya usaba en
      // inglés, y los tres "API key (…)" nombran el credencial tal y como lo
      // llama cada proveedor (Anthropic, APIM) — traducirlos alejaría la
      // etiqueta del nombre que el operador ve en la consola del proveedor.
      "llmProviders.colSlug",
      "llmProviders.endpoint",
      "llmProviders.claudeApiKeyOption",
      "llmProviders.claudeApiKeyLabel",
      "llmProviders.azureApiKeyLabel",
      // prod-16 `task_prod16_03` — agents. Tres familias, todas legítimas:
      // jerga que la UI castellana ya escribía en inglés ("Built-in", "System
      // prompt", "Memory scope", "Max concurrent tasks", "read-only"), los
      // sufijos (ES)/(EN) de los prompts bilingües —que nombran el idioma, no
      // se traducen— y "irreversible", que se escribe igual en los dos.
      "agents.scopeBuiltin",
      "agents.systemPrompt",
      "agents.promptEsLabel",
      "agents.promptEnLabel",
      "agents.readOnlyBadge",
      "agents.memoryScope",
      "agents.maxConcurrent",
      "agents.deleteWarningStrong",
      // prod-16 `task_prod16_04` — model-prices. "Input", "Output", "Cache",
      // "Provider" y "Context window" son la jerga con que los proveedores LLM
      // nombran sus propios campos de facturación: traducirlos alejaría la
      // columna del nombre que el operador ve en la factura del proveedor.
      "modelPrices.colInput",
      "modelPrices.colOutput",
      "modelPrices.colCache",
      "modelPrices.fieldProvider",
      "modelPrices.fieldInput",
      "modelPrices.fieldOutput",
      "modelPrices.fieldContextWindow",
    ]);

    const identical = new Set(
      everyEntry()
        .filter(({ texts }) => texts.es === texts.en)
        .map(({ ns, key }) => `${ns}.${key}`),
    );

    expect([...identical].filter((id) => !identicalOnPurpose.has(id))).toEqual([]);

    // La otra dirección: una excepción que ya no aplica (porque la clave se
    // borró o porque alguien SÍ la tradujó) debe salir de la lista. Sin esto la
    // allowlist crece y nunca mengua, y acaba tapando lo que debía vigilar.
    expect([...identicalOnPurpose].filter((id) => !identical.has(id))).toEqual([]);
  });
});

describe("translate", () => {
  it("devuelve el texto del idioma pedido", () => {
    expect(translate("es", "login", "submit")).toBe("Iniciar sesión");
    expect(translate("en", "login", "submit")).toBe("Sign in");
  });

  it("cubre los tres errores del formulario de login en ambos idiomas", () => {
    for (const key of [
      "errorInvalidCredentials",
      "errorRateLimited",
      "errorUnreachable",
    ] as const) {
      expect(translate("es", "login", key)).not.toBe(translate("en", "login", key));
    }
  });

  it("interpola las variables que se le pasan", () => {
    expect(interpolate("Hola {name}, tienes {n} avisos", { name: "Ada", n: 3 })).toBe(
      "Hola Ada, tienes 3 avisos",
    );
  });

  it("deja el marcador intacto si nadie aporta la variable (mejor visible que vacío)", () => {
    expect(interpolate("Hola {name}", {})).toBe("Hola {name}");
    expect(interpolate("Hola {name}")).toBe("Hola {name}");
  });

  it("no reinterpola el valor sustituido (una variable con {otra} dentro no se expande)", () => {
    expect(interpolate("{a}", { a: "{b}", b: "boom" })).toBe("{b}");
  });
});

/**
 * `pickLang` es la OTRA mitad del i18n, y la que el diccionario no puede cubrir:
 * texto bilingüe que llega en DATOS (una nota `note_es`/`note_en` del córtex, el
 * label de un runtime template, un aviso del backend). No hay clave que valga
 * porque el contenido no se conoce al compilar.
 *
 * Antes cada llamante lo resolvía con su propio `lang === "es" ? a : b` — 77
 * repartidos por el panel. Centralizarlo no es cosmética: es el único punto
 * donde arreglar el día que el catálogo de idiomas cambie.
 */
describe("pickLang", () => {
  it("devuelve el valor del idioma pedido", () => {
    expect(pickLang("es", { es: "Hola", en: "Hi" })).toBe("Hola");
    expect(pickLang("en", { es: "Hola", en: "Hi" })).toBe("Hi");
  });

  it("acepta un objeto con campos de más (el aviso del backend trae `code`)", () => {
    // Sin la variable intermedia TS rechazaría el literal por propiedad
    // excedente; el llamante real (`warningText`) le pasa un `CapabilityWarning`
    // ya tipado, que es justo este caso.
    const backendWarning = { code: "x", es: "Hola", en: "Hi" };

    expect(pickLang("en", backendWarning)).toBe("Hi");
  });

  it("cae al otro idioma cuando el pedido viene vacío, en vez de pintar nada", () => {
    // El backend puede traer sólo una de las dos caras (una nota redactada en
    // castellano y sin traducir aún). Un hueco en blanco sería peor que el texto
    // en el otro idioma: el operador no vería NADA y lo leería como "sin datos".
    expect(pickLang("en", { es: "Sólo en castellano", en: "" })).toBe("Sólo en castellano");
    expect(pickLang("es", { es: "   ", en: "Only English" })).toBe("Only English");
  });

  it("devuelve cadena vacía si no hay ninguna de las dos", () => {
    expect(pickLang("es", { es: "", en: "" })).toBe("");
  });
});
