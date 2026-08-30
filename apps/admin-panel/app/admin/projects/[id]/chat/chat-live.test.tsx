// @vitest-environment jsdom
/**
 * Los dos defectos VIVOS del chat, vistos en el recorrido E2E del 2026-08-29
 * (`docs/roadmap/2026-08-29-hallazgos-e2e-hello-world-v2.md`, H7 y H8).
 *
 * Se rinde la PÁGINA ENTERA, no los helpers, porque los dos defectos nacen de la
 * convivencia de dos fuentes que ningún test de unidad pone juntas: la respuesta
 * del `POST /messages` y el evento `message.created` del WebSocket. Por eso este
 * fichero mockea `@/lib/ws` **capturando el handler** en vez de anularlo como
 * hacen `page.test.tsx`, `chat-parts.test.tsx` e `i18n.test.tsx`: con el
 * WebSocket mudo, H7 no se reproduce — y ése es exactamente el motivo de que la
 * red existente estuviera verde con el defecto en producción.
 *
 * H7 — el servidor publica el mensaje del usuario en Redis ANTES de contestar al
 *      POST (`routers/conversations.py`: `_publish_message_event` y luego
 *      `return to_message_response(...)`, con el commit de la dependencia por
 *      medio). El eco del WebSocket gana la carrera, y `postMessage.onSuccess`
 *      añadía la respuesta del POST SIN mirar si ese id ya estaba: dos globos
 *      para un solo mensaje en base de datos.
 *
 * H8 — el aviso «el equipo no tiene agentes configurados» es un mensaje
 *      `system` en modo `planning` (`chat/responder.py::_system_notice`), y
 *      `isReplyInFlight` da por vivo cualquier mensaje de planning reciente sin
 *      adjunto. O sea que el propio aviso de que NADIE va a contestar encendía
 *      el «El equipo está pensando…».
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  configure,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
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

/** El handler que `page.tsx` pasa a `useWebSocket`, para poder empujar frames. */
const ws: { handler: ((data: unknown) => void) | null } = { handler: null };
vi.mock("@/lib/ws", () => ({
  useWebSocket: (_url: string | null, onMessage: (data: unknown) => void) => {
    ws.handler = onMessage;
  },
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
  created_at: "2026-08-29T09:00:00Z",
};

/** Un segundo hilo, para comprobar que un eco no se cuela en el hilo de al lado. */
const OTHER_CONVERSATION = { ...CONVERSATION, id: "conv-2", title: "Otro hilo" };

interface ServerMessage {
  id: string;
  tenant_id: string;
  conversation_id: string;
  author_kind: "user" | "agent" | "system";
  author_user_id: string | null;
  author_agent_id: string | null;
  content: string;
  mode: string;
  attachments: Array<Record<string, unknown>>;
  related_plan_id: string | null;
  is_summary: boolean;
  created_at: string;
}

function serverMessage(over: Partial<ServerMessage> = {}): ServerMessage {
  return {
    id: "m-1",
    tenant_id: "t-1",
    conversation_id: "conv-1",
    author_kind: "user",
    author_user_id: "u-1",
    author_agent_id: null,
    content: "hola",
    mode: "planning",
    attachments: [],
    related_plan_id: null,
    is_summary: false,
    created_at: "2026-08-29T09:05:00Z",
    ...over,
  };
}

/** Un POST /messages en vuelo: el servidor aún no ha contestado. */
interface InFlightPost {
  content: string;
  settle: (persisted: ServerMessage) => void;
  /** Contestar con un error, que es el caso que se tragaba el mensaje. */
  reject: (err: unknown) => void;
}

/** El estado del servidor, por conversación. */
let feeds: Record<string, ServerMessage[]> = {};
let inFlight: InFlightPost[] = [];

/** Atajo al hilo por defecto de la mayoría de los casos. */
let feed: ServerMessage[] = [];

function wireApi({ conversations = [CONVERSATION] as unknown[] } = {}) {
  feed = [];
  feeds = { "conv-1": feed, "conv-2": [] };
  inFlight = [];
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/projects/proj-1/conversations") return Promise.resolve(conversations);
    if (path === "/projects/proj-1/planning-roles") return Promise.resolve({ roles: [] });
    const messages = /^\/conversations\/([^/]+)\/messages/.exec(path);
    if (messages && opts?.method === "POST") {
      const content = String((opts.body as { content: string }).content);
      return new Promise<ServerMessage>((resolve, reject) => {
        inFlight.push({ content, settle: resolve, reject });
      });
    }
    // El GET devuelve una COPIA del estado del servidor: el poll de seguridad
    // (`refetchInterval`) no debe borrar lo que ya se persistió.
    if (messages) return Promise.resolve([...(feeds[messages[1]] ?? [])]);
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

/** El frame que el api-server publica al persistir un mensaje. */
function pushOverWebSocket(persisted: ServerMessage) {
  act(() => {
    ws.handler?.({
      type: "message.created",
      payload: {
        message_id: persisted.id,
        author_kind: persisted.author_kind,
        author_user_id: persisted.author_user_id,
        author_agent_id: persisted.author_agent_id,
        content: persisted.content,
        mode: persisted.mode,
        attachments: persisted.attachments,
        is_summary: persisted.is_summary,
      },
    });
  });
}

/**
 * Contesta al POST que sigue en vuelo, como haría el servidor, y espera a que
 * la mutación haya ASENTADO.
 *
 * El punto de sincronización es que el composer se rehabilita
 * (`disabled={postMessage.isPending}`), no un testid del arreglo: un
 * `queryByTestId` que todavía no existe devuelve null desde el primer intento y
 * dejaría pasar el test antes de que corriese el `onSuccess` que duplicaba.
 */
async function replyToPost(persisted: ServerMessage) {
  const post = inFlight.shift();
  if (!post) throw new Error("no hay ningún POST /messages en vuelo");
  await act(async () => {
    post.settle(persisted);
  });
  await waitFor(() => expect(composer().disabled).toBe(false));
}

function composer(): HTMLTextAreaElement {
  return screen.getByTestId("chat-input") as HTMLTextAreaElement;
}

/**
 * Contesta al POST en vuelo con un ERROR, como haría un 500 o una red caída, y
 * espera a que la mutación haya asentado.
 *
 * Mismo punto de sincronización que `replyToPost` y por el mismo motivo: el
 * composer se rehabilita cuando `postMessage` deja de estar en vuelo. Esperar a
 * un testid del arreglo daría verde antes de correr el `onSettled` que borraba
 * el eco.
 */
async function failPost(err: unknown) {
  const post = inFlight.shift();
  if (!post) throw new Error("no hay ningún POST /messages en vuelo");
  await act(async () => {
    post.reject(err);
  });
  await waitFor(() => expect(composer().disabled).toBe(false));
}

function userBubbles(): HTMLElement[] {
  return screen.queryAllByTestId("chat-message-user");
}

/**
 * Los ids de los globos del usuario, EN ORDEN.
 *
 * Se afirma sobre ids y no sobre el número de globos porque contar deja pasar
 * el defecto: dos filas con el mismo texto se ven idénticas, y `waitFor`
 * comprueba la primera vez de forma SÍNCRONA — sobre un DOM que React Query
 * todavía no ha repintado. Un `waitFor(() => expect(...).toHaveLength(1))` justo
 * después de empujar el frame del WebSocket cuadraba con el DOM viejo (el eco
 * solo) y daba verde con el duplicado intacto; verificado saboteando la
 * reconciliación.
 */
function bubbleIds(): string[] {
  return userBubbles().map((n) => n.getAttribute("data-message-id") ?? "");
}

async function send(text: string) {
  fireEvent.change(composer(), { target: { value: text } });
  fireEvent.click(screen.getByTestId("chat-send"));
  await waitFor(() => expect(inFlight.length).toBeGreaterThan(0));
}

configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  ws.handler = null;
});

describe("Chat del proyecto — H7: el mensaje del usuario se pinta UNA vez", () => {
  it("el eco del WebSocket llega antes que la respuesta del POST y NO se duplica", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeTruthy());

    await send("levanta un hello world");

    // 1) El servidor persiste y publica por WebSocket ANTES de contestar al POST.
    const persisted = serverMessage({ id: "m-server", content: "levanta un hello world" });
    feed.push(persisted);
    pushOverWebSocket(persisted);

    // El persistido SUSTITUYE al eco optimista en vez de sumarse: con el POST
    // todavía en vuelo, en el feed está el mensaje del servidor y NADA más.
    await waitFor(() => expect(bubbleIds()).toEqual(["m-server"]));

    // 2) Y cuando por fin vuelve el POST, sigue habiendo uno: la respuesta trae
    //    el MISMO id que ya entró por el WebSocket, así que no se añade otra vez.
    await replyToPost(persisted);
    expect(bubbleIds()).toEqual(["m-server"]);
    expect(userBubbles()[0].textContent).toContain("levanta un hello world");
    expect(screen.queryByTestId("chat-message-sending")).toBeNull();
  });

  it("el mensaje se ve al pulsar «Enviar», sin esperar al servidor", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeTruthy());

    await send("hola equipo");

    // Ni el POST ha contestado ni ha llegado nada por WebSocket.
    expect(bubbleIds()).toHaveLength(1);
    expect(bubbleIds()[0].startsWith("optimistic:")).toBe(true);
    expect(userBubbles()[0].textContent).toContain("hola equipo");
    // Y se ve que todavía no está confirmado: el eco no miente diciendo que ya
    // está en la conversación del equipo.
    expect(screen.getByTestId("chat-message-sending")).toBeTruthy();
  });

  it("dos mensajes con el MISMO texto se siguen viendo los dos", async () => {
    // No-regresión de la deduplicación: repetir la misma frase es legítimo
    // («no funciona» dos veces seguidas), así que casar eco y persistido por
    // contenido a secas se comería el segundo.
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeTruthy());

    await send("otra vez");
    const first = serverMessage({ id: "m-1", content: "otra vez" });
    feed.push(first);
    await replyToPost(first);
    expect(bubbleIds()).toEqual(["m-1"]);

    await send("otra vez");
    // El segundo se ve YA, aunque el primero con ese mismo texto siga en el feed.
    await waitFor(() => expect(bubbleIds()).toHaveLength(2));
    expect(bubbleIds()[0]).toBe("m-1");
    expect(bubbleIds()[1].startsWith("optimistic:")).toBe(true);

    const second = serverMessage({ id: "m-2", content: "otra vez" });
    feed.push(second);
    await replyToPost(second);
    await waitFor(() => expect(bubbleIds()).toEqual(["m-1", "m-2"]));
  });

  it("el eco de un hilo NO se pinta en el hilo de al lado", async () => {
    // El eco vive en estado de la pantalla, no en la caché por conversación: sin
    // filtrarlo por su hilo, enviar y cambiar de conversación mientras el POST
    // vuela pintaría el mensaje en una conversación en la que no se escribió.
    wireApi({ conversations: [OTHER_CONVERSATION, CONVERSATION] });
    mount();
    // La lista viene ascendente y arranca en la última: conv-1.
    await waitFor(() =>
      expect((screen.getByTestId("conversation-picker") as HTMLSelectElement).value).toBe("conv-1"),
    );

    await send("esto es del primer hilo");
    expect(bubbleIds()).toHaveLength(1);

    fireEvent.change(screen.getByTestId("conversation-picker"), { target: { value: "conv-2" } });
    // Se espera a que el feed del OTRO hilo haya cargado de verdad —su estado
    // vacío—, no a que cambie el desplegable: mientras carga, `MessageFeed`
    // pinta «Cargando mensajes…» y taparía el eco intruso.
    await waitFor(() => expect(screen.getByTestId("chat-feed-empty")).toBeTruthy());
    expect(bubbleIds()).toEqual([]);

    // Y al volver sigue donde se escribió.
    fireEvent.change(screen.getByTestId("conversation-picker"), { target: { value: "conv-1" } });
    await waitFor(() => expect(bubbleIds()).toHaveLength(1));
    expect(screen.getByTestId("chat-message-sending")).toBeTruthy();
  });
});

