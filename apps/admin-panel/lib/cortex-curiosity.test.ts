// Córtex F4 (Sub-fase 4.5) — helpers puros del panel «Lo que está aprendiendo».
//
// Por qué existen: las etiquetas del ciclo de vida de un pursuit y el formato del
// budget eran un `const` inline dentro de `app/admin/cortex/mind/page.tsx`, sin
// una sola prueba, y sólo en castellano. Tres cosas se rompían en silencio:
//
//   1. una etiqueta que falta ⇒ la UI enseña el SLUG del estado (`digested`), que
//      al owner no le dice nada;
//   2. el budget mal formateado ⇒ el owner cree que no hay tope (o que se ha
//      pasado) y toca el kill-switch sin motivo;
//   3. el copy honesto sólo en ES ⇒ el requisito ES+EN del proyecto (CLAUDE.md
//      §12) se incumple justo en el aviso que el ADR 0075 §6 hace obligatorio.
//
// Los estados son un catálogo CERRADO (CHECK de `cortex_curiosity_pursuits`):
// selected | searching | digested | surfaced | skipped | failed.

import { describe, expect, it } from "vitest";

import {
  autonomyHonestNote,
  budgetUsageLabel,
  budgetUsageRatio,
  honestNote,
  PURSUIT_STATUSES,
  pursuitAwaitsApproval,
  pursuitStatusLabel,
} from "./cortex-curiosity";

describe("pursuitStatusLabel", () => {
  it("traduce los seis estados del catálogo cerrado, en ES y EN", () => {
    // La tabla ES la fija el test entera: es el contrato con la pantalla, y una
    // entrada que falte devolvería el slug sin que nada protestara.
    expect(PURSUIT_STATUSES.map((s) => pursuitStatusLabel(s, "es"))).toEqual([
      "elegido",
      "investigando",
      "aprendido — pendiente de contarlo",
      "comentado en conversación",
      "descartado",
      "falló",
    ]);
    expect(PURSUIT_STATUSES.map((s) => pursuitStatusLabel(s, "en"))).toEqual([
      "picked",
      "researching",
      "learned — not shared yet",
      "mentioned in conversation",
      "skipped",
      "failed",
    ]);
    // Ningún estado se le enseña al hispanohablante como slug inglés.
    for (const status of PURSUIT_STATUSES) {
      expect(pursuitStatusLabel(status, "es")).not.toBe(status);
    }
    // OJO: en EN, `skipped`/`failed` COINCIDEN con el slug — es la palabra
    // correcta, no una traducción olvidada. Por eso el aserto de "nunca el slug"
    // sólo aplica a ES; la completitud de la tabla EN la fija la lista de arriba.
  });

  it("mantiene el copy ES que ya lee la pantalla (no rompe la UI existente)", () => {
    expect(pursuitStatusLabel("digested")).toBe("aprendido — pendiente de contarlo");
    expect(pursuitStatusLabel("surfaced")).toBe("comentado en conversación");
  });

  it("un estado desconocido cae al slug, no a una cadena vacía", () => {
    // Si el backend añade un estado, es mejor ver `paused` que un hueco: el
    // hueco parece un bug de datos y el slug avisa de que falta traducirlo.
    expect(pursuitStatusLabel("paused")).toBe("paused");
    expect(pursuitStatusLabel("")).toBe("");
  });

  it("por defecto habla castellano (el panel arranca en ES)", () => {
    expect(pursuitStatusLabel("searching")).toBe(pursuitStatusLabel("searching", "es"));
  });
});

describe("pursuitAwaitsApproval", () => {
  it("es true SOLO en `selected` con la decisión sin tomar (gate activo)", () => {
    expect(pursuitAwaitsApproval({ status: "selected", approved: null })).toBe(true);
    expect(pursuitAwaitsApproval({ status: "selected" })).toBe(true);
  });

  it("es false en cuanto el owner decidió, aunque el estado no haya avanzado aún", () => {
    // El bucle mueve selected→searching/skipped en su siguiente pasada; entre la
    // decisión y la pasada NO se deben volver a ofrecer los botones (doble
    // aprobación = doble gasto).
    expect(pursuitAwaitsApproval({ status: "selected", approved: true })).toBe(false);
    expect(pursuitAwaitsApproval({ status: "selected", approved: false })).toBe(false);
  });

  it("es false en cualquier otro estado (ya salió del gate)", () => {
    for (const status of ["searching", "digested", "surfaced", "skipped", "failed"]) {
      expect(pursuitAwaitsApproval({ status, approved: null })).toBe(false);
    }
  });
});

