"use client";

/**
 * Sondeo del lote de documentos que acaba de subir el asistente (`task_ui_21`).
 *
 * Pide `GET …/documents/{id}` sólo de los ids del lote que aún no son terminales
 * (no la página entera del listado, que se queda corta con lotes grandes). Acaba
 * cuando todos son terminales — `indexed_empty` incluido —, cuando un documento
 * responde 404 (borrado: terminal, `null`), cuando el diálogo se cierra
 * (`enabled` = false) o al pasar `maxMs`, con `timedOut` para avisar de que sigue
 * indexando en segundo plano.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError, apiFetch } from "@/lib/api";

import { isTerminal, type KbDocument } from "./kb-types";

export const POLL_MS = 1500;
export const MAX_POLL_MS = 5 * 60 * 1000;
const CHUNK = 10;

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

    async function fetchOne(id: string): Promise<[string, KbDocument | null | undefined]> {
      try {
        return [id, await apiFetch<KbDocument>(`/knowledge-bases/${kbId}/documents/${id}`)];
      } catch (err) {
        if (err instanceof ApiError && err.status === 404) return [id, null];
        return [id, undefined]; // error transitorio: se reintenta en el siguiente ciclo
      }
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
      const fresh: Record<string, KbDocument | null> = {};
      for (let i = 0; i < pending.length; i += CHUNK) {
        const entries = await Promise.all(pending.slice(i, i + CHUNK).map(fetchOne));
        for (const [id, value] of entries) if (value !== undefined) fresh[id] = value;
      }
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