describe("Chat del proyecto — H8: el aviso y el «pensando…» son excluyentes", () => {
  it("con el aviso de que el equipo no puede responder NO se pinta el spinner", async () => {
    wireApi();
    // El aviso es RECIENTE a propósito: con una marca vieja `isReplyInFlight`
    // devolvería false por la ventana de 180 s y el test pasaría sin arreglar
    // nada (verificar-antes-de-implementar §4).
    feed.push(
      serverMessage({
        id: "m-user",
        author_kind: "user",
        content: "hazme un plan",
        created_at: new Date(Date.now() - 4000).toISOString(),
      }),
      serverMessage({
        id: "m-notice",
        author_kind: "system",
        author_user_id: null,
        content:
          "⚠️ El equipo del proyecto no tiene agentes configurados, así que no puede " +
          "responder en el chat. Asigna un equipo con agentes al proyecto.",
        created_at: new Date().toISOString(),
      }),
    );
    mount();

    // El aviso SÍ se ve: lo que sobra es el spinner, no la explicación.
    await waitFor(() => expect(screen.getByTestId("chat-system-banner")).toBeTruthy());
    expect(screen.getByTestId("chat-system-banner").textContent).toContain(
      "no tiene agentes configurados",
    );
    expect(screen.queryByTestId("chat-team-thinking")).toBeNull();
  });

  it("un turno de verdad en vuelo SÍ enciende el spinner (no-regresión)", async () => {
    wireApi();
    feed.push(
      serverMessage({
        id: "m-user",
        author_kind: "user",
        content: "hazme un plan",
        created_at: new Date().toISOString(),
      }),
    );
    mount();

    await waitFor(() => expect(screen.getByTestId("chat-team-thinking")).toBeTruthy());
  });
});

