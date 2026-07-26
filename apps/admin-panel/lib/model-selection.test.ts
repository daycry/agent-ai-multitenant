// Reglas compartidas de los selectores de modelo (`plan-unificacion-provider-id`,
// fase 3). La regla estaba escrita dos veces byte a byte; aquí queda una sola,
// probada, y con el motivo por el que existe.

import { describe, expect, it } from "vitest";

import { reasoningLabel, selectableReasoningOptions } from "@/lib/model-selection";

describe("selectableReasoningOptions", () => {
  it("deja las opciones del proveedor tal cual cuando la guardada está entre ellas", () => {
    expect(selectableReasoningOptions("high", ["low", "high", "max"])).toEqual([
      "low",
      "high",
      "max",
    ]);
  });

  it("conserva la guardada que el proveedor ya no ofrece, y la pone primero", () => {
    // Si desapareciera del desplegable, el <select> no casaría con ningún
    // <option> y el siguiente guardado cambiaría la config EN SILENCIO.
    expect(selectableReasoningOptions("xhigh", ["low", "high"])).toEqual(["xhigh", "low", "high"]);
  });

  it("no conserva `off`: es la ausencia de razonamiento, no un valor que perder", () => {
    expect(selectableReasoningOptions("off", ["low", "high"])).toEqual(["low", "high"]);
  });

  it("sin valor guardado devuelve solo lo que ofrece el proveedor", () => {
    expect(selectableReasoningOptions(null, ["low"])).toEqual(["low"]);
    expect(selectableReasoningOptions(undefined, ["low"])).toEqual(["low"]);
    expect(selectableReasoningOptions("", ["low"])).toEqual(["low"]);
  });

  it("un proveedor sin razonamiento y sin valor guardado no ofrece nada", () => {
    // Los llamantes esconden el desplegable con longitud 0; devolver algo aquí
    // haría aparecer un control para una capacidad que el proveedor no tiene.
    expect(selectableReasoningOptions(null, [])).toEqual([]);
  });

  it("un proveedor sin razonamiento pero con valor guardado SÍ lo enseña", () => {
    // El caso al revés del anterior: hay algo que perder, así que se ve.
    expect(selectableReasoningOptions("max", [])).toEqual(["max"]);
  });

  it("no muta la lista que recibe", () => {
    const available = ["low"];
    selectableReasoningOptions("max", available);
    expect(available).toEqual(["low"]);
  });
});

describe("reasoningLabel", () => {
  it("traduce `off` y deja el resto tal cual", () => {
    expect(reasoningLabel("off")).toBe("Desactivado");
    expect(reasoningLabel("high")).toBe("high");
  });

  it("admite la etiqueta de `off` en otro idioma", () => {
    expect(reasoningLabel("off", "Off")).toBe("Off");
  });
});
