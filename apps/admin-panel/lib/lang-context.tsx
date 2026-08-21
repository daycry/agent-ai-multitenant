"use client";

/**
 * Idioma activo del panel (ES por defecto, EN como única alternativa
 * en esta versión — ver CLAUDE.md §12 "Idiomas soportados").
 *
 * El contexto vive a nivel de `app/providers.tsx` — o sea, en el layout RAÍZ —
 * para que también lo vean las pantallas de sesión (`/login`,
 * `/select-tenant`, `/no-access`), que están fuera de `/admin/*`. Estuvo en
 * `app/admin/layout.tsx` hasta prod-16 `task_prod16_01`, y por eso el login
 * seguía siendo bilingüe a mano. **No lo montes dos veces**: un provider
 * anidado tapa al de arriba y el selector del header dejaría de afectar al
 * login.
 *
 * Persistimos en `localStorage` para que el toggle sobreviva a recargas; la
 * lectura inicial se hace en `useEffect` para evitar mismatches de hidratación
 * SSR/CSR (el primer render usa el default ES, el segundo aplica el valor
 * guardado si existe).
 *
 * `Lang` se re-exporta desde `lib/i18n/types` (donde vive ahora, para que el
 * diccionario no dependa de React); los ficheros que lo importaban de aquí
 * siguen funcionando.
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

import type { Lang } from "@/lib/i18n/types";

export type { Lang };

interface LangContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
}

const LangContext = createContext<LangContextValue | null>(null);
const STORAGE_KEY = "admin-panel.lang";

export function LanguageProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>("es");

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    if (stored === "es" || stored === "en") {
      setLangState(stored);
    }
  }, []);

  // `app/layout.tsx` sirve `<html lang="es">` (el default real). Aquí se
  // sincroniza con el idioma activo, que es lo que leen los lectores de
  // pantalla y la corrección ortográfica del navegador.
  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  function setLang(next: Lang) {
    setLangState(next);
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, next);
    }
  }

  return <LangContext.Provider value={{ lang, setLang }}>{children}</LangContext.Provider>;
}

export function useLang(): LangContextValue {
  const ctx = useContext(LangContext);
  if (!ctx) {
    throw new Error("useLang must be used inside <LanguageProvider>");
  }
  return ctx;
}

/**
 * El idioma activo, o `es` si el árbol NO tiene `<LanguageProvider>`.
 *
 * Para componentes que sólo eligen entre dos textos que ya vienen del backend
 * (p. ej. `note_es`/`note_en` del córtex) y que se montan también fuera del
 * layout de `/admin` (render aislado en tests). `useLang` sigue lanzando a
 * propósito cuando alguien necesita `setLang`: ahí la ausencia del provider SÍ
 * es un error de montaje.
 */
export function useLangOptional(): Lang {
  return useContext(LangContext)?.lang ?? "es";
}
