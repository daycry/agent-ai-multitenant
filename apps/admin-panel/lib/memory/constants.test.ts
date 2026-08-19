/**
 * `MEMORY_SCOPE_OPTIONS` — el `<Select>` de política de memoria (prod-16
 * `task_prod16_03`).
 *
 * Este fichero era un hueco conocido y anotado: las cuatro etiquetas estaban
 * cableadas en castellano dentro de la constante, así que las DOS pantallas que
 * lo consumen —la política de memoria del equipo (`teams/[team_id]`) y la ficha
 * del agente (`agents/[id]/agent-edit-dialog`)— seguían pintando «Privada» y
 * «Compartida con equipo» con el toggle en EN, estando las dos ya migradas por
 * lo demás. El módulo es puro (sin React), de ahí que la traducción se resuelva
 * con `translate(lang, …)` y no con `useT()`.
 *
 * Se testea aquí y no sólo en las pantallas porque es la fuente ÚNICA: si
 * alguien añade un scope nuevo sin su par inglés, salta acá y no en la pantalla
 * que lo pinte primero.
 */

import { describe, expect, it } from "vitest";

import { translate } from "@/lib/i18n";
import { LANGS } from "@/lib/i18n";

import { MEMORY_SCOPE_OPTIONS, memoryScopeLabel } from "./constants";

describe("MEMORY_SCOPE_OPTIONS", () => {
  it("espeja el enum MemoryScope del backend, en orden", () => {
    expect(MEMORY_SCOPE_OPTIONS.map((o) => o.value)).toEqual([
      "private",
      "team_shared",
      "project_shared",
      "global",
    ]);
  });

  it("cada opción tiene texto en los dos idiomas, y distinto en cada uno", () => {
    // Guarda contra el paso en vacío (verificar-antes-de-implementar §4): si la
    // constante se quedara sin opciones, el bucle no afirmaría nada.
    expect(MEMORY_SCOPE_OPTIONS.length).toBe(4);

    for (const option of MEMORY_SCOPE_OPTIONS) {
      const texts = LANGS.map((lang) => translate(lang, "memoryScope", option.key));
      for (const text of texts) expect(text.trim()).not.toBe("");
      // Los cuatro scopes son prosa, no identificadores: ninguno se escribe
      // igual en los dos idiomas.
      expect(texts[0]).not.toBe(texts[1]);
    }
  });
});

describe("memoryScopeLabel", () => {
  it("traduce el scope al idioma pedido", () => {
    expect(memoryScopeLabel("team_shared", "es")).toBe("Compartida con equipo");
    expect(memoryScopeLabel("team_shared", "en")).toBe("Shared with team");
  });

  it("cae al valor crudo ante un scope que no conoce, y a «—» si no hay ninguno", () => {
    // Un scope nuevo del backend que el panel aún no conozca: enseñar su valor
    // crudo es mejor pista que un hueco en blanco.
    expect(memoryScopeLabel("some_new_scope", "en")).toBe("some_new_scope");
    expect(memoryScopeLabel(null, "en")).toBe("—");
    expect(memoryScopeLabel(undefined, "es")).toBe("—");
  });
});
