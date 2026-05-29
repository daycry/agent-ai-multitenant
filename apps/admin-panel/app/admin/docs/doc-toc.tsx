"use client";

/**
 * DocToc — auto-generated table of contents for the open doc (Plan 07
 * task_07_12).
 *
 * Built from {@link extractToc}, whose slugs match the `id`s `rehype-slug`
 * puts on the rendered headings, so each entry is an in-page `#anchor`. The
 * TOC is purely navigational: clicking an entry scrolls to the heading via the
 * native fragment behaviour (the headings carry `scroll-mt` so they clear any
 * sticky chrome). H1 is treated as the doc title and indented to the H2 base
 * so the rail reads as one consistent hierarchy.
 */

import type { TocEntry } from "@/lib/docs-api";
import { cn } from "@/lib/utils";

// Indent each level off the shallowest heading present, capped so deep docs
// don't march off the rail.
const INDENT_REM = 0.75;
const MAX_INDENT_STEPS = 3;

export function DocToc({ entries }: { entries: TocEntry[] }) {
  if (entries.length === 0) {
    return (
      <p className="text-muted-foreground px-2 py-1 text-xs italic" data-testid="docs-toc-empty">
        Sin secciones.
      </p>
    );
  }

  const minLevel = entries.reduce((min, e) => Math.min(min, e.level), 6);

  return (
    <nav aria-label="Tabla de contenidos" data-testid="docs-toc">
      <p className="text-muted-foreground mb-2 px-2 text-xs font-semibold uppercase tracking-wider">
        En esta página
      </p>
      <ul className="space-y-0.5">
        {entries.map((entry, index) => {
          const step = Math.min(entry.level - minLevel, MAX_INDENT_STEPS);
          return (
            <li key={`${entry.id}-${index}`}>
              <a
                href={`#${entry.id}`}
                className={cn(
                  "text-muted-foreground hover:text-foreground block truncate rounded px-2 py-1 text-sm transition-colors",
                  entry.level === minLevel && "text-foreground/90 font-medium",
                )}
                style={{ paddingLeft: `${0.5 + step * INDENT_REM}rem` }}
                data-testid={`docs-toc-link-${entry.id}`}
              >
                {entry.text}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
