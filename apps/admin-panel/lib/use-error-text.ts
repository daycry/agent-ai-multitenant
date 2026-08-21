"use client";

/**
 * `useErrorText()` — `errorText` ligado al idioma activo (plan prod-16, `task_prod16_05`).
 *
 * Separado de `lib/api-error.ts` por la misma razón que `use-t.ts` lo está de
 * `translate.ts`: la función pura debe poder usarse desde código que no es un
 * componente y testearse sin montar un árbol de React.
 *
 * Sustituye a las 14 copias locales de `function errorText(err: unknown)` que
 * había en el panel. En un componente:
 *
 *     const errorText = useErrorText();
 *     const mutation = useMutation({ onError: (err) => setMsg(errorText(err)) });
 *     …
 *     {query.isError && <p>{errorText(query.error)}</p>}
 *
 * Vale tanto en render como dentro de callbacks creados durante el render
 * (`onError` de `useMutation`), porque la referencia es estable mientras no
 * cambie el idioma.
 */

import { useCallback } from "react";

import { errorText } from "@/lib/api-error";
import { useLangOptional } from "@/lib/lang-context";

export function useErrorText(): (err: unknown) => string {
  const lang = useLangOptional();
  return useCallback((err: unknown) => errorText(err, lang), [lang]);
}
