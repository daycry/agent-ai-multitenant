"use client";

/**
 * task_04_15 — Ingestion progress for one KB document.
 *
 * Shows the document's current `status` plus a live tail of
 * `document.status` / `document.progress` events the ingestion
 * worker publishes to the Redis stream `doc:{id}`. The tail goes
 * through `/ws/documents/{id}`.
 */

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { FileScan } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { useWebSocket, wsUrl } from "@/lib/ws";

interface DocumentResponse {
  id: string;
  kb_id: string;
  title: string;
  source_filename: string;
  source_mime_type: string;
  source_size_bytes: number;
  status: string;
  error_message: string | null;
  page_count: number;
  indexed_at: string | null;
  created_at: string;
  updated_at: string;
}

interface WireEvent {
  type: string;
  occurred_at: string;
  payload: string; // JSON-encoded string in the Redis frame
}

interface ProgressEvent {
  kind: "status" | "progress";
  occurredAt: string;
  status?: string;
  stage?: string;
  detail?: string;
  errorMessage?: string;
  chunks?: number;
}

const STATUS_VARIANT: Record<string, "muted" | "warning" | "success" | "danger" | "default"> = {
  pending: "muted",
  processing: "warning",
  indexed: "success",
  // task_06_17_05: "indexado vacío" NO es verde — se procesó pero 0 chunks,
  // el agente no puede recuperar nada. Warning, no success.
  indexed_empty: "warning",
  failed: "danger",
};

const STATUS_LABEL: Record<string, string> = {
  pending: "Pendiente",
  processing: "Procesando",
  indexed: "Indexado",
  indexed_empty: "Indexado vacío",
  failed: "Fallido",
};

export default function DocumentIngestionPage() {
  const params = useParams<{ id: string }>();
  const documentId = params.id;
  const [events, setEvents] = useState<ProgressEvent[]>([]);
  const [liveStatus, setLiveStatus] = useState<string | null>(null);

  // Reutilizamos el endpoint /citations (existe en task_04_25) para sacar
  // el Document — devuelve `{ document, chunks }`. Antes este query estaba
  // disabled "para mantener WS como single source of truth", pero eso
  // dejaba la página totalmente vacía si abres /ingestion sobre un
  // documento ya indexado (no hay worker procesando → no hay eventos
  // → estado "pending" falso). Con el query habilitado, la página pinta
  // el estado real del documento aunque no haya pipeline corriendo.
  const docQuery = useQuery({
    queryKey: ["document", documentId],
    queryFn: async () => {
      const { document } = await apiFetch<{ document: DocumentResponse }>(
        `/documents/${documentId}/citations`,
      );
      return document;
    },
    refetchOnWindowFocus: false,
  });

  const url = wsUrl(`/ws/documents/${documentId}`);
  useWebSocket(url, (data) => {
    const frame = data as WireEvent;
    if (!frame || typeof frame.type !== "string") return;
    let payload: Record<string, unknown> = {};
    try {
      payload =
        typeof frame.payload === "string" ? JSON.parse(frame.payload) : (frame.payload ?? {});
    } catch {
      payload = {};
    }
    if (frame.type === "document.status") {
      const status = String(payload.status ?? "");
      setLiveStatus(status);
      setEvents((prev) => [
        ...prev,
        {
          kind: "status",
          occurredAt: frame.occurred_at,
          status,
          errorMessage:
            typeof payload.error_message === "string" ? payload.error_message : undefined,
          chunks: typeof payload.chunks === "number" ? payload.chunks : undefined,
        },
      ]);
    } else if (frame.type === "document.progress") {
      setEvents((prev) => [
        ...prev,
        {
          kind: "progress",
          occurredAt: frame.occurred_at,
          stage: typeof payload.stage === "string" ? payload.stage : "",
          detail: typeof payload.detail === "string" ? payload.detail : "",
        },
      ]);
    }
  });

  // Reset when the doc id changes mid-flight (e.g. navigation).
  useEffect(() => {
    setEvents([]);
    setLiveStatus(null);
  }, [documentId]);

  const status = liveStatus ?? docQuery.data?.status ?? "pending";
  const variant = STATUS_VARIANT[status] ?? "muted";

  return (
    <div
      className="mx-auto w-full max-w-5xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="ingestion-page"
    >
      <PageHeader
        icon={<FileScan className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Ingestión de documento"
        description="Progreso en vivo del pipeline scan → parse → embed → persist."
        actions={
          <Badge variant={variant} data-testid="ingestion-status-badge" data-status={status}>
            {STATUS_LABEL[status] ?? status}
          </Badge>
        }
        data-testid="ingestion-header"
      />

      <Card className="mt-6" data-testid="ingestion-events-card">
        <CardHeader>
          <CardTitle>Eventos</CardTitle>
        </CardHeader>
        <CardContent>
          {events.length === 0 ? (
            <p
              className="text-muted-foreground text-sm italic"
              data-testid="ingestion-events-empty"
            >
              {status === "indexed"
                ? "Documento ya indexado. No hay pipeline corriendo, así que no llegarán eventos nuevos por WebSocket."
                : status === "indexed_empty"
                  ? "Indexado vacío: el documento se procesó pero no produjo ningún fragmento (0 chunks), así que el agente no puede recuperar nada de él. Suele pasar con PDFs solo-imagen o formatos no soportados. Sube un original con texto seleccionable o reindexa."
                  : status === "failed"
                    ? `Documento en estado fallido${docQuery.data?.error_message ? `: ${docQuery.data.error_message}` : "."}`
                    : "Esperando eventos del worker de ingestión…"}
            </p>
          ) : (
            <ul className="space-y-2 text-sm" data-testid="ingestion-events-list">
              {events.map((ev, idx) => (
                <li
                  key={idx}
                  data-testid={`ingestion-event-${idx}`}
                  data-event-kind={ev.kind}
                  className="border-muted flex flex-col gap-1 rounded border px-3 py-2"
                >
                  <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide">
                    <Badge variant={ev.kind === "status" ? "default" : "muted"}>{ev.kind}</Badge>
                    <span className="text-muted-foreground">{ev.occurredAt}</span>
                  </div>
                  {ev.kind === "status" ? (
                    <p>
                      Estado: <span className="font-mono">{ev.status}</span>
                      {ev.chunks !== undefined ? (
                        <span className="text-muted-foreground"> · {ev.chunks} chunks</span>
                      ) : null}
                      {ev.errorMessage ? (
                        <span className="text-destructive ml-2">{ev.errorMessage}</span>
                      ) : null}
                    </p>
                  ) : (
                    <p>
                      <span className="font-semibold">{ev.stage}</span>
                      {ev.detail ? (
                        <span className="text-muted-foreground"> · {ev.detail}</span>
                      ) : null}
                    </p>
                  )}
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
