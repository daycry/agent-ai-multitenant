"use client";

/**
 * DocDiffRenderer — a lightweight, dependency-free unified-diff renderer
 * (Plan 07 task_07_16).
 *
 * The backend (`GET /projects/{id}/docs/diff`) already classifies each line of
 * `git diff` into {@link DocDiffLine} rows (`context` / `added` / `removed` /
 * `hunk`), so we only re-apply styling per `kind` — no diffing happens client
 * side and we pull in no heavy diff dependency. Added lines get a green gutter
 * marker (`+`), removed lines a red one (`-`), hunk headers a neutral band, and
 * context lines a muted background.
 *
 * The component is purely presentational; the surrounding {@link DocDiffView}
 * owns fetching + the loading / error / empty states.
 */

import type { DocDiffLine, DocDiffLineKind } from "@/lib/docs-api";

/** Per-kind row styling. Colors come from the semantic diff/soft tokens. */
const ROW_CLASS: Record<DocDiffLineKind, string> = {
  added: "bg-success-soft/40 text-foreground",
  removed: "bg-danger-soft/40 text-foreground",
  hunk: "bg-muted text-muted-foreground select-none",
  context: "text-muted-foreground",
};

/** Single-char gutter marker per kind. */
const MARKER: Record<DocDiffLineKind, string> = {
  added: "+",
  removed: "-",
  hunk: "",
  context: " ",
};

interface DocDiffRendererProps {
  lines: DocDiffLine[];
}

export function DocDiffRenderer({ lines }: DocDiffRendererProps) {
  return (
    <div
      className="overflow-x-auto rounded-lg border font-mono text-xs leading-relaxed"
      data-testid="docs-diff-renderer"
    >
      <table className="w-full border-collapse">
        <tbody>
          {lines.map((line, index) => (
            <tr
              key={index}
              className={ROW_CLASS[line.kind]}
              data-testid={`docs-diff-line-${line.kind}`}
              data-diff-kind={line.kind}
            >
              <td
                className="text-muted-foreground/70 w-8 select-none border-r px-2 text-center align-top"
                aria-hidden="true"
              >
                {MARKER[line.kind]}
              </td>
              <td className="w-full whitespace-pre-wrap break-words px-3 py-px align-top">
                {line.content.length > 0 ? line.content : " "}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
