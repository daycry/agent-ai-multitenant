"use client";

/**
 * `useT(namespace)` — el hook que consumen las pantallas (plan prod-16).
 *
 * Se apoya en el `LanguageProvider` que ya existía (`lib/lang-context.tsx`), no
 * en un contexto nuevo: el selector ES/EN del header y la persistencia en
 * localStorage siguen siendo los mismos.
 *
 * Usa `useLangOptional()` en lugar de `useLang()` para que un componente se
 * pueda renderizar aislado en un test sin envolverlo en el provider; ahí cae al
 * default documentado (ES), que es el comportamiento correcto, no un fallo
 * silencioso. Quien necesite `setLang` sigue usando `useLang()`, y ahí la
 * ausencia del provider SÍ debe lanzar.
 */

import { useCallback } from "react";

import { useLangOptional } from "@/lib/lang-context";

import type { MessageKey, NamespaceName } from "./dictionary";
import { translate, type Translator } from "./translate";
import type { TranslationVars } from "./types";

export function useT<N extends NamespaceName>(namespace: N): Translator<N> {
  const lang = useLangOptional();

  return useCallback(
    (key: MessageKey<N>, vars?: TranslationVars) => translate(lang, namespace, key, vars),
    [lang, namespace],
  );
}
