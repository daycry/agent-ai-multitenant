// @vitest-environment jsdom
// mejoras-2026-06, Feature 2 (historial de conversaciones): la barra de historial
// del chat del proyecto. Hasta ahora SOLO estaban probados los helpers puros
// (`lib/conversation-history.test.ts`); la pantalla que los usa no tenía ni un
// test jsdom, así que las tres afirmaciones del plan no tenían red debajo:
//
//   - el desplegable lista las conversaciones DEL PROYECTO (etiqueta + modo) y
//     cambiar de activa cambia realmente el feed que se pide;
//   - «Nueva conversación» está visible SIEMPRE (antes solo aparecía con la
//     lista vacía — ese era el bug: el operador quedaba atrapado en la última);
//   - «Eliminar conversación» pasa por ConfirmDialog y dispara
//     `DELETE /conversations/{id}`, saltando después a la más reciente restante.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

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

function conversation(overrides: Record<string, unknown> = {}) {
  return {
    id: "conv-1",
    tenant_id: "t-1",
    project_id: "proj-1",
    title: null as string | null,
    current_mode: "planning",
    custom_mode_name: null,
    related_plan_id: null,
    created_at: "2026-07-01T09:00:00Z",
    ...overrides,
  };
}

// La lista viene created_at-ASCENDENTE del backend (la más reciente, la última).
const CONVERSATIONS = [
  conversation({
    id: "conv-1",
    title: "Arranque del proyecto",
    created_at: "2026-07-01T09:00:00Z",
  }),
  conversation({
    id: "conv-2",
    title: null,
    current_mode: "discussion",
    created_at: "2026-07-02T09:00:00Z",
  }),
  conversation({
    id: "conv-3",
    title: "Revisión de la fase 2",
    current_mode: "execution",
    created_at: "2026-07-03T09:00:00Z",
  }),
];

function wireApi({ conversations = CONVERSATIONS as unknown[] } = {}) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/projects/proj-1/conversations" && opts?.method === "POST") {
      return Promise.resolve(conversation({ id: "conv-new", created_at: "2026-07-04T09:00:00Z" }));
    }
    if (path === "/projects/proj-1/conversations") return Promise.resolve(conversations);
    if (path === "/projects/proj-1/planning-roles") return Promise.resolve({ roles: ["backend"] });
    if (path.includes("/messages")) return Promise.resolve([]);
    if (opts?.method === "DELETE") return Promise.resolve(undefined);
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

function picker(): HTMLSelectElement {
  return screen.getByTestId("conversation-picker") as HTMLSelectElement;
}

