import { describe, expect, it } from "vitest";

import {
  buildModelConfig,
  chatModeLabel,
  composeEffectivePrompt,
  DEFAULT_MODEL_CONFIG,
  draftFromConfig,
  isProviderKind,
  PROVIDER_KINDS,
  PROVIDER_LABEL,
  resolvePromptSource,
  TEMPERATURE_MAX,
  TEMPERATURE_MIN,
  UNAVAILABLE_LABEL,
  validateDraft,
  type ChatModeOption,
  type ModelConfig,
  type ModelConfigDraft,
} from "@/lib/persona/persona";

/**
 * Plan 06.17 task_06_17_11 — sección Persona (SER).
 *
 * Lógica pura (sin React/DOM), testeada aislada igual que `lib/capability/hub.ts`.
 * Cubre lo que la tarea exige: (1) el selector SOLO ofrece los 4 proveedores del
 * catálogo cerrado (ADR 0021); (2) temperatura en rango; (3) prompt efectivo
 * rol+modo; (4) edición es/en sobre la misma fuente que la tarjeta de la lista
 * (colisión lista vs detalle); (5) el modo custom "No disponible aún".
 */

const PLANNING: ChatModeOption = {
  name: "planning",
  label_es: "Planning",
  label_en: "Planning",
  system_prompt: "Estás en el modo PLANNING.",
  available: true,
};
const CUSTOM: ChatModeOption = {
  name: "custom",
  label_es: "Personalizado",
  label_en: "Custom",
  system_prompt: "",
  available: false,
};

describe("persona — catálogo cerrado de proveedores (ADR 0021/0055)", () => {
  it("expone EXACTAMENTE los 4 proveedores del catálogo, en orden", () => {
    expect([...PROVIDER_KINDS]).toEqual(["claude_sdk", "copilot", "azure_foundry", "ollama"]);
  });

  it("cada proveedor tiene etiqueta amigable bilingüe (no se renderiza el slug)", () => {
    for (const p of PROVIDER_KINDS) {
      expect(PROVIDER_LABEL[p].es.length).toBeGreaterThan(0);
      expect(PROVIDER_LABEL[p].en.length).toBeGreaterThan(0);
    }
  });

  it("isProviderKind acepta los 4 y rechaza cualquier quinto", () => {
    expect(isProviderKind("claude_sdk")).toBe(true);
    expect(isProviderKind("ollama")).toBe(true);
    expect(isProviderKind("openai")).toBe(false);
    expect(isProviderKind("litellm")).toBe(false);
    expect(isProviderKind("")).toBe(false);
  });

  it("el default seguro pertenece al catálogo cerrado", () => {
    expect(isProviderKind(DEFAULT_MODEL_CONFIG.provider)).toBe(true);
  });
});

describe("persona — validación del borrador (catálogo + temperatura)", () => {
  const ok: ModelConfigDraft = {
    provider: "claude_sdk",
    model: "claude-opus-4",
    temperature: 0.2,
    reasoning_effort: "off",
  };

  it("un borrador válido no produce errores", () => {
    expect(validateDraft(ok, "es")).toHaveLength(0);
  });

  it("proveedor fuera de catálogo → error de provider", () => {
    const errs = validateDraft({ ...ok, provider: "openai" as never }, "es");
    expect(errs.some((e) => e.field === "provider")).toBe(true);
  });

  it("modelo vacío → error de model", () => {
    const errs = validateDraft({ ...ok, model: "  " }, "en");
    expect(errs.some((e) => e.field === "model")).toBe(true);
  });

  it("temperatura fuera de rango [0,2] → error de temperature", () => {
    expect(
      validateDraft({ ...ok, temperature: -0.1 }, "es").some((e) => e.field === "temperature"),
    ).toBe(true);
    expect(
      validateDraft({ ...ok, temperature: 2.1 }, "es").some((e) => e.field === "temperature"),
    ).toBe(true);
    expect(validateDraft({ ...ok, temperature: TEMPERATURE_MIN }, "es")).toHaveLength(0);
    expect(validateDraft({ ...ok, temperature: TEMPERATURE_MAX }, "es")).toHaveLength(0);
  });

  it("los mensajes de error son bilingües", () => {
    const es = validateDraft({ ...ok, provider: "x" as never }, "es")[0];
    const en = validateDraft({ ...ok, provider: "x" as never }, "en")[0];
    expect(es.message).not.toBe(en.message);
  });
});

