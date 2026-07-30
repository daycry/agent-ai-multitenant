/**
 * Resolución de textos: función pura, sin React (plan prod-16, `task_prod16_01`).
 *
 * Separada del hook a propósito, para poder testear el diccionario y la
 * interpolación sin montar un árbol de componentes, y para poder traducir desde
 * código que no es un componente (helpers de `lib/`).
 */

import { dictionary, type MessageKey, type NamespaceName } from "./dictionary";
import type { Lang, TranslationVars } from "./types";

/**
 * Sustituye los marcadores `{nombre}` por sus valores.
 *
 * Recorre la plantilla UNA vez (`String.replace` con función), de modo que un
 * valor que contenga `{otra}` no se vuelve a expandir. Un marcador sin valor se
 * deja tal cual: en pantalla se ve el hueco, que es mejor pista de un bug que
 * una cadena vacía.
 */
export function interpolate(template: string, vars?: TranslationVars): string {
  if (!vars) return template;

  return template.replace(/\{([a-zA-Z0-9_]+)\}/g, (whole, name: string) => {
    const value = vars[name];
    return value === undefined ? whole : String(value);
  });
}

/**
 * El texto de `namespace.key` en `lang`, con las variables interpoladas.
 *
 * No hay fallback entre idiomas porque no puede hacer falta: el tipo
 * `Translation` obliga a rellenar ES y EN en cada clave.
 */
export function translate<N extends NamespaceName>(
  lang: Lang,
  namespace: N,
  key: MessageKey<N>,
  vars?: TranslationVars,
): string {
  const entry = (dictionary[namespace] as Record<string, Record<Lang, string>>)[key];
  return interpolate(entry[lang], vars);
}

/** La función que devuelve `useT(namespace)`. */
export type Translator<N extends NamespaceName> = (
  key: MessageKey<N>,
  vars?: TranslationVars,
) => string;
