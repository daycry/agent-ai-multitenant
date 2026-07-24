"use client";

/**
 * La Oficina — burbujas con el ÚLTIMO PASO real EN VIVO (ADR 0118 v2).
 *
 * Abre un WebSocket por run activo (`/ws/executions/{id}`, que reemite cada step
 * con su `summary`) y mantiene `runId → último summary`. El frame lo publica el
 * worker como `{payload:{step:{…}}}` (wrapper F47) — mismo parseo que la página
 * de ejecución. La Oficina inyecta ese summary como la "tarea" del ciudadano →
 * el motor miniverse lo pinta en la burbuja. Cota defensiva de sockets abiertos.
 */

import { useEffect, useRef, useState } from "react";

import { wsUrl } from "@/lib/ws";

const MAX_SOCKETS = 24;
const SUMMARY_MAX = 120;

/** Extrae el `summary` del step de un frame del WS de ejecución, o null. Puro
 * (testeable): pela el wrapper `payload.step` (F47) o usa el payload directo. */
export function parseStepSummary(data: unknown): string | null {
  const frame = data as { payload?: unknown } | null;
  const payload = frame?.payload;
  const step =
    payload && typeof payload === "object" && "step" in payload
      ? (payload as { step?: unknown }).step
      : payload;
  if (step && typeof step === "object" && "summary" in step) {
    const s = (step as { summary?: unknown }).summary;
    if (typeof s === "string" && s.trim()) return s.trim().slice(0, SUMMARY_MAX);
  }
  return null;
}

/**
 * Suscribe un WS por cada `runId` activo y devuelve `{ runId: últimoSummary }`,
 * en vivo. Reconciliación incremental (abre altas, cierra bajas) sin churn; el
 * desmontaje cierra todo. En SSR/tests (sin WebSocket) es un no-op silencioso.
 */
export function useRunStepBubbles(runIds: string[]): Record<string, string> {
  const [summaries, setSummaries] = useState<Record<string, string>>({});
  const socketsRef = useRef<Map<string, WebSocket>>(new Map());
  const key = [...runIds].sort().join(",");

  useEffect(() => {
    if (typeof window === "undefined" || typeof WebSocket === "undefined") return;
    const want = new Set(runIds.slice(0, MAX_SOCKETS));
    const sockets = socketsRef.current;

    // Bajas: cierra sockets de runs que ya no están y olvida su summary.
    for (const [rid, ws] of [...sockets.entries()]) {
      if (want.has(rid)) continue;
      try {
        ws.close();
      } catch {
        /* noop */
      }
      sockets.delete(rid);
      setSummaries((prev) => {
        if (!(rid in prev)) return prev;
        const next = { ...prev };
        delete next[rid];
        return next;
      });
    }

    // Altas: abre un WS por run nuevo.
    for (const rid of want) {
      if (sockets.has(rid)) continue;
      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl(`/ws/executions/${rid}`));
      } catch {
        continue;
      }
      ws.onmessage = (ev: MessageEvent) => {
        try {
          const summary = parseStepSummary(JSON.parse(String(ev.data)));
          if (summary)
            setSummaries((prev) => (prev[rid] === summary ? prev : { ...prev, [rid]: summary }));
        } catch {
          /* frame no-JSON o sin step: ignorar */
        }
      };
      sockets.set(rid, ws);
    }
    // Sin cleanup por-cambio: la reconciliación de arriba gestiona altas/bajas.
  }, [key]); // eslint-disable-line react-hooks/exhaustive-deps

  // Cierre total al desmontar.
  useEffect(() => {
    const sockets = socketsRef.current;
    return () => {
      for (const ws of sockets.values()) {
        try {
          ws.close();
        } catch {
          /* noop */
        }
      }
      sockets.clear();
    };
  }, []);

  return summaries;
}