describe("persona — draftFromConfig (carga inicial)", () => {
  it("extrae provider/model/temperature de un model_config válido", () => {
    const d = draftFromConfig({ provider: "ollama", model: "llama3", temperature: 0.7 });
    expect(d).toEqual({
      provider: "ollama",
      model: "llama3",
      temperature: 0.7,
      reasoning_effort: "off",
    });
  });

  it("un model_config vacío / legacy cae al default seguro (provider del catálogo)", () => {
    const d = draftFromConfig({});
    expect(isProviderKind(d.provider)).toBe(true);
    expect(d.provider).toBe(DEFAULT_MODEL_CONFIG.provider);
    expect(d.model).toBe("");
  });

  it("un provider fuera de catálogo en datos legacy NO se propaga: cae al default", () => {
    const d = draftFromConfig({ provider: "openai", model: "gpt", temperature: 0.5 });
    expect(d.provider).toBe(DEFAULT_MODEL_CONFIG.provider);
  });

  it("extrae reasoning_effort si está; por defecto 'off' (ADR 0070)", () => {
    expect(
      draftFromConfig({ provider: "claude_sdk", model: "m", reasoning_effort: "high" })
        .reasoning_effort,
    ).toBe("high");
    expect(draftFromConfig({ provider: "ollama", model: "llama3" }).reasoning_effort).toBe("off");
  });
});

describe("persona — colisión lista vs detalle (fuente única del prompt)", () => {
  const cfg: ModelConfig = { system_prompts: { es: "Prompt ES", en: "Prompt EN" } };

  it("resuelve el prompt bilingüe en el idioma pedido (lo MISMO que la tarjeta)", () => {
    expect(resolvePromptSource(cfg, "plano legacy", "es")).toEqual({
      text: "Prompt ES",
      origin: "bilingual",
    });
    expect(resolvePromptSource(cfg, "plano legacy", "en").text).toBe("Prompt EN");
  });

  it("si falta el idioma pedido pero existe el otro, cae al otro (no a un hueco)", () => {
    const onlyEs: ModelConfig = { system_prompts: { es: "Solo ES" } };
    expect(resolvePromptSource(onlyEs, null, "en")).toEqual({
      text: "Solo ES",
      origin: "bilingual",
    });
  });

  it("sin system_prompts cae al campo plano legacy (origin=flat)", () => {
    expect(resolvePromptSource({}, "plano legacy", "es")).toEqual({
      text: "plano legacy",
      origin: "flat",
    });
  });

  it("sin nada devuelve vacío con origin=none (honesto)", () => {
    expect(resolvePromptSource(null, null, "es")).toEqual({ text: "", origin: "none" });
  });
});