/** Rutas de `GET /conversations/{id}/messages` pedidas hasta ahora. */
function feedCalls(): string[] {
  return apiFetchMock.mock.calls
    .map(([p]) => p as string)
    .filter((p) => typeof p === "string" && p.includes("/messages"));
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

describe("Chat del proyecto — barra de historial de conversaciones", () => {
  it("el desplegable lista las conversaciones del proyecto, con etiqueta y modo", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("conversation-history-bar")).toBeTruthy());
    await waitFor(() => expect(picker().options.length).toBe(3));

    const opts = Array.from(picker().options);
    expect(opts.map((o) => o.value)).toEqual(["conv-1", "conv-2", "conv-3"]);
    // Con título: se usa el título. Sin título: sello con fecha (no todas
    // "Conversación", que era el motivo del helper `conversationLabel`).
    expect(opts[0].textContent).toContain("Arranque del proyecto");
    expect(opts[1].textContent).toContain("Conversación · ");
    // El modo activo viaja en la etiqueta para poder distinguirlas de un vistazo.
    expect(opts[0].textContent).toContain("planning");
    expect(opts[1].textContent).toContain("discussion");
    expect(opts[2].textContent).toContain("execution");
  });

  it("arranca en la más reciente y cambiar de activa recarga SU feed", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(picker().options.length).toBe(3));

    // La lista es ascendente: la activa por defecto es la última (la más nueva).
    //
    // Va en `waitFor` y no en un `expect` a pelo porque la selección la hace un
    // `useEffect` DESPUÉS de que aterrice la lista (`page.tsx`: «Auto-select the
    // most recent conversation as soon as the list lands»). Entre que se pintan
    // las `<option>` y que ese efecto commitea el `setActiveConversationId`, el
    // `<select>` no tiene value y el DOM devuelve la PRIMERA opción — o sea
    // `conv-1`. En esta máquina la ventana no se ve nunca; en el runner de CI,
    // el 2026-08-19, sí: `AssertionError: expected 'conv-1' to be 'conv-3'`.
    // El `waitFor` no relaja la aserción —un componente que no eligiera la más
    // reciente seguiría fallando por timeout—, sólo deja de dar por hecho que
    // React ya vació sus efectos.
    await waitFor(() => expect(picker().value).toBe("conv-3"));
    await waitFor(() => expect(feedCalls().some((p) => p.includes("conv-3"))).toBe(true));
    expect(screen.getByTestId("chat-current-mode").textContent).toBe("execution");

    fireEvent.change(picker(), { target: { value: "conv-1" } });

    await waitFor(() => expect(picker().value).toBe("conv-1"));
    // Cambiar de activa no es cosmético: se pide el feed de la nueva…
    await waitFor(() => expect(feedCalls().some((p) => p.includes("conv-1"))).toBe(true));
    // …y la cabecera refleja el modo de ESA conversación.
    expect(screen.getByTestId("chat-current-mode").textContent).toBe("planning");
  });

  it("«Nueva conversación» está visible con la lista LLENA (era el bug)", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(picker().options.length).toBe(3));
    // Antes el botón solo existía en el estado vacío: con conversaciones, el
    // operador quedaba atrapado en la última.
    expect(screen.getByTestId("conversation-new")).toBeTruthy();

    fireEvent.click(screen.getByTestId("conversation-new"));
    await waitFor(() => {
      const post = apiFetchMock.mock.calls.find(
        ([p, o]) =>
          p === "/projects/proj-1/conversations" && (o as { method?: string })?.method === "POST",
      );
      expect(post).toBeDefined();
    });
    // Y la nueva pasa a ser la activa, sin borrar las anteriores.
    await waitFor(() => expect(picker().value).toBe("conv-new"));
    expect(Array.from(picker().options).map((o) => o.value)).toEqual([
      "conv-1",
      "conv-2",
      "conv-3",
      "conv-new",
    ]);
  });

  it("«Nueva conversación» sigue disponible con UNA sola conversación", async () => {
    // Guarda contra el arreglo a medias: condicionarlo a "hay más de una" sería
    // el mismo bug con otro umbral.
    wireApi({ conversations: [CONVERSATIONS[0]] });
    mount();
    await waitFor(() => expect(picker().options.length).toBe(1));
    expect(screen.getByTestId("conversation-new")).toBeTruthy();
  });

  it("eliminar pasa por el ConfirmDialog y dispara DELETE /conversations/{id}", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("conversation-delete")).toBeTruthy());

    // 1) El diálogo no está abierto hasta que se pide.
    expect(screen.queryByTestId("confirm-dialog")).toBeNull();
    fireEvent.click(screen.getByTestId("conversation-delete"));
    await waitFor(() => expect(screen.getByTestId("confirm-dialog")).toBeTruthy());
    expect(screen.getByTestId("confirm-dialog").textContent).toContain("Eliminar conversación");

    // 2) Abrir el diálogo NO borra nada por sí solo.
    expect(
      apiFetchMock.mock.calls.filter(
        ([, o]) => (o as { method?: string } | undefined)?.method === "DELETE",
      ),
    ).toHaveLength(0);

    // 3) Confirmar sí: DELETE del hilo ACTIVO (el más reciente, conv-3).
    fireEvent.click(screen.getByTestId("confirm-dialog-accept"));
    await waitFor(() => {
      const del = apiFetchMock.mock.calls.find(
        ([p, o]) =>
          (o as { method?: string })?.method === "DELETE" && p === "/conversations/conv-3",
      );
      expect(del).toBeDefined();
    });

    // 4) Y salta a la más reciente restante (conv-2), no al estado vacío.
    await waitFor(() => expect(picker().value).toBe("conv-2"));
    expect(Array.from(picker().options).map((o) => o.value)).toEqual(["conv-1", "conv-2"]);
  });

  it("cancelar el diálogo de borrado no borra nada", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("conversation-delete")).toBeTruthy());
    fireEvent.click(screen.getByTestId("conversation-delete"));
    await waitFor(() => expect(screen.getByTestId("confirm-dialog")).toBeTruthy());
    fireEvent.click(screen.getByTestId("confirm-dialog-cancel"));
    await waitFor(() => expect(screen.queryByTestId("confirm-dialog")).toBeNull());
    expect(
      apiFetchMock.mock.calls.filter(
        ([, o]) => (o as { method?: string } | undefined)?.method === "DELETE",
      ),
    ).toHaveLength(0);
    expect(picker().value).toBe("conv-3");
  });

  it("borrar la última conversación cae al estado vacío con su CTA", async () => {
    wireApi({ conversations: [CONVERSATIONS[0]] });
    mount();
    await waitFor(() => expect(screen.getByTestId("conversation-delete")).toBeTruthy());
    fireEvent.click(screen.getByTestId("conversation-delete"));
    await waitFor(() => expect(screen.getByTestId("confirm-dialog")).toBeTruthy());
    fireEvent.click(screen.getByTestId("confirm-dialog-accept"));
    await waitFor(() => expect(screen.getByTestId("chat-create-conversation")).toBeTruthy());
    expect(screen.queryByTestId("conversation-picker")).toBeNull();
  });
});
