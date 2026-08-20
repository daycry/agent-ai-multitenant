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
      // prod-16 `task_prod16_04` — knowledge-bases. Cuatro familias, todas
      // deliberadas: el nombre del módulo ("Knowledge Bases", que el panel ya
      // escribía en inglés en castellano y que `nav.knowledgeBases` tiene por la
      // misma razón), la jerga del dominio que el backend y los logs usan sin
      // traducir ("Grant", "Built-in", "Tenant", "Slug", "KB:"), "Error" y
      // "Color", que se escriben igual, e "irreversible", también.
      "knowledgeBases.title",
      "knowledgeBases.errorTitle",
      "knowledgeBases.builtinBadge",
      "knowledgeBases.grant",
      "knowledgeBases.categoryGroupBuiltin",
      "knowledgeBases.categoryGroupTenant",
      "knowledgeBases.slugLabel",
      "knowledgeBases.colorLabel",
      "knowledgeBases.deleteDescriptionStrong",
      "knowledgeBases.grantKbPrefix",
      "knowledgeBases.docStatusFailed",
      "kbCategories.kbsCrumb",
      "kbCategories.errorTitle",
      "kbCategories.builtinSection",
      "kbCategories.tenantSection",
      "kbCategories.builtinBadge",
      "kbCategories.slugLabel",
      "kbCategories.colorLabel",
      // prod-16 `task_prod16_04` — tools. "Custom + MCP" son los dos términos
      // con que el propio panel nombra el origen de una tool del tenant: MCP es
      // una sigla y "custom" es la palabra que la UI castellana ya usaba.
      "tools.groupCustomHint",
      // prod-16 `task_prod16_04` — teams. Tres familias: "Built-in", que la UI
      // castellana ya escribía en inglés (igual que `nav.*` y `agents.*`);
      // "Forked"/"Linked", que son los NOMBRES de los dos modos de alta de
      // miembro y se muestran tal cual porque así los llama el propio diálogo al
      // explicarlos; e "irreversible", que se escribe igual en los dos idiomas.
      "teams.tabBuiltin",
      "teams.builtinBadge",
      "teams.forkedBadge",
      "teams.linkedBadge",
      "teams.deleteDescStrong",
      // prod-16 `task_prod16_04` — guardrails. "Guardrails" es el nombre del
      // subsistema (igual que `nav.guardrails`) y "Hook" es el término con que
      // el propio backend nombra la columna `hook_point`.
      "guardrails.title",
      "guardrails.colHook",
      // prod-16 `task_prod16_04` — ollama. Nombre del producto y de la sección
      // ("Ollama & Embeddings", "Embeddings"), la abreviatura "Dim", el "no" que
      // se escribe igual, "Pull" —el verbo del CLI de Ollama, no una traducción—
      // y "Compatible (768)", donde ambas palabras coinciden.
      "ollama.title",
      "ollama.embeddingsHeading",
      "ollama.colDim",
      "ollama.colCompatible",
      "ollama.no",
      "ollama.pull",
      // prod-16 `task_prod16_03` — settings. El título del índice repite la
      // etiqueta de la sidebar (`nav.settings`, que está aquí por la misma
      // razón): el usuario acaba de pulsar "Settings" y cambiarle el nombre al
      // entrar haría dudar de si ha llegado a otra pantalla.
      "settingsIndex.title",
      // prod-16 `task_prod16_03` — projects. "embedding" es el nombre de la
      // columna del backend y "Runtime" el término que el propio panel usa en
      // castellano: los dos se muestran tal cual para que coincidan con lo que
      // el operador ve en la BD y en los logs.
      "projectMemories.badgeEmbedding",
      "depCache.colRuntime",
      // prod-16 `task_prod16_04` — memories. "Global" y "Scope" son el valor y
      // el nombre del enum `MemoryScope` del backend: el operador los ve así en
      // la API y en la BD, y el resto del catálogo (`memoryScope.*`) ya los
      // trata igual. "1 similar" coincide porque en castellano «similar» es
      // invariable en singular; el plural (`similarBadgeMany`) sí difiere.
      "memories.scopeGlobal",
      "memories.fieldScope",
      "memories.similarBadgeOne",
      // prod-16 `task_prod16_04` — la videollamada de voz. «Error» se escribe
      // igual en los dos idiomas.
      "voiceCall.statusError",
      // prod-16 `task_prod16_03` — `settings/sso` (OIDC + SAML). Cuatro
      // familias, ninguna traducible: el nombre de un producto (Vault), los
      // términos del estándar SAML que el operador copia literales de la
      // consola de su IdP («Entity ID»), y los tres valores de `NameID` que son
      // el sufijo del URN (`persistent`, `transient`, `unspecified`) — no
      // etiquetas, sino el identificador que viaja en la aserción.
      "ssoOidc.sourceVault",
      "ssoSaml.sourceVault",
      "ssoSaml.spIntroEntityId",
      "ssoSaml.entityIdLabel",
      "ssoSaml.nameIdPersistent",
      "ssoSaml.nameIdTransient",
      "ssoSaml.nameIdUnspecified",
      // prod-16 `task_prod16_03` — webhooks entrantes y comandos del proyecto.
      // Los dos catálogos son nombres propios: los cinco emisores soportados y
      // los cuatro stacks de los presets. El quinto preset («Lectura» →
      // «Read-only») SÍ se traduce, que es justo por lo que el catálogo pasó a
      // guardar claves. «Runtime template» es el término que la UI castellana
      // ya usaba en inglés, igual que en `depCache`.
      "incomingWebhooks.originGithub",
      "incomingWebhooks.originGitlab",
      "incomingWebhooks.originJira",
      "incomingWebhooks.originSentry",
      "incomingWebhooks.originLinear",
      "projectCommands.presetPhp",
      "projectCommands.presetNode",
      "projectCommands.presetDotnet",
      "projectCommands.presetPython",
      "projectCommands.runtimeTemplateLabel",
      // prod-16 `task_prod16_03` — «Knowledge Bases», «Chat» y «Planning» son
      // los nombres que la UI castellana ya escribía en inglés (los dos
      // primeros son además el rótulo de su sección en el menú).
      "projectKbs.breadcrumbCurrent",
      "projectChat.breadcrumbCurrent",
      "projectChat.modePlanning",
      // prod-16 `task_prod16_04` — notificaciones. «Vault» es un nombre de
      // producto, «Tenant» la palabra que la UI castellana ya usaba en inglés
      // (igual que en `knowledgeBases`), y «no» se escribe igual en los dos.
      "notifications.secretSourceVault",
      "notifications.ruleNo",
      "notificationsInbox.scopeTenant",
      // prod-16 `task_prod16_04` — docs. Las cinco coincidencias son nombres de
      // las carpetas canónicas del repo y de los tipos que se derivan de ellas
      // («Runbooks», «Changelog», «ADR»): traducirlos alejaría la faceta del
      // nombre de la carpeta que el usuario ve en el árbol.
      "docFacets.categoryRunbooks",
      "docFacets.categoryChangelog",
      "docFacets.typeAdr",
      "docFacets.typeChangelog",
      "docFacets.typeRunbook",
      // prod-16 `task_prod16_04` — marketplace. «Marketplace», «global»,
      // «Manifest», «Skill (SKILL.md)» y «version (semver)» son el nombre del
      // módulo, el enum del backend y nombres de campo del propio manifest: se
      // escriben igual en los dos idiomas porque son lo que hay que teclear.
      "marketplace.title",
      "marketplace.badgeGlobal",
      "marketplacePrivate.manifestLabel",
      "marketplacePrivate.kindSkill",
      "marketplacePrivate.fieldVersionShort",
      // prod-16 `task_prod16_03` — el hub del proyecto y sus piezas. Tres
      // familias, ninguna traducible: los nombres de sub-sección que la UI
      // castellana YA escribía en inglés («Chat», «Tasks», «Knowledge Bases»,
      // «MCP servers» — los dos últimos por la misma razón que `nav.*`),
      // «irreversible», que se escribe igual en los dos idiomas (como en
      // `knowledgeBases`, `teams` y `agents`), y las siglas del formulario de
      // git y de servicios: «PAT», «HTTPS», «Token», «Alias» y «hostname» son
      // lo que el operador copia de la consola de su proveedor.
      "projectHub.sectionChat",
      "projectHub.sectionTasks",
      "projectHub.sectionKbs",
      "projectHub.sectionMcp",
      "projectHub.deleteDescriptionStrong",
      "projectGit.authPat",
      "projectGit.tokenLabel",
      "projectRuntimeServices.serviceAliasLabel",
      // prod-16 `task_prod16_03` — roles de agente. Ocho de los diez son los
      // nombres que la UI castellana ya escribía en inglés, exactamente igual
      // que `agents.*` y `nav.*`: «Arquitecto» y «Especialista» SÍ se traducen,
      // que es lo que hace que esta lista siga significando algo.
      "agentRole.projectManager",
      "agentRole.backendDev",
      "agentRole.frontendDev",
      "agentRole.qa",
      "agentRole.reviewer",
      "agentRole.devops",
      "agentRole.security",
      "agentRole.technicalWriter",
      // prod-16 `task_prod16_03` — `mcp-servers`. «MCP servers» es el nombre del
      // subsistema (el rótulo de la sidebar y del propio protocolo); «tool» y
      // «roles» son la jerga del dominio que la UI castellana ya usaba en
      // inglés; y «Issue trackers» y «Meta / Agent helpers» son nombres de dos
      // categorías del catálogo del backend — las otras nueve SÍ se traducen.
      "mcpServers.breadcrumbCurrent",
      "mcpServers.testToolOne",
      "mcpServers.testToolMany",
      "mcpServers.rolesCount",
      "mcpServers.categoryIssues",
      "mcpServers.categoryMeta",
      // prod-16 `task_prod16_03` — detalle de plan y desglose de coste.
      // «Pull request» es el nombre del objeto en GitHub/GitLab (el operador lo
      // busca así en su plataforma git), «Gantt» es un apellido, «est.» la misma
      // abreviatura en los dos idiomas, e «ID»/«Total» se escriben igual.
      "planDetail.statusPr",
      "planDetail.statusCostEstimatedSuffix",
      "planDetail.ganttTitle",
      "planDetail.colId",
      "planCost.colId",
      "planCost.total",
      // prod-16 `task_prod16_03` — tareas del proyecto. «Kanban», «Backlog»,
      // «Ready», «Tasks», «Plan» y «Runs» son los rótulos que la UI castellana
      // ya escribía en inglés; los otros seis estados de tarea SÍ se traducen.
      "viewToggle.kanban",
      "taskStatus.backlog",
      "taskStatus.ready",
      "projectTasks.breadcrumbCurrent",
      "projectTasks.fieldPlan",
      "taskDetail.runsHeading",
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
