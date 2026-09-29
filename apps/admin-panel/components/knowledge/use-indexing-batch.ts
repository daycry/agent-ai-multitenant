"use client";

/**
 * Sondeo del lote de documentos que acaba de subir el asistente (`task_ui_21`).
 *
 * UNA petición por ciclo, `GET …/documents?ids=…`, con sólo los ids del lote que
 * aún no son terminales (no la página entera del listado). Acaba cuando todos son
 * terminales — `indexed_empty` incluido —, cuando un id deja de volver en la
 * respuesta (borrado: terminal, `null`), cuando el diálogo se cierra
 * (`enabled` = false) o al pasar `maxMs`, con `timedOut` para avisar de que sigue
 * indexando en segundo plano.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { apiFetch } from "@/lib/api";

import { isTerminal, type KbDocument } from "./kb-types";

export const POLL_MS = 1500;
export const MAX_POLL_MS = 5 * 60 * 1000;
/** Tope del filtro `ids` del backend (= tamaño máximo de un lote de subida). */
const MAX_IDS = 200;

export interface UseIndexingBatchOptions {
  kbId: string | null;
  docIds: string[];
  enabled: boolean;
  pollMs?: number;
  maxMs?: number;
}

export function useIndexingBatch({
  kbId,
  docIds,
  enabled,
  pollMs = POLL_MS,
  maxMs = MAX_POLL_MS,
}: UseIndexingBatchOptions) {
  /** `null` = el documento ya no existe (404). */
  const [docs, setDocs] = useState<Record<string, KbDocument | null>>({});
  const [timedOut, setTimedOut] = useState(false);
  const [nonce, setNonce] = useState(0);
  const docsRef = useRef(docs);
  docsRef.current = docs;
  const startedAt = useRef<number | null>(null);
  const idsKey = docIds.join(",");

  useEffect(() => {
    if (!enabled || kbId === null || idsKey === "") return;
    const ids = idsKey.split(",");
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    if (startedAt.current === null) startedAt.current = Date.now();

    /** UNA petición con todos los pendientes (`?ids=`); lo que no vuelve = borrado. */
    async function fetchPending(
      pending: string[],
    ): Promise<Record<string, KbDocument | null> | undefined> {
      const fresh: Record<string, KbDocument | null> = {};
      for (let i = 0; i < pending.length; i += MAX_IDS) {
        const slice = pending.slice(i, i + MAX_IDS);
        const qs = slice.map((id) => `ids=${encodeURIComponent(id)}`).join("&");
        try {
          const rows = await apiFetch<KbDocument[]>(
            `/knowledge-bases/${kbId}/documents?${qs}&limit=${MAX_IDS}`,
          );
          const byId = new Map(rows.map((d) => [d.id, d]));
          for (const id of slice) fresh[id] = byId.get(id) ?? null;
        } catch {
          // error transitorio: se reintenta en el siguiente ciclo
        }
      }
      return fresh;
    }

    async function tick() {
      const pending = ids.filter((id) => {
        const d = docsRef.current[id];
        return d === undefined || (d !== null && !isTerminal(d.status));
      });
      if (pending.length === 0 || cancelled) return;
      if (Date.now() - (startedAt.current ?? Date.now()) > maxMs) {
        setTimedOut(true);
        return;
      }
      const fresh = await fetchPending(pending);
      if (cancelled) return;
      setDocs((prev) => ({ ...prev, ...fresh }));
      timer = setTimeout(() => void tick(), pollMs);
    }

    void tick();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [enabled, kbId, idsKey, pollMs, maxMs, nonce]);

  /** Vuelve a sondear un documento (p. ej. tras «Reintentar»). */
  const restart = useCallback((id: string) => {
    setDocs((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
    startedAt.current = Date.now();
    setTimedOut(false);
    setNonce((n) => n + 1);
  }, []);

  return { docs, timedOut, restart };
}
