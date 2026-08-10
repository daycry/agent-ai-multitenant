// @vitest-environment jsdom
/**
 * Red de caracterización de las PIEZAS del chat, escrita para poder trocear
 * `page.tsx` (plan prod-16, `task_prod16_08`) sin cruzar los dedos.
 *
 * `page.test.tsx` existía ya, pero cubre **sólo la barra de historial**. Antes de
 * mover una línea se comprobó qué protegía de verdad: se saboteó el salto a la
 * conversación más reciente tras borrar (`nextActiveAfterDelete(...)` → `null`)
 * y **los 7 tests siguieron verdes**. O sea que el selector de modo, el feed de
 * mensajes, el resumen plegable, el botón «Generar Plan» y el composer con
 * @-menciones —justo lo que el troceo saca del fichero— iban a salir del
 * monolito sin nada debajo.
 *
 * Estos casos rinden la PÁGINA ENTERA (no las piezas por separado) a propósito:
 * lo que hay que fijar no es que cada componente funcione aislado, sino que
 * después del corte siga **montado** en su sitio. Un `<ChatComposer>` que se
 * queda sin importar no rompe ningún test de unidad; rompe la pantalla.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "proj-1" }),
  usePathname: () => "/admin/projects/proj-1/chat",
  useRouter: () => ({ push: pushMock }),
}));

vi.mock("@/lib/ws", () => ({
  useWebSocket: () => {},
  wsUrl: (p: string) => `ws://test${p}`,
}));

import ProjectChatPage from "@/app/admin/projects/[id]/chat/page";

const CONVERSATION = {
  id: "conv-1",
  tenant_id: "t-1",
  project_id: "proj-1",
  title: "Arranque",
  current_mode: "planning",
  custom_mode_name: null,
  related_plan_id: null,
  created_at: "2026-07-01T09:00:00Z",
};

function message(overrides: Record<string, unknown> = {}) {
  return {
    id: "m-1",
    tenant_id: "t-1",
    conversation_id: "conv-1",
    author_kind: "user",
    author_user_id: "u-1",
    author_agent_id: null,
    content: "hola",
    mode: "planning",
    attachments: [] as Array<Record<string, unknown>>,
    related_plan_id: null,
    is_summary: false,
    // Muy antiguo a propósito: `isReplyInFlight` no debe creer que hay un turno
    // en vuelo y ensuciar la pantalla con el «El equipo está pensando…».
    created_at: "2026-07-01T09:00:00Z",
    ...overrides,
  };
}

function wireApi({ messages = [] as unknown[], roles = ["backend", "frontend"] } = {}) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/projects/proj-1/conversations") return Promise.resolve([CONVERSATION]);
    if (path === "/projects/proj-1/planning-roles") return Promise.resolve({ roles });
    if (path === "/projects/proj-1/plans" && opts?.method === "POST") {
      return Promise.resolve({ id: "plan-9" });
    }
    if (path.includes("/messages") && opts?.method === "POST") {
      return Promise.resolve(
        message({ id: "m-new", content: String((opts.body as { content: string }).content) }),
      );
    }
    if (path.includes("/messages")) return Promise.resolve(messages);
    if (opts?.method === "PUT")
      return Promise.resolve({ ...CONVERSATION, current_mode: "discussion" });
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ProjectChatPage />
    </QueryClientProvider>,
  );
}

configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  pushMock.mockReset();
});

describe("Chat del proyecto — selector de modo", () => {
  it("pinta los tres modos incorporados y marca el activo", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-mode-selector")).toBeTruthy());

    expect(screen.getByTestId("chat-mode-planning").getAttribute("data-active")).toBe("true");
    expect(screen.getByTestId("chat-mode-discussion").getAttribute("data-active")).toBe("false");
    expect(screen.getByTestId("chat-mode-execution")).toBeTruthy();
  });

  it("pulsar otro modo hace PUT /conversations/{id} con current_mode", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-mode-discussion")).toBeTruthy());

    fireEvent.click(screen.getByTestId("chat-mode-discussion"));

    await waitFor(() =>
      expect(
        apiFetchMock.mock.calls.some(
          ([p, o]) =>
            p === "/conversations/conv-1" &&
            (o as { method?: string; body?: { current_mode?: string } })?.method === "PUT" &&
            (o as { body: { current_mode: string } }).body.current_mode === "discussion",
        ),
      ).toBe(true),
    );
  });

  it("pulsar el modo YA activo no dispara ninguna llamada", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-mode-planning")).toBeTruthy());

    fireEvent.click(screen.getByTestId("chat-mode-planning"));

    expect(
      apiFetchMock.mock.calls.some(([, o]) => (o as { method?: string })?.method === "PUT"),
    ).toBe(false);
  });
});

describe("Chat del proyecto — feed de mensajes", () => {
  it("sin mensajes muestra el vacío, no una lista fantasma", async () => {
    wireApi({ messages: [] });
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-feed-empty")).toBeTruthy());
    expect(screen.queryByTestId("chat-feed")).toBeNull();
  });

  it("distingue turno de usuario, de agente y banner de sistema", async () => {
    wireApi({
      messages: [
        message({ id: "m-u", author_kind: "user", content: "quiero un plan" }),
        message({ id: "m-a", author_kind: "agent", content: "vale, empiezo" }),
        message({ id: "m-s", author_kind: "system", content: "modo cambiado" }),
      ],
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-feed")).toBeTruthy());

    expect(screen.getByTestId("chat-message-user").textContent).toContain("quiero un plan");
    expect(screen.getByTestId("chat-message-agent").textContent).toContain("vale, empiezo");
    expect(screen.getByTestId("chat-system-banner").textContent).toContain("modo cambiado");
  });

  it("un resumen plegado se pinta como resumen y se despliega al pulsarlo", async () => {
    wireApi({
      messages: [
        message({
          id: "m-sum",
          author_kind: "system",
          is_summary: true,
          content: "Resumen de lo hablado",
          // Forma REAL del adjunto (`lib/chat-feed.ts::summaryFoldedCount`): el
          // plegado se cuenta por los ids que el resumen sustituye, no por un
          // contador aparte. Escribir el fixture "a ojo" daba un resumen que se
          // pintaba como banner de sistema — y el test lo cazó.
          attachments: [
            {
              kind: "summary_replaces",
              message_ids: Array.from({ length: 12 }, (_, i) => `old-${i}`),
            },
          ],
        }),
      ],
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-message-summary")).toBeTruthy());

    // Plegado por defecto: el cuerpo no está en el DOM.
    expect(screen.queryByTestId("chat-summary-body")).toBeNull();
    expect(screen.getByTestId("chat-summary-toggle").textContent).toContain("12");

    fireEvent.click(screen.getByTestId("chat-summary-toggle"));
    expect(screen.getByTestId("chat-summary-body").textContent).toContain("Resumen de lo hablado");
  });
});

describe("Chat del proyecto — botón «Generar Plan»", () => {
  it("no aparece mientras el equipo no lo pida", async () => {
    wireApi({ messages: [message({ author_kind: "agent", content: "sigo pensando" })] });
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-feed")).toBeTruthy());
    expect(screen.queryByTestId("generate-plan-button")).toBeNull();
  });

  it("aparece cuando el ÚLTIMO mensaje de agente trae la directiva finish_planning", async () => {
    wireApi({
      messages: [
        message({
          id: "m-fin",
          author_kind: "agent",
          content: "plan listo",
          attachments: [{ kind: "planning_directive", intent: "finish_planning" }],
        }),
      ],
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("generate-plan-button")).toBeTruthy());
  });

  it("NO aparece si la directiva la trae un mensaje de agente ANTERIOR", async () => {
    wireApi({
      messages: [
        message({
          id: "m-old",
          author_kind: "agent",
          attachments: [{ kind: "planning_directive", intent: "finish_planning" }],
        }),
        message({ id: "m-new", author_kind: "agent", content: "he cambiado de idea" }),
      ],
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-feed")).toBeTruthy());
    expect(screen.queryByTestId("generate-plan-button")).toBeNull();
  });

  it("al pulsarlo crea el plan y navega a su ficha", async () => {
    wireApi({
      messages: [
        message({
          id: "m-fin",
          author_kind: "agent",
          attachments: [{ kind: "planning_directive", intent: "finish_planning" }],
        }),
      ],
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("generate-plan-button")).toBeTruthy());

    fireEvent.click(screen.getByTestId("generate-plan-button"));

    await waitFor(() =>
      expect(
        apiFetchMock.mock.calls.some(
          ([p, o]) =>
            p === "/projects/proj-1/plans" &&
            (o as { body: { conversation_id: string } })?.body?.conversation_id === "conv-1",
        ),
      ).toBe(true),
    );
    await waitFor(() =>
      expect(pushMock).toHaveBeenCalledWith("/admin/projects/proj-1/plans/plan-9"),
    );
  });
});

describe("Chat del proyecto — composer", () => {
  it("envía el mensaje escrito y vacía el campo", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeTruthy());

    const input = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "  hola equipo  " } });
    fireEvent.click(screen.getByTestId("chat-send"));

    await waitFor(() =>
      expect(
        apiFetchMock.mock.calls.some(
          ([p, o]) =>
            p === "/conversations/conv-1/messages" &&
            (o as { body: { content: string } })?.body?.content === "hola equipo",
        ),
      ).toBe(true),
    );
    await waitFor(() => expect(input.value).toBe(""));
  });

  it("no envía nada si sólo hay espacios", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeTruthy());

    fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "   " } });
    expect((screen.getByTestId("chat-send") as HTMLButtonElement).disabled).toBe(true);
  });

  it("@ sugiere SOLO los roles del equipo del proyecto y al elegir uno lo inserta", async () => {
    wireApi({ roles: ["backend", "frontend"] });
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeTruthy());

    const input = screen.getByTestId("chat-input") as HTMLTextAreaElement;
    fireEvent.change(input, { target: { value: "pregunta a @b" } });

    await waitFor(() => expect(screen.getByTestId("mention-suggestions")).toBeTruthy());
    expect(screen.getByTestId("mention-suggestion-backend")).toBeTruthy();
    expect(screen.queryByTestId("mention-suggestion-frontend")).toBeNull();

    fireEvent.click(screen.getByTestId("mention-suggestion-backend"));
    await waitFor(() => expect(input.value).toBe("pregunta a @backend "));
  });

  it("la pestaña de vista previa cambia el textarea por el markdown renderizado", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeTruthy());

    fireEvent.change(screen.getByTestId("chat-input"), { target: { value: "# titular" } });
    fireEvent.click(screen.getByTestId("chat-input-tab-preview"));

    expect(screen.queryByTestId("chat-input")).toBeNull();
    expect(screen.getByTestId("chat-input-preview").textContent).toContain("titular");
  });
});
