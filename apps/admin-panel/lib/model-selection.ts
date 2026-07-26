/**
 * Reglas compartidas de los selectores de modelo (ADR 0082).
 *
 * Cierra la Fase 3 del plan `plan-unificacion-provider-id`. Los cuatro
 * selectores (persona, chat, asistente, córtex) ya eligen por PROVEEDOR
 * CONCRETO, que era el objetivo del plan; lo que quedaba duplicado era esta
 * regla, escrita **dos veces byte a byte** —en `ProviderModelSelects` y en
 * `CortexModelSection`— y es justo el tipo de regla que diverge sin que nadie
 * lo note, porque solo se manifiesta en una configuración concreta.
 *
 * Por qué NO se fusionaron los componentes: el del córtex vive tras
 * `require_system_owner` y lee `/owner/cortex/model-options`, mientras que el
 * compartido lee `/agents/provider-options`, que exige pertenencia a un tenant
 * — y el córtex es tenant-less por diseño (ADR 0074). No es divergencia
 * cosmética: es otro ámbito de autorización. Además el córtex no tiene
 * temperatura. Se comparte la REGLA, no el widget.
 */

/**
 * Las opciones de razonamiento a mostrar, conservando la guardada aunque el
 * proveedor ya no la ofrezca.
 *
 * La razón: si el operador tenía guardado `xhigh` y cambia a un proveedor que
 * no lo lista, quitarlo del desplegable haría que el `<select>` no case con
 * ningún `<option>` y el siguiente guardado **cambiaría la configuración en
 * silencio**. Se prefiere enseñarla —aunque sea inválida para ese proveedor—
 * para que el cambio sea una decisión y no un efecto secundario.
 *
 * `off` no se conserva: es la ausencia de razonamiento, no un valor que perder.
 */
export function selectableReasoningOptions(
  current: string | null | undefined,
  available: readonly string[],
): string[] {
  const options = [...available];
  if (current && current !== "off" && !options.includes(current)) {
    return [current, ...options];
  }
  return options;
}

/** Etiqueta de una opción de razonamiento (`off` → texto; el resto, tal cual). */
export function reasoningLabel(option: string, offLabel = "Desactivado"): string {
  return option === "off" ? offLabel : option;
}
