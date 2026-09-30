"use client";

/**
 * Texto de un run con sus citas de memoria resueltas (`task_mem_02`).
 *
 * El agente cita `[mem:<id corto>]` en su informe cuando una memoria condicionó
 * una decisión. Todas las citas del informe se resuelven en UNA petición contra
 * `GET /memories/resolve?prefixes=` (mismo aislamiento que el listado: tenant +
 * visibilidad de la persona) y cada una se pinta como un chip que despliega la
 * memoria citada. Si no se resuelve (borrada, de otro ámbito, id inventado) se
 * enseña como «desconocida»; si el prefijo lo comparten varias memorias, como
 * «ambigua» — nunca se adivina cuál.
 */

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import type { MemoryResponse } from "@/components/memories/types";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { splitCitations } from "@/lib/memory/citations";

/** Lo que devuelve `GET /memories/resolve` por prefijo. */
interface ResolvedCitation {
  prefix: string;
  status: "ok" | "ambiguous" | "unknown";
  memory?: MemoryResponse | null;
}

/** El endpoint exige al menos 8 caracteres: un prefijo más corto no se envía (y no se adivina). */
const MIN_PREFIX = 8;

function CitationChip({
  short,
  resolution,
  settled,
}: {
  short: string;
  resolution: ResolvedCitation | undefined;
  settled: boolean;
}) {
  const t = useT("memoriesProvenance");
  const tc = useT("memoriesCard");
  const [open, setOpen] = useState(false);
  const memory = resolution?.status === "ok" ? (resolution.memory ?? undefined) : undefined;
  const ambiguous = resolution?.status === "ambiguous";
  const unresolved = settled && !memory && !ambiguous;
  const unresolvedLabel = ambiguous ? t("citationAmbiguous") : t("citationUnknown");

  return (
    <span className="inline" data-testid={`memory-citation-${short}`}>
      <button
        type="button"
        onClick={() => memory && setOpen((v) => !v)}
        disabled={!memory}
        aria-expanded={memory ? open : undefined}
        title={
          memory ? memory.content.slice(0, 120) : unresolved || ambiguous ? unresolvedLabel : ""
        }
        className={
          unresolved || ambiguous
            ? "text-muted-foreground rounded border border-dashed px-1 font-mono text-xs"
            : "bg-primary/10 text-primary rounded border px-1 font-mono text-xs"
        }
      >
        {`mem:${short}`}
        {unresolved || ambiguous ? ` (${unresolvedLabel})` : ""}
      </button>
      {memory && open && (
        <span
          className="bg-muted mt-1 block whitespace-pre-wrap rounded border p-2 text-xs"
          data-testid={`memory-citation-body-${short}`}
        >
          {memory.content}
          <Link
            href={`/admin/memories/${memory.id}`}
            className="text-primary mt-1 block underline"
            data-testid={`memory-citation-open-${short}`}
          >
            {tc("openCard")}
          </Link>
        </span>
      )}
    </span>
  );
}

/** El texto con cada `[mem:…]` sustituido por su chip. Sin citas, es el texto tal cual. */
export function CitedText({ text }: { text: string }) {
  const parts = splitCitations(text);
  const prefixes = [
    ...new Set(
      parts.flatMap((p) => (p.type === "cite" && p.short.length >= MIN_PREFIX ? [p.short] : [])),
    ),
  ];
  const query = useQuery({
    queryKey: ["memory-citations", prefixes.join(",")],
    queryFn: () => apiFetch<ResolvedCitation[]>(`/memories/resolve?prefixes=${prefixes.join(",")}`),
    enabled: prefixes.length > 0,
    staleTime: 60_000,
  });
  const byPrefix = new Map((query.data ?? []).map((r) => [r.prefix, r]));
  // Sin respuesta aún no se pinta «desconocida»; un prefijo demasiado corto ya no la tendrá nunca.
  const settled = (short: string) => query.isSuccess || short.length < MIN_PREFIX;
  return (
    <>
      {parts.map((part, i) =>
        part.type === "text" ? (
          <span key={i}>{part.value}</span>
        ) : (
          <CitationChip
            key={i}
            short={part.short}
            resolution={byPrefix.get(part.short)}
            settled={settled(part.short)}
          />
        ),
      )}
    </>
  );
}
