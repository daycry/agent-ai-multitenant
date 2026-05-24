/**
 * Tiny markdown renderer for plan drafts in the chat (Plan 03 task_03_11).
 *
 * The planning sub-graph emits structured plan drafts as markdown
 * (tables for tasks, ordered lists for phases, bold for keywords).
 * Rather than pulling in `react-markdown` + `remark-gfm` (~200 KB
 * across the wire) just for this, we render the four patterns we
 * actually need:
 *
 *   - `## heading`
 *   - GFM tables (`| col1 | col2 |\n|---|---|\n| ... | ... |`)
 *   - unordered lists (`- item` / `* item`)
 *   - ordered lists (`1. item`)
 *   - paragraphs with inline `**bold**`
 *
 * Anything else falls through as a plain paragraph with newlines
 * preserved. The chat feed already trusts agent content (it goes
 * through the backend, never user-uploaded HTML), so we don't need
 * an HTML sanitiser here — but we still escape inline text via
 * React's default text rendering (we never use dangerouslySetInnerHTML).
 */

import React from "react";

interface RenderedBlock {
  key: string;
  node: React.ReactNode;
}

const TABLE_HEADER_RE = /^\|.*\|\s*$/;
// `-` placed last in the character class so it's a literal hyphen
// (writing `:-|` would have been parsed as the ASCII range `:`→`|`
// which excludes the actual `-` character).
const TABLE_DIVIDER_RE = /^\|[\s:|-]+\|\s*$/;
const ORDERED_LIST_RE = /^\d+\.\s+/;
const HEADING_RE = /^(#{2,4})\s+(.*)$/;

function inline(text: string): React.ReactNode {
  // Split on **bold** and rebuild as a fragment.
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, idx) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={idx}>{part.slice(2, -2)}</strong>;
    }
    return <React.Fragment key={idx}>{part}</React.Fragment>;
  });
}

function renderTable(lines: string[], key: string): RenderedBlock | null {
  if (lines.length < 2) return null;
  if (!TABLE_HEADER_RE.test(lines[0])) return null;
  if (!TABLE_DIVIDER_RE.test(lines[1])) return null;

  const cells = (line: string) =>
    line
      .replace(/^\|/, "")
      .replace(/\|\s*$/, "")
      .split("|")
      .map((c) => c.trim());

  const headers = cells(lines[0]);
  const rows = lines.slice(2).map(cells);

  return {
    key,
    node: (
      <table
        className="border-muted my-2 w-full border-collapse text-xs"
        data-testid="plan-draft-table"
      >
        <thead>
          <tr>
            {headers.map((h, i) => (
              <th
                key={i}
                className="border-muted bg-muted/50 border px-2 py-1 text-left font-semibold"
              >
                {inline(h)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, c) => (
                <td key={c} className="border-muted border px-2 py-1 align-top">
                  {inline(cell)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    ),
  };
}

function renderList(lines: string[], key: string, ordered: boolean): RenderedBlock {
  const items = lines.map((line) =>
    ordered ? line.replace(ORDERED_LIST_RE, "") : line.replace(/^[-*]\s+/, ""),
  );
  const className = ordered
    ? "list-decimal pl-5 my-1 space-y-0.5"
    : "list-disc pl-5 my-1 space-y-0.5";
  const dataTestId = ordered ? "plan-draft-ol" : "plan-draft-ul";
  const ListTag = ordered ? "ol" : "ul";
  return {
    key,
    node: (
      <ListTag className={className} data-testid={dataTestId}>
        {items.map((item, i) => (
          <li key={i}>{inline(item)}</li>
        ))}
      </ListTag>
    ),
  };
}

function renderHeading(line: string, key: string): RenderedBlock | null {
  const match = HEADING_RE.exec(line);
  if (!match) return null;
  const level = match[1].length; // 2..4
  const text = match[2];
  const sizeCls = level === 2 ? "text-base" : level === 3 ? "text-sm" : "text-xs";
  return {
    key,
    node: (
      <h3
        className={`mt-2 mb-1 font-semibold ${sizeCls}`}
        data-testid="plan-draft-heading"
        data-level={level}
      >
        {inline(text)}
      </h3>
    ),
  };
}

function renderParagraph(lines: string[], key: string): RenderedBlock {
  return {
    key,
    node: (
      <p className="my-1 whitespace-pre-wrap">
        {lines.map((l, i) => (
          <React.Fragment key={i}>
            {i > 0 && <br />}
            {inline(l)}
          </React.Fragment>
        ))}
      </p>
    ),
  };
}

/**
 * Render a markdown-ish string as React nodes.
 *
 * The strategy: split into blocks by blank lines, then route each
 * block to the right renderer based on its first line. Unknown blocks
 * become paragraphs. Tables that don't have a divider line (`|---|`)
 * fall back to a plain paragraph instead of crashing the chat.
 */
export function renderPlanDraft(content: string): React.ReactNode {
  if (!content) return null;
  const blocks = content.split(/\n\s*\n/);
  const rendered: RenderedBlock[] = [];

  // A single "block" (between blank lines) may start with one or more
  // heading lines followed by a structured chunk (table, list, paragraph).
  // We peel headings off the top and let the remainder be classified.
  const processLines = (lines: string[], baseKey: string) => {
    let cursor = 0;
    let subKey = 0;
    while (cursor < lines.length && HEADING_RE.test(lines[cursor])) {
      const heading = renderHeading(lines[cursor], `${baseKey}-h${subKey++}`);
      if (heading) rendered.push(heading);
      cursor += 1;
    }
    const remaining = lines.slice(cursor);
    if (remaining.length === 0) return;

    const first = remaining[0];
    const restKey = `${baseKey}-r${subKey}`;

    if (TABLE_HEADER_RE.test(first)) {
      const table = renderTable(remaining, restKey);
      if (table) {
        rendered.push(table);
        return;
      }
    }

    if (/^[-*]\s+/.test(first)) {
      rendered.push(renderList(remaining, restKey, false));
      return;
    }

    if (ORDERED_LIST_RE.test(first)) {
      rendered.push(renderList(remaining, restKey, true));
      return;
    }

    rendered.push(renderParagraph(remaining, restKey));
  };

  blocks.forEach((rawBlock, blockIdx) => {
    const lines = rawBlock.split("\n").filter((l) => l.length > 0);
    if (lines.length === 0) return;
    processLines(lines, `block-${blockIdx}`);
  });

  return (
    <div className="plan-draft space-y-1" data-testid="plan-draft">
      {rendered.map((b) => (
        <React.Fragment key={b.key}>{b.node}</React.Fragment>
      ))}
    </div>
  );
}
