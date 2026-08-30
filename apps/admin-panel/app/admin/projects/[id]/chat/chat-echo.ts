/**
 * El eco optimista del mensaje del usuario y su reconciliación con el mensaje
 * persistido (hallazgo H7 del recorrido E2E del 2026-08-29,
 * `docs/roadmap/2026-08-29-hallazgos-e2e-hello-world-v2.md`).
 *
 * **El defecto.** Tras pulsar «Enviar», el mensaje aparecía DOS veces en el
 * feed, las dos con la etiqueta `USER · PLANNING`. En base de datos había uno
 * solo: el envío era correcto, el pintado no.
 *
 * **La causa raíz.** El api-server publica el mensaje en Redis ANTES de
 * contestar al POST (`routers/conversations.py`: `_publish_message_event(...)`
 * y luego `return to_message_response(...)`, con el commit de la dependencia
 * `yield` por medio). El eco del WebSocket gana esa carrera casi siempre, y
 * `postMessage.onSuccess` añadía la respuesta del POST **sin mirar si ese id ya
 * estaba en la caché**. El handler del WebSocket sí deduplicaba por id; la
 * mutación no. Dos filas con la misma `key` de React para un único mensaje.
 *
 * **Los dos arreglos, que son distintos:**
 *
 * 1. `appendUnlessPresent` — deduplicar por id al asentar el POST. Cierra el
 *    duplicado permanente.
 * 2. `mergePendingEchoes` — el eco optimista, para que el usuario vea su
 *    mensaje SIN esperar al ida y vuelta, y para que el persistido lo
 *    SUSTITUYA en vez de sumarse.
 *
 * **Por qué el eco no se guarda en la caché de React Query:** el feed se
 * re-sondea cada 3 s mientras el turno está en vuelo, y un refetch reemplaza el
 * array entero — se llevaría por delante un eco todavía sin confirmar. Vive en
 * estado local de la pantalla y se mezcla al pintar.
 *
 * **La clave de reconciliación, que es lo delicado.** Casar eco y persistido por
 * el TEXTO a secas está mal: repetir la misma frase es legítimo («no funciona»
 * dos veces seguidas) y la segunda desaparecería. Cada eco guarda por eso
 * `seenIds`: los ids que YA estaban en el feed cuando se pulsó «Enviar». Sólo
 * puede cancelarlo un mensaje de usuario con su mismo texto que **no** estuviera
 * ahí antes, y cada persistido cancela como mucho un eco. Con dos envíos
 * idénticos en vuelo, el primero que aterriza cancela uno y deja el otro
 * pintado.
 *
 * La red de seguridad sigue siendo el id temporal: aunque el emparejamiento por
 * contenido no case (porque el backend normalizase el texto, por ejemplo), el
 * eco se retira igualmente cuando la mutación SALE BIEN.
 *
 * **Y sólo cuando sale bien.** Retirarlo en `onSettled` —o sea, también al
 * fallar— convertía un POST rechazado en la desaparición silenciosa del
 * mensaje: el usuario perdía su texto y no veía ningún error. Un eco `failed`
 * se queda pintado, marcado como «no se pudo enviar», con su reintento.
 */

import type { Message } from "./chat-types";

/** Un mensaje enviado que todavía no ha vuelto del servidor. */
export interface PendingEcho {
  /** Id temporal, imposible de confundir con un UUID del servidor. */
  tempId: string;
  conversationId: string;
  content: string;
  mode: string;
  createdAt: string;
  /** Ids que ya estaban en el feed al enviar (ver cabecera del módulo). */
  seenIds: readonly string[];
  /**
   * El POST contestó con un error. El eco NO se retira en ese caso: es el único
   * sitio donde queda el texto que el usuario escribió. Retirarlo «pase lo que
   * pase» era lo que hacía desaparecer el mensaje sin decir nada.
   */
  failed?: boolean;
}

let echoCounter = 0;

/**
 * Id del eco. El prefijo `optimistic:` no es cosmético: garantiza que nunca
 * colisiona con un id del servidor (UUID), que es lo que permite deduplicar por
 * id sin miedo a confundir un eco con un mensaje real.
 */
export function nextEchoId(): string {
  echoCounter += 1;
  return `optimistic:${Date.now()}:${echoCounter}`;
}

/** El eco con la forma de `Message` que el feed sabe pintar. */
export function echoToMessage(echo: PendingEcho): Message {
  return {
    id: echo.tempId,
    tenant_id: "",
    conversation_id: echo.conversationId,
    author_kind: "user",
    author_user_id: null,
    author_agent_id: null,
    content: echo.content,
    mode: echo.mode,
    attachments: [],
    related_plan_id: null,
    is_summary: false,
    created_at: echo.createdAt,
  };
}

/**
 * Añade el mensaje al feed salvo que su id ya esté — el eco del WebSocket pudo
 * adelantarse a la respuesta del POST (H7).
 */
export function appendUnlessPresent(prev: Message[] | undefined, msg: Message): Message[] {
  if (!prev) return [msg];
  if (prev.some((m) => m.id === msg.id)) return prev;
  return [...prev, msg];
}

/**
 * El feed a pintar: los mensajes persistidos más los ecos que todavía no han
 * vuelto. Un eco desaparece en cuanto se le puede asignar su persistido.
 */
export function mergePendingEchoes(
  persisted: readonly Message[],
  pending: readonly PendingEcho[],
): Message[] {
  if (pending.length === 0) return [...persisted];
  const claimed = new Set<string>();
  const out: Message[] = [...persisted];
  for (const echo of pending) {
    const seen = new Set(echo.seenIds);
    const landed = persisted.find(
      (m) =>
        m.author_kind === "user" &&
        m.content === echo.content &&
        !seen.has(m.id) &&
        !claimed.has(m.id),
    );
    if (landed) {
      claimed.add(landed.id);
      continue;
    }
    out.push(echoToMessage(echo));
  }
  return out;
}