describe("budgetUsageLabel", () => {
  it("dice consumido, cap y porcentaje, en ES y EN", () => {
    expect(budgetUsageLabel(3, 10, "es")).toBe("3 de 10 búsquedas hoy (30 %)");
    expect(budgetUsageLabel(3, 10, "en")).toBe("3 of 10 searches today (30%)");
  });

  it("agotado es 100 % y no se pasa de ahí aunque el contador se desmadre", () => {
    // Redis puede quedar por encima del cap si el cap baja a media jornada: el
    // porcentaje se topa en 100 para no pintar una barra desbordada.
    expect(budgetUsageLabel(10, 10, "es")).toBe("10 de 10 búsquedas hoy (100 %)");
    expect(budgetUsageLabel(14, 10, "es")).toBe("14 de 10 búsquedas hoy (100 %)");
  });

  it("sin cap configurado lo dice, en vez de dividir por cero", () => {
    expect(budgetUsageLabel(0, 0, "es")).toBe("0 búsquedas hoy · sin cupo configurado");
    expect(budgetUsageLabel(2, 0, "en")).toBe("2 searches today · no cap configured");
  });

  it("redondea y saca los negativos (el contador nunca es fraccionario)", () => {
    expect(budgetUsageLabel(2.6, 9.2, "es")).toBe("3 de 9 búsquedas hoy (33 %)");
    expect(budgetUsageLabel(-4, 10, "es")).toBe("0 de 10 búsquedas hoy (0 %)");
  });
});

describe("budgetUsageRatio", () => {
  it("da la fracción [0,1] para la barra de progreso", () => {
    expect(budgetUsageRatio(0, 10)).toBe(0);
    expect(budgetUsageRatio(5, 10)).toBe(0.5);
    expect(budgetUsageRatio(10, 10)).toBe(1);
  });

  it("clampa por arriba y por abajo, y sin cap devuelve 0", () => {
    expect(budgetUsageRatio(30, 10)).toBe(1);
    expect(budgetUsageRatio(-3, 10)).toBe(0);
    expect(budgetUsageRatio(3, 0)).toBe(0);
  });
});

describe("honestNote", () => {
  it("devuelve la nota del idioma activo (la API manda las dos)", () => {
    const block = { note_es: "Comportamiento programado.", note_en: "Programmed behaviour." };
    expect(honestNote(block, "es")).toBe("Comportamiento programado.");
    expect(honestNote(block, "en")).toBe("Programmed behaviour.");
  });

  it("si falta la del idioma pedido cae a la otra (mejor un aviso que ninguno)", () => {
    // El aviso de honestidad es OBLIGATORIO (ADR 0075 §6): antes que dejarlo en
    // blanco por un campo vacío, se muestra en el otro idioma.
    expect(honestNote({ note_es: "", note_en: "Programmed." }, "es")).toBe("Programmed.");
    expect(honestNote({ note_es: "Programado.", note_en: "   " }, "en")).toBe("Programado.");
  });

  it("sin ninguna nota devuelve cadena vacía y el llamante pone su fallback", () => {
    expect(honestNote({}, "es")).toBe("");
    expect(honestNote({ note_es: null, note_en: null }, "en")).toBe("");
  });
});

describe("autonomyHonestNote", () => {
  // Es `honestNote` MÁS la garantía de que nunca sale vacío. La tarjeta de
  // autonomía enseña el kill-switch y el gasto del día: sin el aviso, esos dos
  // controles quedan sin el contexto que el ADR 0075 §6 declara no removible.
  it("prefiere la nota del backend cuando la hay (no la pisa con el respaldo)", () => {
    const block = { note_es: "Nota del backend.", note_en: "Backend note." };
    expect(autonomyHonestNote(block, "es")).toBe("Nota del backend.");
    expect(autonomyHonestNote(block, "en")).toBe("Backend note.");
  });

  it("sin ninguna nota cae al respaldo del diccionario, en el idioma pedido", () => {
    const es = autonomyHonestNote({}, "es");
    const en = autonomyHonestNote({ note_es: null, note_en: "  " }, "en");
    expect(es).toContain("comportamiento programado, no curiosidad consciente");
    expect(en).toContain("programmed behaviour, not conscious curiosity");
    // Y son textos DISTINTOS: un respaldo copiado dejaría al panel en inglés
    // enseñando castellano, que es el fallo que ya ocurrió con el banner de afecto.
    expect(es).not.toBe(en);
  });

  it("nunca devuelve vacío — la diferencia entera con `honestNote`", () => {
    expect(honestNote({}, "es")).toBe("");
    for (const lang of ["es", "en"] as const) {
      expect(autonomyHonestNote({}, lang).trim().length).toBeGreaterThan(0);
    }
  });
});