describe("persona — prompt efectivo (rol + modo)", () => {
  const cfg: ModelConfig = { system_prompts: { es: "Eres un backend senior." } };

  it("combina prompt del rol + prompt del modo seleccionado", () => {
    const eff = composeEffectivePrompt({ cfg, flatPrompt: null, mode: PLANNING, lang: "es" });
    expect(eff.rolePrompt).toBe("Eres un backend senior.");
    expect(eff.modePrompt).toBe("Estás en el modo PLANNING.");
    expect(eff.combined).toContain("Eres un backend senior.");
    expect(eff.combined).toContain("Estás en el modo PLANNING.");
    // El rol va primero, el modo después.
    expect(eff.combined.indexOf("backend senior")).toBeLessThan(eff.combined.indexOf("PLANNING"));
  });

  it("sin modo seleccionado, el efectivo es solo el rol", () => {
    const eff = composeEffectivePrompt({ cfg, flatPrompt: null, mode: null, lang: "es" });
    expect(eff.modePrompt).toBe("");
    expect(eff.combined).toContain("backend senior");
    expect(eff.combined).not.toContain("PLANNING");
  });

  it("un modo NO disponible (custom) no aporta texto al efectivo (honestidad)", () => {
    const eff = composeEffectivePrompt({ cfg, flatPrompt: null, mode: CUSTOM, lang: "es" });
    expect(eff.modePrompt).toBe("");
  });

  it("usa la MISMA fuente que la tarjeta (model_config.system_prompts) y el fallback plano", () => {
    const eff = composeEffectivePrompt({
      cfg: {},
      flatPrompt: "Prompt plano legacy",
      mode: null,
      lang: "es",
    });
    expect(eff.roleOrigin).toBe("flat");
    expect(eff.combined).toContain("Prompt plano legacy");
  });
});

describe("persona — buildModelConfig (envío preservando bilingüe)", () => {
  it("aplica el borrador y persiste los prompts es/en editados", () => {
    const next = buildModelConfig({
      current: { provider: "claude_sdk", model: "old", temperature: 0.1 },
      draft: { provider: "ollama", model: "llama3", temperature: 0.5, reasoning_effort: "off" },
      prompts: { es: "Nuevo ES", en: "Nuevo EN" },
    });
    expect(next.provider).toBe("ollama");
    expect(next.model).toBe("llama3");
    expect(next.temperature).toBe(0.5);
    expect(next.system_prompts).toEqual({ es: "Nuevo ES", en: "Nuevo EN" });
  });

  it("conserva claves del model_config actual que la UI no edita", () => {
    const next = buildModelConfig({
      current: { provider: "claude_sdk", model: "m", temperature: 0.2, extra_flag: true },
      draft: { provider: "claude_sdk", model: "m", temperature: 0.2, reasoning_effort: "off" },
      prompts: {},
    });
    expect(next.extra_flag).toBe(true);
  });

  it("persiste reasoning_effort cuando no es 'off' (ADR 0070)", () => {
    const next = buildModelConfig({
      current: null,
      draft: { provider: "claude_sdk", model: "m", temperature: 0.2, reasoning_effort: "xhigh" },
      prompts: {},
    });
    expect(next.reasoning_effort).toBe("xhigh");
  });

  it("omite reasoning_effort 'off' y borra el heredado del current (ADR 0070)", () => {
    const next = buildModelConfig({
      current: { provider: "claude_sdk", model: "m", temperature: 0.2, reasoning_effort: "high" },
      draft: { provider: "claude_sdk", model: "m", temperature: 0.2, reasoning_effort: "off" },
      prompts: {},
    });
    expect(next.reasoning_effort).toBeUndefined();
  });

  it("omite system_prompts si ambos idiomas quedan vacíos (no persiste '')", () => {
    const next = buildModelConfig({
      current: { system_prompts: { es: "viejo" } },
      draft: DEFAULT_MODEL_CONFIG,
      prompts: { es: "   ", en: "" },
    });
    expect(next.system_prompts).toBeUndefined();
  });
});

describe("persona — modo custom y etiquetas", () => {
  it("chatModeLabel devuelve la etiqueta del idioma activo", () => {
    expect(chatModeLabel(PLANNING, "es")).toBe("Planning");
    expect(chatModeLabel(CUSTOM, "en")).toBe("Custom");
  });

  it("expone la etiqueta 'No disponible aún' reutilizable y bilingüe", () => {
    expect(UNAVAILABLE_LABEL.es).toBe("No disponible aún");
    expect(UNAVAILABLE_LABEL.en).toBe("Not available yet");
  });
});
