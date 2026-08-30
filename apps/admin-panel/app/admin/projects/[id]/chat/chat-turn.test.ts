import { describe, expect, it } from "vitest";

import {
  chatPollInterval,
  isTeamWorking,
  turnFeed,
  type TurnMessage,
} from "@/app/admin/projects/[id]/chat/chat-turn";
import { CHAT_POLL_MS, isReplyInFlight } from "@/lib/chat-feed";

const NOW = Date.parse("2026-08-29T12:00:00.000Z");

function msg(over: Partial<TurnMessage> = {}): TurnMessage {
  return {
    author_kind: "agent",
    mode: "planning",
    created_at: new Date(NOW - 5_000).toISOString(),
    attachments: [],
    is_summary: false,
    ...over,
  };
}

/**
 * El aviso de H6/H8 tal y como llega al feed: lo escribe
 * `chat/responder.py::_system_notice` con el `mode` del turno y sin adjuntos.
 * El TEXTO no entra en la decisión a propósito —sería atarse a una cadena en
 * castellano del backend—; lo que la decide es que sea `system` y no un resumen.
 */
const NO_AGENTS_NOTICE = msg({ author_kind: "system" });

describe("isTeamWorking", () => {
  it("un aviso del sistema CIERRA el turno, por reciente que sea (H8)", () => {
    const feed = [msg({ author_kind: "user" }), NO_AGENTS_NOTICE];

    // La delta con el helper de abajo es el defecto entero: `isReplyInFlight`
    // da por vivo cualquier mensaje de planning reciente sin adjunto, así que el
    // aviso de que NADIE va a contestar encendía el «El equipo está pensando…».
    expect(isReplyInFlight(feed, NOW)).toBe(true);
    expect(isTeamWorking(feed, NOW)).toBe(false);
  });

  it("el aviso cierra el turno en cualquier modo, no sólo en planning", () => {
    // `_system_notice` se llama en siete sitios del responder y en los siete el
    // turno se acaba ahí (sin proveedor LLM, timeout, error, respuesta vacía…),
    // y el «Modo cambiado» del PUT ni siquiera pertenece a un turno.
    for (const mode of ["planning", "discussion", "execution"]) {
      expect(isTeamWorking([msg({ author_kind: "system", mode })], NOW)).toBe(false);
    }
  });

  it("un RESUMEN plegado no cierra el turno (se emite a mitad de la ronda)", () => {
    // `compress_conversation_best_effort` corre ANTES de que el equipo conteste,
    // y su resumen es también `system`. Tratarlo como terminal apagaría el
    // indicador justo cuando el turno acaba de empezar.
    const feed = [msg({ author_kind: "user" }), msg({ author_kind: "system", is_summary: true })];
    expect(isTeamWorking(feed, NOW)).toBe(true);
  });

  it("con el usuario esperando la primera respuesta, el equipo SÍ trabaja", () => {
    expect(isTeamWorking([msg({ author_kind: "user" })], NOW)).toBe(true);
  });

  it("a mitad de una ronda de planning sigue trabajando", () => {
    expect(isTeamWorking([msg({ author_kind: "agent" })], NOW)).toBe(true);
  });

  it("sin mensajes no hay turno", () => {
    expect(isTeamWorking([], NOW)).toBe(false);
    expect(isTeamWorking(undefined, NOW)).toBe(false);
  });
});

describe("chatPollInterval", () => {
  it("el poll de seguridad sigue al mismo criterio que el indicador", () => {
    expect(chatPollInterval([msg({ author_kind: "user" })], NOW)).toBe(CHAT_POLL_MS);
    // Un turno cerrado por un aviso no se sondea durante tres minutos a la
    // espera de algo que nadie va a escribir.
    expect(chatPollInterval([msg({ author_kind: "user" }), NO_AGENTS_NOTICE], NOW)).toBe(false);
  });
});

/**
 * El envío que falla y el indicador — la contradicción de H8 reintroducida por
 * la puerta del arreglo de H7.
 *
 * El eco de un POST rechazado se queda pintado a propósito (es el único sitio
 * donde sobrevive el texto del usuario), pero es un mensaje del USUARIO al final
 * del feed, y para `isReplyInFlight` eso significa «esperando la primera
 * respuesta». Encima del cartel «no se pudo enviar» seguía girando «El equipo
 * está pensando…».
 */
describe("turnFeed", () => {
  /** Un mensaje del feed con id, que es lo que distingue a un eco. */
  function withId(id: string, over: Partial<TurnMessage> = {}) {
    return { id, ...msg(over) };
  }

  it("un envío que FALLÓ no empieza ningún turno", () => {
    const feed = [withId("optimistic:1", { author_kind: "user" })];

    // La delta con la línea de abajo es el defecto entero: sin filtrar el eco
    // fallido, el mensaje que NUNCA llegó al servidor enciende el indicador de
    // que alguien está trabajando en él.
    expect(isTeamWorking(feed, NOW)).toBe(true);
    expect(isTeamWorking(turnFeed(feed, new Set(["optimistic:1"])), NOW)).toBe(false);
  });

  it("un envío TODAVÍA en vuelo sí lo empieza", () => {
    // El POST sigue camino y el equipo va a contestar: apagar el indicador aquí
    // sería el error contrario, y el más caro de los dos (parece que no pasa
    // nada mientras pasa).
    const feed = [withId("optimistic:1", { author_kind: "user" })];
    expect(isTeamWorking(turnFeed(feed, new Set()), NOW)).toBe(true);
  });

  it("no toca lo persistido: sólo retira los ids que fallaron", () => {
    const feed = [
      withId("m-1", { author_kind: "user" }),
      withId("optimistic:1", { author_kind: "user" }),
    ];
    expect(turnFeed(feed, new Set(["optimistic:1"])).map((m) => m.id)).toEqual(["m-1"]);
  });
});
