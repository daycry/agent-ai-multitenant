"use client";

/**
 * Idioma activo del panel (ES por defecto, EN como única alternativa
 * en esta versión — ver CLAUDE.md §12 "Idiomas soportados").
 *
 * El contexto vive a nivel de `app/admin/layout.tsx`, de modo que
 * cualquier pantalla bajo `/admin/*` puede leer/escribir la preferencia
 * sin prop-drilling. Persistimos en `localStorage` para que el toggle
 * sobreviva a recargas; la lectura inicial se hace en `useEffect` para
 * evitar mismatches de hidratación SSR/CSR (el primer render usa el
 * default ES, el segundo aplica el valor guardado si existe).
 */

import { createContext, useContext, useEffect, useState, type ReactNode } from "react";

export type Lang = "es" | "en";

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
