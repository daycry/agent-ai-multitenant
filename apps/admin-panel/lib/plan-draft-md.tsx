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
 * preserved. We never use dangerouslySetInnerHTML, so inline text is
 * escaped by React's default text rendering. The ONE active sink is the
 * `[text](url)` link: agent content is LLM output (influenceable via the
 * composer / @-mentions), NOT trusted, so link hrefs are validated
 * against a scheme allowlist (`isSafeHref`) — `javascript:`/`data:` links
 * degrade to plain text instead of becoming a clickable XSS vector.
 */

import React from "react";

interface RenderedBlock {
  key: string;
  node: React.ReactNode;
}

/**
 * True when `raw` is a link href safe to render as a clickable `<a>`.
 *
 * Allowlist: `http(s):`, `mailto:`, and scheme-less (relative) URLs.
 * Everything with another scheme — `javascript:`, `data:`, `vbscript:`,
 * `file:`, … — is rejected. Browsers ignore leading/embedded whitespace
 * and control chars when resolving a scheme, so we drop those first
 * (defeats `java\tscript:` / ` javascript:` / `JavaScript:`).
 */
export function isSafeHref(raw: string): boolean {
  let stripped = "";
  for (const ch of raw) {
    const code = ch.charCodeAt(0);
    if (code > 0x20 && code !== 0x7f) stripped += ch; // drop space + ASCII control chars
  }
  const scheme = /^([a-z][a-z0-9+.-]*):/.exec(stripped.toLowerCase());
  if (!scheme) return true; // relative / anchor / scheme-less → safe
  return scheme[1] === "http" || scheme[1] === "https" || scheme[1] === "mailto";
}

// Single regex that captures the five inline patterns in priority
// order. The order matters: `**bold**` must beat the `*italic*` rule
// (otherwise `**x**` reads as italic-empty-italic) and inline `code`
// is matched first so its content escapes the other patterns.
const INLINE_RE =
  /(`[^`\n]+`)|(\[[^\]\n]+\]\([^)\n]+\))|(\*\*[^*\n]+\*\*)|(\*[^*\n]+\*|_[^_\n]+_)|(~~[^~\n]+~~)/g;

const TABLE_HEADER_RE = /^\|.*\|\s*$/;
// `-` placed last in the character class so it's a literal hyphen
// (writing `:-|` would have been parsed as the ASCII range `:`→`|`
// which excludes the actual `-` character).
const TABLE_DIVIDER_RE = /^\|[\s:|-]+\|\s*$/;
const ORDERED_LIST_RE = /^\d+\.\s+/;
const HEADING_RE = /^(#{2,4})\s+(.*)$/;

function inline(text: string): React.ReactNode {
  const out: React.ReactNode[] = [];
  let cursor = 0;
  let match: RegExpExecArray | null;
  INLINE_RE.lastIndex = 0;
  let key = 0;
  while ((match = INLINE_RE.exec(text)) !== null) {
    if (match.index > cursor) {
      out.push(<React.Fragment key={key++}>{text.slice(cursor, match.index)}</React.Fragment>);
    }
    const token = match[0];
    if (match[1]) {
      // `inline code`
      out.push(
        <code key={key++} className="bg-muted rounded px-1 py-0.5 font-mono text-[0.85em]">
          {token.slice(1, -1)}
        </code>,
      );
    } else if (match[2]) {
      // [text](url)
      const labelEnd = token.indexOf("](");
      const label = token.slice(1, labelEnd);
      const href = token.slice(labelEnd + 2, -1);
      // Unsafe schemes (javascript:/data:/…) degrade to plain text — never a clickable link.
      out.push(
        isSafeHref(href) ? (
          <a
            key={key++}
            href={href}
            target="_blank"
            rel="noopener noreferrer"
            className="text-primary underline-offset-2 hover:underline"
          >
            {label}
          </a>
        ) : (
          <React.Fragment key={key++}>{label}</React.Fragment>
        ),
      );
    } else if (match[3]) {
      // **bold**
      out.push(<strong key={key++}>{token.slice(2, -2)}</strong>);
    } else if (match[4]) {
      // *italic* or _italic_
      out.push(<em key={key++}>{token.slice(1, -1)}</em>);
    } else if (match[5]) {
      // ~~strikethrough~~
      out.push(<s key={key++}>{token.slice(2, -2)}</s>);
    }
    cursor = match.index + token.length;
  }
  if (cursor < text.length) {
    out.push(<React.Fragment key={key++}>{text.slice(cursor)}</React.Fragment>);
  }
  return out;
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
    // A wide table (many columns / long cells) must scroll WITHIN its own box
    // instead of pushing the page width and creating a page-level horizontal
    // scrollbar. Same pattern the plan-detail DAG/Gantt use.
    node: (
      <div className="overflow-x-auto">
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
      </div>
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
