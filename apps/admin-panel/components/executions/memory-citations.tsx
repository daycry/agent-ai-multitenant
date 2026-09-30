"use client";

/**
 * Texto de un run con sus citas de memoria resueltas (`task_mem_02`).
 *
 * El agente cita `[mem:<id corto>]` en su informe cuando una memoria condicionó
 * una decisión. Aquí cada cita se resuelve contra `GET /memories?id_prefix=`
 * (mismo aislamiento que el listado: tenant + visibilidad de la persona) y se
 * pinta como un chip que despliega la memoria citada. Si no se resuelve (borrada,
 * de otro ámbito, id inventado) se enseña como «desconocida», sin adornarla.
 */

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";

import type { MemoryResponse } from "@/components/memories/types";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { splitCitations } from "@/lib/memory/citations";

function CitationChip({ short }: { short: string }) {
  const t = useT("memoriesProvenance");
  const tc = useT("memoriesCard");
  const [open, setOpen] = useState(false);
  const query = useQuery({
    queryKey: ["memory-citation", short],
    queryFn: () => apiFetch<MemoryResponse[]>(`/memories?id_prefix=${short}&limit=1`),
    staleTime: 60_000,
  });
  const memory = query.data?.[0];
  const unresolved = query.isSuccess && !memory;

  return (
    <span className="inline" data-testid={`memory-citation-${short}`}>
      <button
        type="button"
        onClick={() => memory && setOpen((v) => !v)}
        disabled={!memory}
        aria-expanded={memory ? open : undefined}
        title={memory ? memory.content.slice(0, 120) : unresolved ? t("citationUnknown") : ""}
        className={
          unresolved
            ? "text-muted-foreground rounded border border-dashed px-1 font-mono text-xs"
            : "bg-primary/10 text-primary rounded border px-1 font-mono text-xs"
        }
      >
        {`mem:${short}`}
        {unresolved ? ` (${t("citationUnknown")})` : ""}
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
  return (
    <>
      {splitCitations(text).map((part, i) =>
        part.type === "text" ? (
          <span key={i}>{part.value}</span>
        ) : (
          <CitationChip key={i} short={part.short} />
        ),
      )}
    </>
  );
}
