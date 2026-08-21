// @vitest-environment jsdom

/**
 * `components/capability/*` migrado al diccionario (plan prod-16, `task_prod16_04`).
 *
 * Era el mayor bolsón de deuda i18n que quedaba: **25 de los 34 ternarios de
 * idioma** que contaba `check-i18n.mjs` vivían en estos cuatro ficheros. Y en
 * dos de ellos ni siquiera eran ternarios sueltos: había un `const t = (es, en)
 * => …` local, o sea un **diccionario privado por fichero**, que es justo la
 * forma de deuda que este plan retira — el guard sólo veía UNO por fichero, no
 * los veinte textos que escondía detrás. Migrarlos hace visible lo que ya estaba.
 *
 * (La prosa de aquí arriba evita escribir el patrón literal a propósito: el
 * guard no distingue comentarios de código, así que documentar el anti-patrón
 * con su forma exacta lo haría fallar. Anotado como limitación conocida.)
 *
 * Cada bloque afirma en los DOS sentidos: que el idioma pedido sale, y que el
 * otro no se cuela por debajo. Media pantalla migrada es peor que ninguna.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

import { CapabilityHub } from "@/components/capability/capability-hub";
import { ChatModelSection } from "@/components/capability/chat-model-section";
import { PersonaPromptFields, PersonaSection } from "@/components/capability/persona-section";
import { ProviderModelSelects } from "@/components/capability/provider-model-selects";

const STORAGE_KEY = "admin-panel.lang";

const PROVIDER_OPTIONS = {
  providers: [
    {
      id: "prov-1",
      kind: "ollama",
      display_name: "Ollama local",
      models: ["llama3"],
      reasoning_options: ["off", "high"],
    },
  ],
};

const CAPABILITIES = {
  entity_type: "agent",
  entity_id: "a-1",
  saber: { knowledge_bases: [] },
  recordar: { memory_scope: "team_shared", memory: [] },
  ser: {
    model_configured: true,
    provider: "Ollama local",
    model: "llama3",
    temperature: 0.3,
    system_prompt_present: true,
    model_origin: "agent",
  },
  hacer: { effective: [], unrestricted: true, shell_exec_effective: false },
  warnings: [],
};

function routeApi(path: string): unknown {
  if (path === "/agents/provider-options") return PROVIDER_OPTIONS;
  if (path === "/chat-modes") {
    return [
      { name: "planning", label_es: "Planificación", label_en: "Planning", available: true },
      { name: "custom", label_es: "Personalizado", label_en: "Custom", available: false },
    ];
  }
  if (path.endsWith("/capabilities")) return CAPABILITIES;
  throw new Error(`unexpected endpoint in test: ${path}`);
}

function renderIn(lang: "es" | "en", node: React.ReactElement) {
  apiFetchMock.mockImplementation((path: string) => Promise.resolve(routeApi(path)));
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>{node}</LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

const MODEL_CONFIG = {
  provider_id: "prov-1",
  provider: "ollama",
  model: "llama3",
  temperature: 0.4,
};

describe("PersonaSection — la pata SER", () => {
  it("en castellano rinde cabecera, campos del resumen y el selector de modo", async () => {
    renderIn(
      "es",
      <PersonaSection modelConfig={MODEL_CONFIG} systemPrompt={null} role="backend" />,
    );

    expect(await screen.findByText("SER · Persona")).toBeDefined();
    expect(
      screen.getByText(
        "Quién es el agente: proveedor, modelo, temperatura y el prompt efectivo (rol + modo).",
      ),
    ).toBeDefined();
    expect(screen.getByText("Proveedor")).toBeDefined();
    expect(screen.getByText("Modelo")).toBeDefined();
    expect(screen.getByText("Temperatura")).toBeDefined();
    expect(screen.getByLabelText("Combinar con el modo")).toBeDefined();
    expect(screen.getByText("Rol:", { exact: false })).toBeDefined();
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn(
      "en",
      <PersonaSection modelConfig={MODEL_CONFIG} systemPrompt={null} role="backend" />,
    );

    expect(await screen.findByText("BE · Persona")).toBeDefined();
    expect(screen.getByText("Provider")).toBeDefined();
    expect(screen.getByText("Model")).toBeDefined();
    expect(screen.getByText("Temperature")).toBeDefined();
    expect(screen.getByLabelText("Combine with mode")).toBeDefined();

    expect(screen.queryByText("SER · Persona")).toBeNull();
    expect(screen.queryByText("Proveedor")).toBeNull();
    expect(screen.queryByLabelText("Combinar con el modo")).toBeNull();
  });

  it("sin prompt definido, el aviso también se traduce", async () => {
    renderIn("en", <PersonaSection modelConfig={null} systemPrompt={null} role="backend" />);

    const warn = await screen.findByTestId("persona-no-prompt");
    expect(warn.textContent).toBe("No system prompt defined. Edit the persona to add one (es/en).");
    // Y el resumen de modelo cae al "Not configured", no al castellano.
    expect(screen.getByTestId("persona-summary-provider").textContent).toBe("Not configured");
  });

  it("el modo custom no disponible se anuncia en el idioma activo", async () => {
    renderIn(
      "es",
      <PersonaSection modelConfig={MODEL_CONFIG} systemPrompt={null} role="backend" />,
    );

    const note = await screen.findByTestId("persona-custom-unavailable");
    expect(note.textContent).toContain("El modo personalizado está");
  });
});

describe("PersonaPromptFields — edición bilingüe del system prompt", () => {
  it("la ayuda se traduce; el nombre del campo del JSON NO", async () => {
    renderIn("en", <PersonaPromptFields prompts={{ es: "", en: "" }} onChange={() => {}} />);

    expect(
      await screen.findByText(
        "System prompt per language (ES + EN). Single source shown by the card and the effective prompt.",
      ),
    ).toBeDefined();
    // "System prompt (ES)" nombra la ruta del JSON que se guarda: si se
    // tradujera dejaría de poder buscarse. Es idéntico en los dos idiomas.
    expect(screen.getByText("System prompt (ES)", { exact: false })).toBeDefined();
  });
});

describe("ProviderModelSelects — el control compartido", () => {
  it("etiqueta los campos en castellano", async () => {
    renderIn(
      "es",
      <ProviderModelSelects
        value={{
          provider_id: "prov-1",
          provider: "ollama",
          model: "llama3",
          temperature: 0.3,
          reasoning_effort: "off",
        }}
        onChange={() => {}}
        idPrefix="x"
      />,
    );

    expect(await screen.findByLabelText("Proveedor")).toBeDefined();
    expect(screen.getByLabelText("Modelo")).toBeDefined();
    expect(screen.getByLabelText("Temperatura")).toBeDefined();
  });

  it("y en inglés, sin dejar castellano", async () => {
    renderIn(
      "en",
      <ProviderModelSelects
        value={{
          provider_id: "prov-1",
          provider: "ollama",
          model: "llama3",
          temperature: 0.3,
          reasoning_effort: "off",
        }}
        onChange={() => {}}
        idPrefix="x"
      />,
    );

    expect(await screen.findByLabelText("Provider")).toBeDefined();
    expect(screen.getByLabelText("Model")).toBeDefined();
    expect(screen.getByLabelText("Temperature")).toBeDefined();
    expect(screen.queryByLabelText("Proveedor")).toBeNull();
  });
});

describe("ChatModelSection — el modelo del chat", () => {
  it("en castellano: título, descripción, casilla de herencia y botón", async () => {
    renderIn("es", <ChatModelSection value={null} onSave={() => {}} idPrefix="team" />);

    expect(await screen.findByText("Modelo del chat")).toBeDefined();
    expect(screen.getByText("Heredar el modelo de ejecución")).toBeDefined();
    expect(screen.getByTestId("team-chat-model-save").textContent).toBe("Guardar modelo del chat");
  });

  it("en inglés, incluido el botón que se compone con el título", async () => {
    renderIn("en", <ChatModelSection value={null} onSave={() => {}} idPrefix="team" />);

    expect(await screen.findByText("Chat model")).toBeDefined();
    expect(screen.getByText("Inherit the execution model")).toBeDefined();
    // El botón se arma con el título en minúsculas: si el título no se hubiera
    // traducido, aquí saldría "Save modelo del chat" — un híbrido que un
    // `t("Guardar X","Save X")` suelto no habría cazado.
    expect(screen.getByTestId("team-chat-model-save").textContent).toBe("Save chat model");
  });

  it("un título propio del llamante (par bilingüe en props) también elige idioma", async () => {
    renderIn(
      "en",
      <ChatModelSection
        value={null}
        onSave={() => {}}
        idPrefix="proj"
        title={{ es: "Modelo del proyecto", en: "Project model" }}
      />,
    );

    expect(await screen.findByText("Project model")).toBeDefined();
    expect(screen.getByTestId("proj-chat-model-save").textContent).toBe("Save project model");
  });

  it("en solo lectura y sin modelo fijado, dice que hereda — traducido", async () => {
    renderIn("en", <ChatModelSection value={null} onSave={() => {}} idPrefix="ro" isReadOnly />);

    const ro = await screen.findByTestId("ro-chat-model-readonly");
    expect(ro.textContent).toBe("Inherits the execution model.");
  });
});

describe("CapabilityHub — el hub SABER/RECORDAR/SER/HACER", () => {
  it("en castellano rinde la descripción y el detalle de SER", async () => {
    renderIn("es", <CapabilityHub entityType="agent" entityId="a-1" />);

    expect(
      await screen.findByText(
        "Las cuatro vías de capacidad y su estado real. Asigna desde cada sección.",
      ),
    ).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("capability-ser-detail")).toBeDefined());
    expect(screen.getByTestId("capability-ser-detail").textContent).toContain("Origen del modelo");
  });

  it("en inglés traduce descripción, detalle de SER y el aviso de HACER", async () => {
    renderIn("en", <CapabilityHub entityType="agent" entityId="a-1" />);

    expect(
      await screen.findByText(
        "The four capability paths and their real state. Assign from each section.",
      ),
    ).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("capability-ser-detail")).toBeDefined());
    const ser = screen.getByTestId("capability-ser-detail").textContent ?? "";
    expect(ser).toContain("Model origin");
    expect(ser).not.toContain("Origen del modelo");

    expect(screen.getByTestId("capability-hacer-unrestricted").textContent).toBe(
      "This level does not restrict tools per agent; the effective set is set by each agent.",
    );
  });
});
