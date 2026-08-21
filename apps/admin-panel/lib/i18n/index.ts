/**
 * Punto de entrada de la capa i18n (plan prod-16, `task_prod16_01`).
 *
 *   import { useT } from "@/lib/i18n";
 *   const t = useT("login");
 *   <Button>{t("submit")}</Button>
 *
 * El diccionario vive en `dictionary.ts`; los tipos en `types.ts`.
 */

export { dictionary } from "./dictionary";
export type { DictionaryShape, MessageKey, NamespaceName } from "./dictionary";
export { interpolate, pickLang, translate } from "./translate";
export type { Translator } from "./translate";
export { LANGS } from "./types";
export type { Dictionary, Lang, Namespace, Translation, TranslationVars } from "./types";
export { useT } from "./use-t";
