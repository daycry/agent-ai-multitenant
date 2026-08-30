// @vitest-environment jsdom

/**
 * El chat del proyecto migrado al diccionario (plan prod-16, `task_prod16_03`).
 *
 * Fichero propio y no un bloque en `app/admin/projects/i18n.test.tsx` porque
 * esta pantalla necesita mocks que las otras no —`@/lib/ws` y `usePathname`—, y
 * meterlos en el fichero compartido se los impondría a seis pantallas que no
 * abren un WebSocket.
 *
 * Se rinde el módulo ENTERO: pantalla, selector de modo, feed (incluido el
 * resumen plegado, que sólo se ve al desplegarlo) y composer con su pestaña de
 * vista previa. El caso que motivó migrarlo a la vez y no por trozos está en el
 * selector: los tres modos YA traían `labelEn` en `chat-types.ts` y el render
 * pintaba siempre `labelEs`, así que la traducción existía y no llegaba a
 * pantalla.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "proj-1" }),
  usePathname: () => "/admin/projects/proj-1/chat",
  useRouter: () => ({ push: vi.fn() }),
}));

vi.mock("@/lib/ws", () => ({
  useWebSocket: () => {},
  wsUrl: (p: string) => `ws://test${p}`,
}));

import ProjectChatPage from "@/app/admin/projects/[id]/chat/page";

const STORAGE_KEY = "admin-panel.lang";

const CONVERSATION = {
  id: "conv-1",
  tenant_id: "t-1",
  project_id: "proj-1",
  title: "Arranque",
  current_mode: "planning",
  custom_mode_name: null,
  related_plan_id: null,
  created_at: "2026-08-20T09:00:00Z",
};

/** Un resumen plegado: el feed lo pinta distinto y tiene texto propio. */
const SUMMARY_MESSAGE = {
  id: "msg-1",
  tenant_id: "t-1",
  conversation_id: "conv-1",
  author_kind: "system",
  author_user_id: null,
  author_agent_id: null,
  content: "El equipo acordó empezar por el backend.",
  mode: "planning",
  attachments: [
    { kind: "summary_replaces", message_ids: Array.from({ length: 12 }, (_, i) => `old-${i}`) },
  ],
  related_plan_id: null,
  is_summary: true,
  created_at: "2026-08-20T09:30:00Z",
};

function renderIn(lang: "es" | "en", { messages = [] as unknown[] } = {}) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string }) => {
    if (path === "/projects/proj-1/conversations") return Promise.resolve([CONVERSATION]);
    if (path === "/projects/proj-1/planning-roles") return Promise.resolve({ roles: ["backend"] });
    // El POST se queda EN VUELO para poder mirar el eco optimista (H7), que sólo
    // existe mientras el servidor no ha contestado.
    if (path.includes("/messages") && opts?.method === "POST") return new Promise(() => {});
    if (path.includes("/messages")) return Promise.resolve(messages);
    return Promise.resolve([]);
  });
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <ProjectChatPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("projects/[id]/chat — chat del proyecto", () => {
  it("en castellano rinde cabecera, barra de historial, modos y composer", async () => {
    renderIn("es");

    expect(await screen.findByText("Chat del proyecto")).toBeDefined();
    expect(screen.getByText("Conversación:")).toBeDefined();
    expect((await screen.findByTestId("conversation-new")).textContent).toContain(
      "Nueva conversación",
    );
    expect((await screen.findByTestId("chat-mode-discussion")).textContent).toBe("Discusión");
    expect((await screen.findByTestId("chat-mode-execution")).textContent).toBe("Ejecución");
    expect((await screen.findByTestId("chat-feed-empty")).textContent).toContain(
      "La conversación está vacía",
    );
    expect((await screen.findByTestId("chat-send")).textContent).toContain("Enviar");
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en");

    expect(await screen.findByText("Project chat")).toBeDefined();
    expect(screen.getByText("Conversation:")).toBeDefined();
    expect((await screen.findByTestId("conversation-new")).textContent).toContain(
      "New conversation",
    );
    expect((await screen.findByTestId("chat-clear")).textContent).toContain("Clear chat");
    expect((await screen.findByTestId("chat-feed-empty")).textContent).toContain(
      "The conversation is empty",
    );
    expect((await screen.findByTestId("chat-send")).textContent).toContain("Send");
    expect((await screen.findByTestId("chat-input")).getAttribute("placeholder")).toContain(
      "Use @ to mention an agent",
    );

    expect(screen.queryByText("Conversación:")).toBeNull();
    expect(screen.queryByText("Nueva conversación")).toBeNull();
  });

  it("el selector de modo usa la cara inglesa que ya existía y nadie pintaba", async () => {
    renderIn("en");

    expect((await screen.findByTestId("chat-mode-planning")).textContent).toBe("Planning");
    expect((await screen.findByTestId("chat-mode-discussion")).textContent).toBe("Discussion");
    expect((await screen.findByTestId("chat-mode-execution")).textContent).toBe("Execution");
    // El `title` del botón es la descripción del modo: también viajaba en
    // castellano fijo, y ninguna de las dos guardas lo veía.
    expect(screen.getByTestId("chat-mode-planning").getAttribute("title")).toBe(
      "The team builds a structured plan",
    );

    expect(screen.queryByText("Discusión")).toBeNull();
    expect(screen.queryByText("Ejecución")).toBeNull();
  });

  it("el resumen plegado del feed se traduce, incluido su cuerpo al desplegarlo", async () => {
    renderIn("en", { messages: [SUMMARY_MESSAGE] });

    const toggle = await screen.findByTestId("chat-summary-toggle");
    expect(toggle.textContent).toContain("Summary of 12 earlier messages");
    expect(toggle.textContent).toContain("show summary");

    fireEvent.click(toggle);

    expect((await screen.findByTestId("chat-summary-body")).textContent).toContain(
      "The team reads this summary instead of those messages",
    );
    expect(screen.getByTestId("chat-summary-toggle").textContent).toContain("hide");

    expect(screen.queryByText(/mensajes anteriores/)).toBeNull();
  });

  it("la marca «enviando…» del eco optimista se traduce en los dos idiomas", async () => {
    // H7: el eco se pinta al instante, y dice que TODAVÍA no ha llegado al
    // servidor. Es texto nuevo, así que tiene que tener sus dos caras.
    renderIn("es");
    fireEvent.change(await screen.findByTestId("chat-input"), { target: { value: "hola" } });
    fireEvent.click(screen.getByTestId("chat-send"));
    expect((await screen.findByTestId("chat-message-sending")).textContent).toContain("enviando…");

    cleanup();

    renderIn("en");
    fireEvent.change(await screen.findByTestId("chat-input"), { target: { value: "hi" } });
    fireEvent.click(screen.getByTestId("chat-send"));
    expect((await screen.findByTestId("chat-message-sending")).textContent).toContain("sending…");
    expect(screen.queryByText(/enviando/)).toBeNull();
  });

  it("la pestaña de vista previa del composer también cambia de idioma", async () => {
    renderIn("en");

    expect((await screen.findByTestId("chat-input-tab-edit")).textContent).toBe("Edit");
    fireEvent.click(await screen.findByTestId("chat-input-tab-preview"));

    expect((await screen.findByTestId("chat-input-preview")).textContent).toContain(
      "Nothing to preview.",
    );
    expect(screen.queryByText("Sin contenido para previsualizar.")).toBeNull();
  });
});
