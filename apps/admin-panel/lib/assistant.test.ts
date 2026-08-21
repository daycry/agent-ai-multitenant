import { describe, expect, it } from "vitest";

import { translate } from "@/lib/i18n";

import {
  ASSISTANT_TOOL_CATALOGUE,
  assistantToolLabel,
  identityToFormValues,
  isAssistantIdentityValid,
  isSupportedLanguage,
  toIdentityUpdate,
  validateAssistantIdentity,
  type AssistantIdentityFormValues,
} from "./assistant";

const valid: AssistantIdentityFormValues = {
  name: "Aria",
  avatarUrl: "",
  tone: "profesional y conciso",
  language: "es",
  systemPrompt: "",
  enabledTools: ["tenant_projects_status"],
};

describe("isSupportedLanguage", () => {
  it("accepts es and en only", () => {
    expect(isSupportedLanguage("es")).toBe(true);
    expect(isSupportedLanguage("en")).toBe(true);
    expect(isSupportedLanguage("fr")).toBe(false);
    expect(isSupportedLanguage("")).toBe(false);
  });
});

describe("validateAssistantIdentity", () => {
  it("localiza los mensajes: el mismo fallo, en el idioma pedido", () => {
    expect(validateAssistantIdentity({ ...valid, name: "" }).name).toBe(
      "El nombre es obligatorio.",
    );
    expect(validateAssistantIdentity({ ...valid, name: "" }, "en").name).toBe(
      "The name is required.",
    );
  });

  it("passes a well-formed identity", () => {
    expect(validateAssistantIdentity(valid)).toEqual({});
    expect(isAssistantIdentityValid(valid)).toBe(true);
  });

  it("requires a non-empty name (trimmed)", () => {
    expect(validateAssistantIdentity({ ...valid, name: "   " }).name).toBeDefined();
  });

  it("rejects an over-long name", () => {
    expect(validateAssistantIdentity({ ...valid, name: "x".repeat(121) }).name).toBeDefined();
  });

  it("requires a non-empty tone", () => {
    expect(validateAssistantIdentity({ ...valid, tone: "" }).tone).toBeDefined();
  });

  it("rejects an over-long tone, avatar and prompt", () => {
    expect(validateAssistantIdentity({ ...valid, tone: "x".repeat(201) }).tone).toBeDefined();
    expect(
      validateAssistantIdentity({ ...valid, avatarUrl: "x".repeat(2049) }).avatarUrl,
    ).toBeDefined();
    expect(
      validateAssistantIdentity({ ...valid, systemPrompt: "x".repeat(8001) }).systemPrompt,
    ).toBeDefined();
  });

  it("rejects an unsupported language", () => {
    expect(validateAssistantIdentity({ ...valid, language: "de" }).language).toBeDefined();
  });
});

describe("toIdentityUpdate", () => {
  it("trims, nulls empties and keeps only catalogue tools", () => {
    const body = toIdentityUpdate({
      name: "  Aria  ",
      avatarUrl: "  ",
      tone: "  amable  ",
      language: "en",
      systemPrompt: "   ",
      enabledTools: ["tenant_projects_status", "made_up_tool"],
    });
    expect(body).toEqual({
      name: "Aria",
      avatar_url: null,
      tone: "amable",
      language: "en",
      system_prompt_override: null,
      enabled_tools: ["tenant_projects_status"],
    });
  });

  it("preserves catalogue order regardless of input order", () => {
    const body = toIdentityUpdate({
      ...valid,
      enabledTools: ["tenant_budget_status", "tenant_projects_status"],
    });
    expect(body.enabled_tools).toEqual(["tenant_projects_status", "tenant_budget_status"]);
  });

  it("falls back to es for an unsupported language", () => {
    expect(toIdentityUpdate({ ...valid, language: "zz" }).language).toBe("es");
  });
});

describe("identityToFormValues", () => {
  it("maps nulls to empty strings and clones tools", () => {
    const values = identityToFormValues({
      name: "Bot",
      avatar_url: null,
      tone: "seco",
      language: "fr",
      system_prompt_override: null,
      enabled_tools: ["tenant_plans_summary"],
    });
    expect(values).toEqual({
      name: "Bot",
      avatarUrl: "",
      tone: "seco",
      language: "es", // unsupported -> es
      systemPrompt: "",
      enabledTools: ["tenant_plans_summary"],
    });
  });
});

describe("assistantToolLabel", () => {
  it("returns the friendly label for a known tool, in the language asked for", () => {
    expect(assistantToolLabel("tenant_projects_status")).toBe("Estado de proyectos");
    expect(assistantToolLabel("tenant_projects_status", "en")).toBe("Project status");
  });
  it("falls back to the raw name for an unknown tool", () => {
    // El nombre crudo NO se traduce a proposito: si el backend manda una tool
    // que este catalogo no conoce, lo util es ver su identificador.
    expect(assistantToolLabel("nope")).toBe("nope");
    expect(assistantToolLabel("nope", "en")).toBe("nope");
  });
  it("covers every catalogue tool with a label and a description in BOTH languages", () => {
    for (const tool of ASSISTANT_TOOL_CATALOGUE) {
      for (const lang of ["es", "en"] as const) {
        expect(translate(lang, "assistant", tool.labelKey).length).toBeGreaterThan(0);
        expect(translate(lang, "assistant", tool.descriptionKey).length).toBeGreaterThan(0);
      }
    }
  });
});
