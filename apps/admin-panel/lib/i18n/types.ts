/**
 * Tipos de la capa i18n del panel (plan prod-16, `task_prod16_01`).
 *
 * El catálogo de idiomas está CERRADO en ES+EN por el principio 12 de
 * CLAUDE.md: añadir un tercero no es "meter un fichero más", es una decisión de
 * producto que pide ADR. Por eso `Lang` es una unión literal y no `string`.
 *
 * `Lang` se define AQUÍ (no en `lib/lang-context.tsx`) para que el diccionario
 * no dependa de React; `lang-context` lo re-exporta para no romper a los ~15
 * ficheros que ya lo importaban de allí.
 */

/** Los idiomas soportados, en orden de preferencia (ES primero: es el default). */
export const LANGS = ["es", "en"] as const;

export type Lang = (typeof LANGS)[number];

/** Un texto, en todos los idiomas soportados. Falta uno ⇒ no compila. */
export type Translation = Record<Lang, string>;

/**
 * Un grupo de textos de una pantalla o módulo (`login`, `common`, …).
 *
 * La forma es "clave-mayor" (`{ submit: { es, en } }`) y no "idioma-mayor"
 * (`{ es: {...}, en: {...} }`) precisamente para que el compilador exija los
 * dos idiomas en CADA clave: con la forma idioma-mayor, olvidar una clave en el
 * bloque EN se detecta sólo si alguien acuerda derivar el tipo del bloque ES.
 */
export type Namespace = Record<string, Translation>;

export type Dictionary = Record<string, Namespace>;

/** Valores que se pueden interpolar en un texto (`"Hola {name}"`). */
export type TranslationVars = Record<string, string | number>;
