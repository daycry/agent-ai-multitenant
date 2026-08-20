import { describe, expect, it } from "vitest";

import {
  formatMoney,
  formatTokens,
  progressPercent,
} from "@/app/admin/projects/[id]/plans/[planId]/plan-status-header";

describe("progressPercent", () => {
  it("reports the share of completed tasks", () => {
    expect(progressPercent({ total: 8, done: 2 })).toBe(25);
  });

  it("distinguishes 'no tasks yet' from '0% done'", () => {
    // Son estados distintos: pintar 0% en un plan sin tareas se lee como
    // trabajo parado que no existe.
    expect(progressPercent({ total: 0, done: 0 })).toBeNull();
    expect(progressPercent({ total: 4, done: 0 })).toBe(0);
  });

  it("rounds instead of showing a long fraction", () => {
    expect(progressPercent({ total: 3, done: 1 })).toBe(33);
  });
});

describe("formatMoney", () => {
  it("trims the API's full precision down to cents", () => {
    // La API no recorta dígitos de una medición real; dar formato es cosa de la UI.
    expect(formatMoney("2.500000", "USD")).toBe("2.50 USD");
  });

  it("treats a missing value as zero, not as a crash", () => {
    expect(formatMoney(null, "EUR")).toBe("0.00 EUR");
    expect(formatMoney(undefined, "EUR")).toBe("0.00 EUR");
  });

  it("does not invent a number out of garbage", () => {
    expect(formatMoney("no-es-un-numero", "USD")).toBe("— USD");
  });
});

describe("formatTokens", () => {
  it("reads big counts as magnitudes", () => {
    expect(formatTokens(812_345, "es")).toBe("812,3k");
  });

  /*
   * El separador decimal es del IDIOMA, no del fichero (prod-16
   * `task_prod16_03`). Estaba `"es-ES"` cableado, así que con el panel en
   * inglés la cifra salía «812,3k» donde un lector inglés lee «812.3k». `lang`
   * es obligatorio y sin default a propósito: con default, el próximo llamante
   * reintroduce el fallo sin enterarse.
   */
  it("usa el separador del idioma activo, no el del fichero", () => {
    expect(formatTokens(812_345, "en")).toBe("812.3k");
  });

  it("keeps small counts exact", () => {
    expect(formatTokens(999, "es")).toBe("999");
    expect(formatTokens(999, "en")).toBe("999");
  });

  it("shows a plan that never ran as 0", () => {
    expect(formatTokens(0, "es")).toBe("0");
    expect(formatTokens(null, "es")).toBe("0");
  });
});
