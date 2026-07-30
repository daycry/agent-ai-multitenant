/**
 * Diccionario central del panel (plan prod-16, `task_prod16_01`).
 *
 * Un `Record` por módulo, hecho a mano y sin librería: decisión D1 opción B del
 * plan. El panel es interno, sin SEO ni routing por locale, y el catálogo está
 * cerrado en ES+EN — next-intl/i18next serían sobrecoste. La ventaja concreta de
 * hacerlo así es que el tipado es exhaustivo: una clave sin traducción EN no
 * compila.
 *
 * ## Cómo añadir texto
 *
 * 1. Elige el namespace del módulo (créalo si no existe).
 * 2. Añade la clave con sus DOS idiomas.
 * 3. Úsala con `useT("login")` → `t("submit")`.
 *
 * No metas aquí texto que venga del backend ya bilingüe (`note_es`/`note_en`
 * del córtex, por ejemplo): eso se elige con `useLangOptional()`.
 *
 * ## Estado de la migración
 *
 * Esta es la FUNDACIÓN: sólo `common` y `login` están migrados. Los ~100
 * ficheros restantes son `task_prod16_02` a `task_prod16_04` y siguen con
 * ternarios `lang === "es" ? …` inline; `scripts/check-i18n.mjs` lleva la
 * cuenta y su allowlist debe ir MENGUANDO, nunca creciendo.
 */

import type { Dictionary } from "./types";

export const dictionary = {
  /**
   * Textos compartidos entre módulos.
   *
   * Sólo entra aquí lo que YA consume alguien. Una clave sin llamante es la
   * versión i18n del patrón "mecanismo entregado, cero llamantes" que este repo
   * arrastra (verificar-antes-de-implementar §5): envejece sin que nadie lo
   * note. El nombre del producto, en cambio, NO va al diccionario: un nombre
   * propio no se traduce.
   */
  common: {
    loading: { es: "Cargando…", en: "Loading…" },
  },

  /** `app/login/page.tsx`. */
  login: {
    tagline: {
      es: "Panel de administración multi-tenant",
      en: "Multi-tenant administration panel",
    },
    cardTitle: { es: "Iniciar sesión", en: "Sign in" },
    mfaTitle: { es: "Verificación en dos pasos", en: "Two-step verification" },
    // "Email" se escribe igual en los dos idiomas; el test de diccionario lo
    // tiene en su allowlist de coincidencias legítimas.
    emailLabel: { es: "Email", en: "Email" },
    passwordLabel: { es: "Contraseña", en: "Password" },
    submit: { es: "Iniciar sesión", en: "Sign in" },
    submitting: { es: "Entrando…", en: "Signing in…" },
    errorInvalidCredentials: {
      es: "Email o contraseña incorrectos.",
      en: "Invalid email or password.",
    },
    errorRateLimited: {
      es: "Demasiados intentos. Espera un momento y vuelve a intentarlo.",
      en: "Too many attempts. Please wait and try again.",
    },
    errorUnreachable: {
      es: "No se pudo contactar con el servidor.",
      en: "Could not reach the server.",
    },
  },
} as const satisfies Dictionary;

/** La forma exacta del diccionario, para derivar las claves válidas. */
export type DictionaryShape = typeof dictionary;

/** Namespaces existentes (`"common" | "login"`). */
export type NamespaceName = keyof DictionaryShape;

/** Claves válidas de un namespace concreto. */
export type MessageKey<N extends NamespaceName> = keyof DictionaryShape[N] & string;