/**
 * El envío que falla — defecto detectado revisando la tanda de H7/H8.
 *
 * `postMessage` no tenía `onError` y `onSettled` retiraba el eco optimista PASE
 * LO QUE PASE. O sea que un POST fallido borraba de la pantalla el mensaje que
 * el usuario acababa de escribir y no ponía nada en su lugar: ni el texto ni el
 * fallo. Es la misma familia que todo el ADR 0162 —una señal que dice algo
 * distinto de lo que ocurre—, y aquí en su versión más cara: el trabajo del
 * usuario desaparece.
 *
 * Se rinde la pantalla entera y se contesta al POST con un error de verdad: el
 * defecto vive en el cableado de la mutación, no en un helper puro.
 */
describe("Chat del proyecto — un envío que falla no puede tragarse el mensaje", () => {
  it("el texto sigue en pantalla y el fallo se ve", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeTruthy());

    await send("levanta un hello world");
    await failPost(new Error("boom"));

    // 1) El texto NO se pierde: sigue siendo el único globo del usuario.
    expect(bubbleIds()).toHaveLength(1);
    expect(userBubbles()[0].textContent).toContain("levanta un hello world");

    // 2) Y deja de mentir diciendo «enviando…»: el envío ya terminó, y mal.
    expect(screen.queryByTestId("chat-message-sending")).toBeNull();
    expect(screen.getByTestId("chat-message-failed")).toBeTruthy();

    // 3) El fallo se ve como tal, no sólo como una ausencia.
    expect(screen.getByTestId("chat-send-error")).toBeTruthy();
  });

  it("reintentar vuelve a enviar el MISMO texto y lo asienta", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeTruthy());

    await send("levanta un hello world");
    await failPost(new Error("boom"));

    fireEvent.click(screen.getByTestId("chat-message-retry"));
    await waitFor(() => expect(inFlight.length).toBe(1));
    expect(inFlight[0].content).toBe("levanta un hello world");

    const persisted = serverMessage({ id: "m-server", content: "levanta un hello world" });
    feed.push(persisted);
    await replyToPost(persisted);

    // Un solo globo, el persistido: el reintento no deja el fallido de recuerdo.
    await waitFor(() => expect(bubbleIds()).toEqual(["m-server"]));
    expect(screen.queryByTestId("chat-message-failed")).toBeNull();
    expect(screen.queryByTestId("chat-send-error")).toBeNull();
  });

  /**
   * H8 reintroducido por la puerta del arreglo de H7.
   *
   * El eco fallido se queda pintado —y debe quedarse, es el único sitio donde
   * sobrevive el texto del usuario—, pero es un mensaje `author_kind: "user"` al
   * final del feed, y para `isReplyInFlight` eso significa «esperando la primera
   * respuesta». O sea que un POST rechazado dejaba encendido «El equipo está
   * pensando…» encima del cartel de que el envío había fallado: exactamente los
   * dos mensajes contradictorios a la vez que H8 vino a retirar.
   */
  it("y NO deja encendido «el equipo está pensando…»", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeTruthy());

    await send("levanta un hello world");
    // Con el POST en vuelo el indicador SÍ está encendido, y tiene que estarlo:
    // hay un turno empezado. Se afirma para que el arreglo no lo apague de más.
    expect(screen.getByTestId("chat-team-thinking")).toBeTruthy();

    await failPost(new Error("boom"));

    // Nadie va a contestar a un mensaje que no llegó al servidor.
    expect(screen.queryByTestId("chat-team-thinking")).toBeNull();
    // Y el fallo se sigue viendo: se apaga el spinner, no la explicación.
    expect(screen.getByTestId("chat-message-failed")).toBeTruthy();
    expect(screen.getByTestId("chat-send-error")).toBeTruthy();
  });

  it("un envío correcto sigue retirando su eco (no-regresión)", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("chat-input")).toBeTruthy());

    await send("hola equipo");
    const persisted = serverMessage({ id: "m-ok", content: "hola equipo" });
    feed.push(persisted);
    await replyToPost(persisted);

    expect(bubbleIds()).toEqual(["m-ok"]);
    expect(screen.queryByTestId("chat-message-failed")).toBeNull();
  });
});
