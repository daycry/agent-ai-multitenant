/**
 * Citas de memoria en el informe de un run (`task_mem_02`).
 *
 * El runtime le recomienda al agente citar `[mem:<8 primeros caracteres del id>]`
 * cuando una memoria condicionó una decisión. Este módulo parte un texto en
 * trozos de texto y citas; resolver cada cita contra `GET /memories?id_prefix=`
 * es cosa del componente (`components/executions/memory-citations.tsx`).
 */

export type CitationPart =
  { type: "text"; value: string } | { type: "cite"; short: string; raw: string };

/** `[mem:` + entre 6 y 36 caracteres hex/guion (id corto o completo) + `]`. */
const CITE_RE = /\[mem:([0-9a-f-]{6,36})\]/gi;

export function splitCitations(text: string): CitationPart[] {
  const parts: CitationPart[] = [];
  let last = 0;
  for (const match of text.matchAll(CITE_RE)) {
    const index = match.index ?? 0;
    if (index > last) parts.push({ type: "text", value: text.slice(last, index) });
    parts.push({ type: "cite", short: match[1].toLowerCase(), raw: match[0] });
    last = index + match[0].length;
  }
  if (last < text.length) parts.push({ type: "text", value: text.slice(last) });
  return parts;
}
