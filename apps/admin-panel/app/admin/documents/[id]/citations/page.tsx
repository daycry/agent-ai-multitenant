"use client";

/**
 * task_04_25 — Visualización de citas con bounding boxes en PDFs.
 *
 * El backend devuelve `{ document, chunks: [{ bbox: {page, x, y, w, h} }] }`.
 * La página renderiza cada página como un rectángulo placeholder (proporción
 * A4 vertical 1:1.414) y dibuja un `div` absolutamente posicionado por
 * cada bbox usando las coordenadas normalizadas que Docling produce.
 *
 * El rendering real del PDF con PDF.js viene en una iteración
 * posterior; este componente fija la **superficie** (citación →
 * página → bbox) sobre la que se conectará PDF.js sin tocar la UI.
 *
 * Convención de bbox (compartida con `DoclingChunk.bbox` en backend):
 *   { "page": int, "x": 0-1, "y": 0-1, "w": 0-1, "h": 0-1 }
 * donde x,y son la esquina top-left en coords normalizadas.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { FileText } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";
import { useErrorText } from "@/lib/use-error-text";

interface BBox {
  page: number;
  x: number;
  y: number;
  w: number;
  h: number;
}

interface CitationChunk {
  id: string;
  ordinal: number;
  content: string;
  bbox: BBox | null;
  metadata: Record<string, unknown>;
}

interface DocumentSummary {
  id: string;
  kb_id: string;
  title: string;
  source_filename: string;
  source_mime_type: string;
  page_count: number;
  status: string;
}

interface CitationsResponse {
  document: DocumentSummary;
  chunks: CitationChunk[];
}

// A4 portrait ratio. PDF.js will swap this in for the real ratio per
// page once it lands; the bbox math is normalised so the overlay
// stays correct.
const PAGE_ASPECT = 1.414;
const PAGE_WIDTH_PX = 480;

export default function CitationViewerPage() {
  const errorText = useErrorText();
  const params = useParams<{ id: string }>();
  const documentId = params.id;
  const [activeChunkId, setActiveChunkId] = useState<string | null>(null);
  const pageRefs = useRef<Map<number, HTMLDivElement | null>>(new Map());

  const query = useQuery({
    queryKey: ["document-citations", documentId],
    queryFn: () => apiFetch<CitationsResponse>(`/documents/${documentId}/citations`),
    refetchOnWindowFocus: false,
    enabled: Boolean(documentId),
  });

  const data = query.data;

  // Group chunks by page so each rendered page only iterates over its
  // own bboxes (instead of filtering the whole list every render).
  const chunksByPage = useMemo(() => {
    const map = new Map<number, CitationChunk[]>();
    if (!data) return map;
    for (const chunk of data.chunks) {
      if (!chunk.bbox) continue;
      const page = chunk.bbox.page;
      if (!map.has(page)) map.set(page, []);
      map.get(page)!.push(chunk);
    }
    return map;
  }, [data]);

  const pagesToRender = useMemo(() => {
    if (!data) return [] as number[];
    const fromCount = Array.from({ length: data.document.page_count }, (_, i) => i);
    const fromChunks = Array.from(chunksByPage.keys());
    const all = new Set<number>([...fromCount, ...fromChunks]);
    return Array.from(all).sort((a, b) => a - b);
  }, [data, chunksByPage]);

  // Scroll to the page hosting the active chunk.
  useEffect(() => {
    if (!activeChunkId || !data) return;
    const chunk = data.chunks.find((c) => c.id === activeChunkId);
    if (!chunk?.bbox) return;
    const target = pageRefs.current.get(chunk.bbox.page);
    target?.scrollIntoView({ behavior: "smooth", block: "start" });
  }, [activeChunkId, data]);

  return (
    <div
      className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8"
      data-testid="citations-page"
    >
      <PageHeader
        icon={<FileText className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Citas del documento"
        description="Cada cita resaltada con su bounding box en la página correspondiente."
        actions={
          data ? (
            <Badge variant="muted" data-testid="citations-doc-title">
              {data.document.title}
            </Badge>
          ) : undefined
        }
        data-testid="citations-header"
      />

      {query.isLoading ? (
        <p className="text-muted-foreground mt-6 text-sm">Cargando…</p>
      ) : query.isError ? (
        <p className="text-destructive mt-6 text-sm" data-testid="citations-error">
          {errorText(query.error)}
        </p>
      ) : !data ? null : (
        <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-[1fr_360px]">
          <CitationViewer
            pagesToRender={pagesToRender}
            chunksByPage={chunksByPage}
            activeChunkId={activeChunkId}
            registerPageRef={(page, node) => {
              if (node) pageRefs.current.set(page, node);
              else pageRefs.current.delete(page);
            }}
          />
          <CitationsSidebar
            chunks={data.chunks}
            activeChunkId={activeChunkId}
            onSelect={setActiveChunkId}
          />
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------
// Viewer: page placeholders + bbox overlays
// --------------------------------------------------------------------------
function CitationViewer({
  pagesToRender,
  chunksByPage,
  activeChunkId,
  registerPageRef,
}: {
  pagesToRender: number[];
  chunksByPage: Map<number, CitationChunk[]>;
  activeChunkId: string | null;
  registerPageRef: (page: number, node: HTMLDivElement | null) => void;
}) {
  return (
    <div className="space-y-4" data-testid="citations-viewer">
      {pagesToRender.length === 0 ? (
        <Card>
          <CardContent className="py-10 text-center">
            <p
              className="text-muted-foreground text-sm italic"
              data-testid="citations-viewer-empty"
            >
              Este documento aún no tiene páginas con bounding boxes (probable: fuente no paginada o
              ingestión sin bbox).
            </p>
          </CardContent>
        </Card>
      ) : (
        pagesToRender.map((page) => (
          <PagePlaceholder
            key={page}
            page={page}
            chunks={chunksByPage.get(page) ?? []}
            activeChunkId={activeChunkId}
            registerRef={registerPageRef}
          />
        ))
      )}
    </div>
  );
}

function PagePlaceholder({
  page,
  chunks,
  activeChunkId,
  registerRef,
}: {
  page: number;
  chunks: CitationChunk[];
  activeChunkId: string | null;
  registerRef: (page: number, node: HTMLDivElement | null) => void;
}) {
  return (
    <div
      ref={(node) => registerRef(page, node)}
      data-testid={`citations-page-${page}`}
      data-page={page}
      style={{
        width: `${PAGE_WIDTH_PX}px`,
        height: `${PAGE_WIDTH_PX * PAGE_ASPECT}px`,
      }}
      className="border-muted bg-muted/20 relative overflow-hidden rounded border"
    >
      <span className="text-muted-foreground absolute right-2 top-2 text-[10px] uppercase tracking-wide">
        Página {page + 1}
      </span>
      {chunks.map((chunk) =>
        chunk.bbox ? (
          <BBoxOverlay key={chunk.id} chunk={chunk} active={chunk.id === activeChunkId} />
        ) : null,
      )}
    </div>
  );
}

function BBoxOverlay({ chunk, active }: { chunk: CitationChunk; active: boolean }) {
  if (!chunk.bbox) return null;
  const { x, y, w, h } = chunk.bbox;
  return (
    <div
      data-testid={`citations-bbox-${chunk.id}`}
      data-active={active ? "true" : "false"}
      title={chunk.content.slice(0, 120)}
      style={{
        position: "absolute",
        left: `${x * 100}%`,
        top: `${y * 100}%`,
        width: `${w * 100}%`,
        height: `${h * 100}%`,
      }}
      className={
        "rounded transition-colors " +
        (active
          ? "border-2 border-emerald-500 bg-emerald-500/30"
          : "border border-blue-500/60 bg-blue-500/15 hover:bg-blue-500/30")
      }
    />
  );
}

// --------------------------------------------------------------------------
// Sidebar: clickable citation list
// --------------------------------------------------------------------------
function CitationsSidebar({
  chunks,
  activeChunkId,
  onSelect,
}: {
  chunks: CitationChunk[];
  activeChunkId: string | null;
  onSelect: (chunkId: string) => void;
}) {
  return (
    <aside data-testid="citations-sidebar">
      <Card>
        <CardContent className="space-y-1.5 p-2">
          {chunks.length === 0 ? (
            <p
              className="text-muted-foreground p-4 text-xs italic"
              data-testid="citations-sidebar-empty"
            >
              Sin citas para este documento.
            </p>
          ) : (
            chunks.map((chunk) => (
              <button
                key={chunk.id}
                type="button"
                onClick={() => onSelect(chunk.id)}
                data-testid={`citations-sidebar-item-${chunk.id}`}
                data-active={chunk.id === activeChunkId ? "true" : "false"}
                aria-pressed={chunk.id === activeChunkId}
                className={
                  "w-full rounded border px-3 py-2 text-left text-xs transition-colors " +
                  (chunk.id === activeChunkId
                    ? "border-emerald-500 bg-emerald-500/10"
                    : "border-muted hover:bg-muted/60")
                }
              >
                <div className="text-muted-foreground mb-0.5 flex items-center justify-between text-[10px] uppercase tracking-wide">
                  <span>#{chunk.ordinal}</span>
                  {chunk.bbox ? <span>p. {chunk.bbox.page + 1}</span> : <span>(sin bbox)</span>}
                </div>
                <p className="line-clamp-3">{chunk.content}</p>
              </button>
            ))
          )}
        </CardContent>
      </Card>
    </aside>
  );
}
