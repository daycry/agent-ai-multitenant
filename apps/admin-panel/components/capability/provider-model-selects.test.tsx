// @vitest-environment jsdom
// ADR 0082 / plan-unificacion-provider-id — el síntoma que originó el plan:
// con DOS proveedores Ollama activos (ollama-local y ollama-cloud) el
// desplegable de proveedor los mostraba indistinguibles, porque la UI elegía
// por KIND (`/agents/model-options` devolvía solo la fila más nueva de cada
// kind) y no por FILA. Elegir "ollama" era, en la práctica, no poder elegir.
//
// Nadie RENDERIZABA este componente en ningún test (ni `ProviderModelSelects`
// ni el `PersonaModelFields` que lo envuelve), así que la corrección no tenía
// red debajo. Aquí se clava:
//
//   1. las dos filas ollama aparecen como DOS opciones distinguibles, con el
//      formato `display_name (kind)`, y su value es el provider_id (la fila);
//   2. elegir una fija provider_id + su kind y resetea modelo y razonamiento
//      (cada fila tiene su propio catálogo de modelos);
//   3. los modelos que se ofrecen son los de la fila elegida, no la unión;
//   4. degradación sin provider_id (config legacy guardada "por kind"): el
//      modelo guardado NO se pierde y sigue editable.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { LanguageProvider } from "@/lib/lang-context";
import { PersonaModelFields } from "@/components/capability/persona-section";
import {
  ProviderModelSelects,
  type ProviderModelValue,
} from "@/components/capability/provider-model-selects";

// Dos filas del MISMO kind (`ollama`) — el caso que el ADR 0082 vino a arreglar.
const OLLAMA_LOCAL = {
  id: "prov-ollama-local",
  kind: "ollama",
  display_name: "Ollama local",
  slug: "ollama-local",
  models: ["llama3.1:8b", "qwen2.5-coder:7b"],
  reasoning_options: [],
};
const OLLAMA_CLOUD = {
  id: "prov-ollama-cloud",
  kind: "ollama",
  display_name: "Ollama cloud",
  slug: "ollama-cloud",
  models: ["llama3.1:405b"],
  reasoning_options: [],
};
const CLAUDE = {
  id: "prov-claude",
  kind: "claude_sdk",
  display_name: "Claude (suscripción)",
  slug: "claude-pro",
  models: ["claude-sonnet-4-5"],
  reasoning_options: ["off", "medium", "high"],
};

const PROVIDERS = [OLLAMA_LOCAL, OLLAMA_CLOUD, CLAUDE];

function wireApi(providers: unknown[] = PROVIDERS) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/agents/provider-options") return Promise.resolve({ providers });
    return Promise.resolve({ providers: [] });
  });
}

const EMPTY: ProviderModelValue = {
  provider_id: "",
  provider: "",
  model: "",
  temperature: 0.1,
  reasoning_effort: "off",
};

function mount(value: ProviderModelValue, onChange = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const utils = render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <ProviderModelSelects value={value} onChange={onChange} idPrefix="t" />
      </LanguageProvider>
    </QueryClientProvider>,
  );
  return { ...utils, onChange };
}

/** Opciones REALES del <select> de proveedor, sin el placeholder. */
function providerOptions(): { value: string; text: string }[] {
  const select = screen.getByTestId("t-provider") as HTMLSelectElement;
  return Array.from(select.options)
    .filter((o) => o.value !== "")
    .map((o) => ({ value: o.value, text: o.textContent ?? "" }));
}

