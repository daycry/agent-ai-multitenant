"use client";

import { useEffect } from "react";

/**
 * Señal REAL de hidratación para los e2e: pone `data-hydrated="true"` en `<html>`
 * cuando React ya ha montado el shell (un `useEffect` sólo corre tras hidratar).
 * Un clic de Playwright antes de esto se pierde porque aún no hay `onClick`.
 */
export function HydrationMarker() {
  useEffect(() => {
    document.documentElement.setAttribute("data-hydrated", "true");
    return () => document.documentElement.removeAttribute("data-hydrated");
  }, []);
  return null;
}
