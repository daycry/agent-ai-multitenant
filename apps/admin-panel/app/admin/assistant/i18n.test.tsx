// @vitest-environment jsdom

/**
 * `assistant`, migrado al diccionario (plan prod-16, `task_prod16_04`).
 *
 * Tres pantallas en una: el chat, los ajustes de identidad y las dos tarjetas
 * de modelo (la del tenant y la de plataforma, que sólo ve un System Admin).
 *
 * Lo que este fichero cubre y el guard de atributos no podía ver:
 *
 *   * **Los estados que no son el feliz.** «Asistente no disponible» tiene DOS
 *     redacciones —member y admin-con-el-toggle-apagado— y ninguna se ve en la
 *     pantalla normal.
 *   * **El catálogo de herramientas**, que vivía como literales castellanos en
 *     `lib/assistant.ts`. Ocho etiquetas y ocho descripciones fuera de todo
 *     atributo: cero señales para las dos guardas.
 *   * **Los mensajes de validación del formulario**, que también estaban en el
 *     módulo puro y sólo aparecen tras intentar guardar con el nombre vacío.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

const currentUser = {
  isSystemAdmin: false,
  isTenantAdmin: true,
  isTenantMember: true,
  isLoading: false,
};
vi.mock("@/lib/use-current-user", () => ({ useCurrentUser: () => currentUser }));

import AssistantChatPage from "@/app/admin/assistant/page";
import AssistantSettingsPage from "@/app/admin/assistant/settings/page";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

const IDENTITY = {
  name: "Aria",
  avatar_url: null,
  tone: "profesional",
  language: "es",
  system_prompt_override: null,
  enabled_tools: ["tenant_projects_status"],
};

function wireApi(enabled = true) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/tenant-settings/personal-assistant") return Promise.resolve({ enabled });
    if (path === "/assistant/identity") return Promise.resolve(IDENTITY);
    if (path === "/assistant/conversations") return Promise.resolve([]);
    if (path === "/assistant/model")
      return Promise.resolve({ source: "unset", has_tenant_override: false });
    if (path === "/assistant/model/options")
      return Promise.resolve({ providers: [], reasoning_by_kind: {} });
    if (path === "/assistant/default-model") return Promise.resolve({ provider_id: null });
    if (path === "/assistant/default-model/options")
      return Promise.resolve({ providers: [], reasoning_by_kind: {} });
    return Promise.resolve([]);
  });
}

function renderIn(lang: "es" | "en", node: React.ReactElement) {
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
  currentUser.isSystemAdmin = false;
  currentUser.isTenantAdmin = true;
});

describe("chat del asistente en castellano", () => {
  it("rinde cabecera, selector de hilo, vacío y formulario", async () => {
    wireApi();
    renderIn("es", <AssistantChatPage />);

    expect(await screen.findByText("Asistente personal")).toBeDefined();
    expect(screen.getByText("Modo voz")).toBeDefined();
    expect(screen.getByText("Hilo:")).toBeDefined();
    expect(screen.getByText("Nuevo hilo")).toBeDefined();
    expect(screen.getByText("Empieza una conversación")).toBeDefined();
    expect(screen.getByTestId("assistant-input").getAttribute("placeholder")).toBe(
      "Escribe tu pregunta…",
    );
    expect(screen.getByTestId("assistant-send").textContent).toContain("Enviar");
  });
});

describe("chat del asistente en inglés", () => {
  it("traduce cabecera, selector de hilo, vacío y formulario", async () => {
    wireApi();
    renderIn("en", <AssistantChatPage />);

    expect(await screen.findByText("Personal assistant")).toBeDefined();
    expect(screen.getByText("Voice mode")).toBeDefined();
    expect(screen.getByText("Thread:")).toBeDefined();
    expect(screen.getByText("New thread")).toBeDefined();
    expect(screen.getByText("Start a conversation")).toBeDefined();
    expect(screen.getByTestId("assistant-input").getAttribute("placeholder")).toBe(
      "Type your question…",
    );
    expect(screen.getByTestId("assistant-input").getAttribute("aria-label")).toBe(
      "Message for the assistant",
    );
    expect(screen.getByTestId("assistant-send").textContent).toContain("Send");

    expect(screen.queryByText("Asistente personal")).toBeNull();
    expect(screen.queryByText("Modo voz")).toBeNull();
    expect(screen.queryByText("Nuevo hilo")).toBeNull();
  });

  it("traduce el estado sin acceso de un member", async () => {
    currentUser.isTenantAdmin = false;
    wireApi();
    renderIn("en", <AssistantChatPage />);

    const box = await screen.findByTestId("assistant-no-access");
    expect(box.textContent).toContain("Assistant not available");
    expect(box.textContent).toContain("only for tenant administrators");
    expect(box.textContent).not.toContain("exclusivo");
  });

  it("traduce el estado «apagado» del admin, que es OTRA redacción", async () => {
    wireApi(false);
    renderIn("en", <AssistantChatPage />);

    const box = await screen.findByTestId("assistant-no-access");
    expect(box.textContent).toContain("disabled for your organization");
    expect(screen.getByTestId("assistant-enable-cta").textContent).toContain("Go to settings");
  });
});

describe("ajustes del asistente en castellano", () => {
  it("rinde el toggle, el formulario y el catálogo de herramientas", async () => {
    wireApi();
    renderIn("es", <AssistantSettingsPage />);

    expect(await screen.findByText("Identidad del asistente")).toBeDefined();
    expect(screen.getByText("Asistente habilitado")).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("assistant-identity-form")).toBeTruthy());
    expect(screen.getByText("Tono")).toBeDefined();
    expect(screen.getByText("Herramientas disponibles")).toBeDefined();
    expect(screen.getByText("Estado de proyectos")).toBeDefined();
    expect(screen.getByTestId("assistant-identity-save").textContent).toBe("Guardar");
  });
});

describe("ajustes del asistente en inglés", () => {
  it("traduce cabecera, toggle y campos del formulario", async () => {
    wireApi();
    renderIn("en", <AssistantSettingsPage />);

    expect(await screen.findByText("Assistant identity")).toBeDefined();
    expect(screen.getByText("Go to chat")).toBeDefined();
    expect(screen.getByText("Assistant enabled")).toBeDefined();
    await waitFor(() => expect(screen.getByTestId("assistant-identity-form")).toBeTruthy());
    // El estado del toggle se afirma DESPUÉS del `waitFor`: antes de que
    // resuelva la query el asistente se pinta como apagado, que es correcto y
    // no es lo que esta prueba mira.
    expect(screen.getByText("On")).toBeDefined();
    expect(screen.getByText("Name")).toBeDefined();
    expect(screen.getByText("Tone")).toBeDefined();
    expect(screen.getByText("Language")).toBeDefined();
    expect(screen.getByText("Extra instructions (optional)")).toBeDefined();
    expect(screen.getByTestId("assistant-tone").getAttribute("placeholder")).toBe(
      "professional and concise",
    );
    expect(screen.getByTestId("assistant-identity-save").textContent).toBe("Save");

    expect(screen.queryByText("Identidad del asistente")).toBeNull();
    expect(screen.queryByText("Tono")).toBeNull();
    expect(screen.queryByText("Configuración")).toBeNull();
  });

  it("traduce el catálogo de herramientas, que vivía en lib/assistant.ts", async () => {
    wireApi();
    renderIn("en", <AssistantSettingsPage />);

    await waitFor(() => expect(screen.getByTestId("assistant-tools")).toBeTruthy());
    expect(screen.getByText("Available tools")).toBeDefined();
    expect(screen.getByText("Project status")).toBeDefined();
    expect(screen.getByText("Remember about you")).toBeDefined();
    expect(screen.getByText(/Counts and consolidated status of every project/)).toBeDefined();

    expect(screen.queryByText("Estado de proyectos")).toBeNull();
    expect(screen.queryByText("Recordar sobre ti")).toBeNull();
  });

  it("traduce los mensajes de validación, que también eran literales del módulo puro", async () => {
    wireApi();
    renderIn("en", <AssistantSettingsPage />);

    await waitFor(() => expect(screen.getByTestId("assistant-name")).toBeTruthy());
    fireEvent.change(screen.getByTestId("assistant-name"), { target: { value: "   " } });
    fireEvent.submit(screen.getByTestId("assistant-identity-form"));

    const err = await screen.findByTestId("assistant-name-error");
    expect(err.textContent).toBe("The name is required.");
  });

  it("traduce el bloqueo cuando el asistente está apagado", async () => {
    wireApi(false);
    renderIn("en", <AssistantSettingsPage />);

    const locked = await screen.findByTestId("assistant-identity-locked");
    expect(locked.textContent).toBe("Enable the assistant to configure it.");
    expect(screen.getByText("Off")).toBeDefined();
  });

  it("traduce la tarjeta de modelo del tenant, incluido su vacío de proveedores", async () => {
    wireApi();
    renderIn("en", <AssistantSettingsPage />);

    const card = await screen.findByTestId("assistant-model-card");
    expect(card.textContent).toContain("LLM model");
    await waitFor(() =>
      expect(screen.getByTestId("assistant-model-effective").textContent).toContain(
        "No model configured",
      ),
    );
    expect(card.textContent).toContain("There are no active LLM providers");
  });

  it("traduce la tarjeta de plataforma, que sólo ve un System Admin", async () => {
    currentUser.isSystemAdmin = true;
    wireApi();
    renderIn("en", <AssistantSettingsPage />);

    const card = await screen.findByTestId("assistant-default-model-card");
    expect(card.textContent).toContain("Platform default model");
    await waitFor(() =>
      expect(screen.getByTestId("assistant-default-model-effective").textContent).toBe(
        "No default model configured.",
      ),
    );

    expect(card.textContent).not.toContain("Sin modelo por defecto");
  });
});