// Los `waitFor` de este fichero esperan transiciones de TanStack Query. El
// timeout por defecto de RTL (1s) se queda corto cuando la suite corre entera en
// paralelo y la máquina va cargada: se vio un rojo fantasma así. Se sube aquí
// (por fichero) en vez de tocar la config compartida.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("ProviderModelSelects — dos filas del mismo kind (ADR 0082)", () => {
  it("enseña las DOS filas ollama por separado, con formato display_name (kind)", async () => {
    wireApi();
    mount(EMPTY);
    await waitFor(() => expect(providerOptions()).toHaveLength(3));
    const opts = providerOptions();

    // 1) Son dos entradas distintas, no una sola "ollama".
    const ollama = opts.filter((o) => o.text.includes("(ollama)"));
    expect(ollama).toHaveLength(2);
    // 2) Y se distinguen por su display_name (era el síntoma: eran iguales).
    expect(new Set(ollama.map((o) => o.text)).size).toBe(2);
    expect(ollama.map((o) => o.text)).toEqual(
      expect.arrayContaining(["Ollama local (ollama)", "Ollama cloud (ollama)"]),
    );
    // 3) El value es el provider_id (la FILA), no el kind: elegir por kind era
    //    justo lo que impedía llegar a ollama-cloud.
    expect(ollama.map((o) => o.value).sort()).toEqual(["prov-ollama-cloud", "prov-ollama-local"]);
    expect(opts.map((o) => o.value)).not.toContain("ollama");
  });

  it("al elegir una fila guarda su provider_id y su kind, y resetea modelo/razonamiento", async () => {
    wireApi();
    const { onChange } = mount({
      ...EMPTY,
      provider_id: "prov-claude",
      provider: "claude_sdk",
      model: "claude-sonnet-4-5",
      reasoning_effort: "high",
    });
    await waitFor(() => expect(providerOptions()).toHaveLength(3));
    fireEvent.change(screen.getByTestId("t-provider"), {
      target: { value: "prov-ollama-cloud" },
    });
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({
        provider_id: "prov-ollama-cloud",
        provider: "ollama",
        model: "",
        reasoning_effort: "off",
      }),
    );
  });

  it("ofrece los modelos de la fila elegida, no la unión de las dos ollama", async () => {
    wireApi();
    mount({ ...EMPTY, provider_id: "prov-ollama-cloud", provider: "ollama" });
    // El control de modelo solo es un <select> cuando el catálogo de la fila
    // llegó; antes es el input libre (degradación), así que hay que esperarlo.
    const model = await waitFor(() => {
      const el = screen.getByTestId("t-model") as HTMLSelectElement;
      expect(el.tagName).toBe("SELECT");
      return el;
    });
    const values = Array.from(model.options)
      .map((o) => o.value)
      .filter((v) => v !== "");
    expect(values).toEqual(["llama3.1:405b"]);
    // Los de la fila LOCAL no se cuelan (serían un 422 del backend al guardar).
    expect(values).not.toContain("llama3.1:8b");
  });

  it("deshabilita la temperatura solo para claude_sdk (el SDK no la expone)", async () => {
    wireApi();
    mount({ ...EMPTY, provider_id: "prov-claude", provider: "claude_sdk" });
    await waitFor(() =>
      expect((screen.getByTestId("t-temperature") as HTMLInputElement).disabled).toBe(true),
    );
    expect(screen.getByTestId("t-temperature-na")).toBeTruthy();
    cleanup();
    mount({ ...EMPTY, provider_id: "prov-ollama-local", provider: "ollama" });
    await waitFor(() =>
      expect((screen.getByTestId("t-temperature") as HTMLInputElement).disabled).toBe(false),
    );
  });
});

describe("ProviderModelSelects — degradación sin provider_id", () => {
  it("con una config legacy (solo kind) conserva el modelo guardado y lo deja cambiar", async () => {
    wireApi();
    // Config guardada antes del ADR 0082: kind sí, fila no.
    const { onChange } = mount({
      ...EMPTY,
      provider_id: "",
      provider: "ollama",
      model: "llama3.1:70b",
    });
    await waitFor(() => expect(providerOptions()).toHaveLength(3));

    // El proveedor queda sin seleccionar: elegir fila es una DECISIÓN, no algo
    // que la UI adivine (dos filas ollama; adivinar es volver al bug del ADR).
    expect((screen.getByTestId("t-provider") as HTMLSelectElement).value).toBe("");

    // Y el modelo guardado NO desaparece: se antepone como opción y sigue
    // seleccionado, para que guardar no cambie la config en silencio.
    const model = screen.getByTestId("t-model") as HTMLSelectElement;
    expect(model.value).toBe("llama3.1:70b");
    expect(Array.from(model.options).map((o) => o.value)).toContain("llama3.1:70b");
    // Sin fila elegida no se ofrecen modelos de NINGUNA fila concreta (ofrecer
    // los de una sería elegir por el operador).
    expect(Array.from(model.options).map((o) => o.value)).not.toContain("llama3.1:405b");

    // El control sigue vivo: cambiar de modelo propaga el cambio.
    fireEvent.change(model, { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ model: "" }));
  });

  it("si /agents/provider-options viene vacío, el modelo se puede teclear a mano", async () => {
    wireApi([]);
    const { onChange } = mount({ ...EMPTY, model: "" });
    await waitFor(() => expect(providerOptions()).toHaveLength(0));
    // Sin catálogo NI modelo guardado, el control degrada a input libre: el
    // operador no queda bloqueado porque el endpoint no devuelva nada.
    const model = screen.getByTestId("t-model") as HTMLInputElement;
    expect(model.tagName).toBe("INPUT");
    fireEvent.change(model, { target: { value: "modelo-a-mano" } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ model: "modelo-a-mano" }));
  });
});

describe("PersonaModelFields — el envoltorio que usan agentes/equipos", () => {
  it("renderiza el mismo selector por fila y ancla los errores de validación", async () => {
    wireApi();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <LanguageProvider>
          <PersonaModelFields
            draft={{ ...EMPTY, temperature: 9 }}
            onChange={vi.fn()}
            idPrefix="persona"
          />
        </LanguageProvider>
      </QueryClientProvider>,
    );
    // Las dos ollama también aquí (es el mismo componente por dentro): hay que
    // esperar a que el catálogo llegue — con 1 sola opción es el placeholder.
    const select = await waitFor(() => {
      const el = screen.getByTestId("persona-provider") as HTMLSelectElement;
      expect(el.options.length).toBe(4); // placeholder + 3 filas
      return el;
    });
    const texts = Array.from(select.options).map((o) => o.textContent ?? "");
    expect(texts.filter((t) => t.includes("(ollama)"))).toHaveLength(2);
    // Y la validación pura se ancla en su control (temperatura fuera de rango).
    expect(screen.getByTestId("persona-temperature-error")).toBeTruthy();
  });
});
