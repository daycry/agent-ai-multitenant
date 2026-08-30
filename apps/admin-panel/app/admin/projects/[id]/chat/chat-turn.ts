/**
 * ¿Está el equipo trabajando en el turno? (hallazgo H8 del recorrido E2E del
 * 2026-08-29, `docs/roadmap/2026-08-29-hallazgos-e2e-hello-world-v2.md`).
 *
 * **El defecto.** En pantalla convivían el aviso «el equipo del proyecto no
 * tiene agentes configurados, así que no puede responder» y el indicador «El
 * equipo está pensando… (esto puede tardar)». El usuario esperaba a que
 * terminase algo que no había empezado.
 *
 * **La causa raíz.** Ese aviso es un mensaje `system` que el responder persiste
 * con el `mode` del turno y sin adjuntos
 * (`chat/responder.py::_system_notice`), e `isReplyInFlight` da por vivo
 * cualquier mensaje de planning reciente que no traiga adjunto. O sea que el
 * propio mensaje que declara que NADIE va a contestar era el que encendía el
 * indicador — y lo mantenía encendido los 180 s de la ventana.
 *
 * **La regla.** Un mensaje `system` CIERRA el turno. Los siete
 * `_system_notice` del responder son terminales sin excepción (sin agentes, sin
 * proveedor LLM, timeout, error, respuesta vacía, plan no estructurable, el
 * equipo no pudo elaborar respuesta): después de cualquiera de ellos la corutina
 * retorna. Y el «Modo cambiado» que escribe `PUT /conversations/{id}` ni
 * siquiera pertenece a un turno, así que también apaga el indicador — el mismo
 * defecto por otra puerta.
 *
 * **La excepción, y no es un detalle.** Un RESUMEN plegado es también `system`,
 * pero se emite a MITAD del turno: `compress_conversation_best_effort` corre
 * antes de que el equipo conteste. Tratarlo como terminal apagaría el indicador
 * justo cuando la ronda arranca. Por eso la regla mira `is_summary` y no sólo
 * `author_kind`.
 *
 * Lo que NO se hace aquí: mirar el TEXTO del aviso. Sería atar la UI a una
 * cadena en castellano del backend, que se rompe en cuanto alguien la reescribe
 * o la traduce. La forma del mensaje basta.
 */

import { CHAT_POLL_MS, isReplyInFlight } from "@/lib/chat-feed";

/** Lo que estos helpers necesitan de un mensaje del feed. */
export interface TurnMessage {
  author_kind: "user" | "agent" | "system";
  mode: string;
  created_at: string;
  attachments?: Array<Record<string, unknown>>;
  is_summary?: boolean;
}

/**
 * El feed que decide el turno: lo que se pinta MENOS los envíos que fallaron.
 *
 * **Por qué hace falta separarlos.** El arreglo de H7 dejó el eco de un POST
 * rechazado pintado a propósito —es el único sitio donde sobrevive el texto que
 * el usuario escribió—, pero ese eco es un mensaje `author_kind: "user"` al
 * final del feed, y para `isReplyInFlight` eso significa «esperando la primera
 * respuesta». O sea que arreglar H7 reintrodujo H8 por otra puerta: encima del
 * cartel «no se pudo enviar» seguía girando «El equipo está pensando…».
 *
 * La regla, en una línea: **un mensaje que no llegó al servidor no empieza
 * ningún turno**. Un eco todavía EN VUELO sí lo empieza y por eso no se filtra:
 * el POST sigue camino y el equipo va a contestar.
 *
 * Se filtra por id —el del eco, con su prefijo `optimistic:`— y no por una marca
 * dentro del mensaje: `echoToMessage` fabrica un `Message` indistinguible de uno
 * del servidor a propósito, para que el feed no tenga que saber de ecos.
 */
export function turnFeed<T extends TurnMessage & { id: string }>(
  messages: readonly T[],
  failedIds: ReadonlySet<string>,
): T[] {
  if (failedIds.size === 0) return [...messages];
  return messages.filter((m) => !failedIds.has(m.id));
}

/**
 * ¿Cerró el turno un aviso del sistema? Un resumen plegado no cuenta: es
 * historia comprimida a mitad de ronda, no el final de nada.
 */
export function turnClosedByNotice(messages: readonly TurnMessage[] | undefined): boolean {
  if (!messages || messages.length === 0) return false;
  const last = messages[messages.length - 1];
  return last.author_kind === "system" && !last.is_summary;
}

/**
 * ¿Sigue el equipo elaborando la respuesta? Es lo que decide el indicador «El
 * equipo está pensando…»: `isReplyInFlight` menos los turnos que un aviso ya
 * cerró.
 */
export function isTeamWorking(
  messages: readonly TurnMessage[] | undefined,
  nowMs: number,
): boolean {
  if (turnClosedByNotice(messages)) return false;
  return isReplyInFlight(messages, nowMs);
}

/**
 * `refetchInterval` del feed. Va por el MISMO criterio que el indicador a
 * propósito: sondear cada 3 s durante tres minutos algo que ya se sabe que
 * nadie va a escribir es la otra mitad del mismo error.
 */
export function chatPollInterval(
  messages: readonly TurnMessage[] | undefined,
  nowMs: number,
): number | false {
  return isTeamWorking(messages, nowMs) ? CHAT_POLL_MS : false;
}
