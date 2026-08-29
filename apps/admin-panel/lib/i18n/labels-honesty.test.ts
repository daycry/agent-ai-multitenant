/**
 * Dos rótulos del panel prometían algo que el código no hace, y este fichero
 * los ata a la verdad (ADR 0162).
 *
 * No es un test de traducción —de eso ya se ocupa `i18n.test.ts`—: es un test de
 * HONESTIDAD. La diferencia importa porque las dos mentiras estaban
 * perfectamente traducidas a los dos idiomas; lo que fallaba no era el idioma,
 * era el contenido. Un rótulo que describe una capacidad que no existe se lee
 * exactamente igual de bien en inglés.
 *
 * Se afirma sobre el diccionario y no sobre la pantalla a propósito: el texto lo
 * consumen tres pantallas distintas (la ficha de la tarea, el panel de tareas
 * escaladas y el selector de modo del chat), y afirmar en una sola dejaría a las
 * otras dos sin guarda.
 */

import { describe, expect, it } from "vitest";

import { dictionary } from "./dictionary";
import { LANGS } from "./types";

describe("«Reasignar con guía» no reasignaba nada", () => {
  /*
   * `task_lifecycle.py` mapea `reassign_with_guidance` a
   * `("backlog", "human_action", True)`: la tarea vuelve al backlog, se anota la
   * guía y se SUMA un reintento. El agente asignado no se toca. El botón decía
   * «Reasignar», que es justo lo único que no hace.
   */
  it("el botón nombra el backlog, no una reasignación", () => {
    for (const lang of LANGS) {
      const label = dictionary.taskActions.reassign[lang];
      expect(label.toLowerCase()).toContain("backlog");
      expect(label.toLowerCase()).not.toMatch(/reasign|reassign/);
    }
  });

  it("el botón de confirmar del diálogo dice lo mismo que el que lo abre", () => {
    for (const lang of LANGS) {
      const submit = dictionary.taskActions.reassignSubmit[lang];
      expect(submit.toLowerCase()).not.toMatch(/reasign|reassign/);
    }
  });

  it("la ayuda avisa de que consume un reintento, que es el efecto que no se ve", () => {
    // El contador de reintentos es lo que acaba bloqueando la tarea. Gastarlo
    // sin decirlo convierte un gesto de desbloqueo en el paso previo a un
    // bloqueo definitivo.
    expect(dictionary.taskActions.reassignDescription.es.toLowerCase()).toContain("reintento");
    expect(dictionary.taskActions.reassignDescription.en.toLowerCase()).toContain("retry");
  });
});

describe("el modo «Ejecución» del chat no ejecuta nada", () => {
  /*
   * `chat/responder.py` bifurca SÓLO en `planning`; `discussion` y `execution`
   * caen los dos en `_simple_reply`, que es una única llamada al LLM sin tools.
   * El hint prometía «El equipo ejecuta tareas del plan aprobado». La ejecución
   * real se arranca con `POST /plans/{id}/start-execution`, detrás del botón
   * `planDetail.lifecycleStart`.
   */
  it("el hint no promete que se ejecute nada", () => {
    expect(dictionary.projectChat.modeExecutionHint.es.toLowerCase()).not.toMatch(
      /ejecuta tareas|ejecuta las tareas/,
    );
    expect(dictionary.projectChat.modeExecutionHint.en.toLowerCase()).not.toMatch(
      /runs the tasks|executes the tasks/,
    );
  });

  it("el hint manda al sitio donde SÍ se arranca la ejecución", () => {
    // Se cita el rótulo real del botón, no una paráfrasis: si alguien renombra
    // «Empezar ejecución», este test señala el hint que se quedó apuntando a un
    // botón que ya no se llama así.
    for (const lang of LANGS) {
      expect(dictionary.projectChat.modeExecutionHint[lang]).toContain(
        dictionary.planDetail.lifecycleStart[lang],
      );
    }
  });
});
