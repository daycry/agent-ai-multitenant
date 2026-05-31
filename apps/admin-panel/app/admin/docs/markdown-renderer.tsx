"use client";

/**
 * MarkdownRenderer — safe Markdown → React for the docs visor (Plan 07
 * task_07_12).
 *
 * Pipeline: `react-markdown` with
 *   - `remark-gfm`       → tables, task lists, strikethrough, autolinks
 *   - `rehype-slug`      → stable `id`s on headings (anchor + TOC targets)
 *   - `rehype-highlight` → syntax highlighting (`hljs-*` classes; palette in
 *                          globals.css)
 *
 * Safety: raw HTML is NOT enabled (no `rehype-raw`), so any HTML embedded in
 * the markdown is rendered as inert text — no script/style/iframe injection.
 * External links are forced to `rel="noopener noreferrer"`.
 *
 * There is no Tailwind typography plugin in this app, so each element is
 * styled explicitly via the `components` map. ```mermaid fences are
 * intercepted and handed to {@link MermaidDiagram}; every other fenced block
 * keeps its highlighted code styling.
 */

import type { AnchorHTMLAttributes, ComponentPropsWithoutRef, ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import rehypeHighlight from "rehype-highlight";
import rehypeSlug from "rehype-slug";
import remarkGfm from "remark-gfm";

import { cn } from "@/lib/utils";

import { MermaidDiagram } from "./mermaid-diagram";

// Pull the raw text out of a fenced code block's children (a string, or an
// array of strings React hands us) so we can pass it to mermaid verbatim.
function nodeText(children: ReactNode): string {
  if (typeof children === "string") {
    return children;
  }
  if (Array.isArray(children)) {
    return children.map(nodeText).join("");
  }
  return "";
}

const components: Components = {
  h1: ({ children, ...props }) => (
    <h1
      {...props}
      className="text-foreground border-border mb-4 mt-6 scroll-mt-24 border-b pb-2 text-2xl font-bold first:mt-0"
    >
      {children}
    </h1>
  ),
  h2: ({ children, ...props }) => (
    <h2
      {...props}
      className="text-foreground border-border mb-3 mt-8 scroll-mt-24 border-b pb-1.5 text-xl font-semibold"
    >
      {children}
    </h2>
  ),
  h3: ({ children, ...props }) => (
    <h3 {...props} className="text-foreground mb-2 mt-6 scroll-mt-24 text-lg font-semibold">
      {children}
    </h3>
  ),
  h4: ({ children, ...props }) => (
    <h4 {...props} className="text-foreground mb-2 mt-4 scroll-mt-24 text-base font-semibold">
      {children}
    </h4>
  ),
  h5: ({ children, ...props }) => (
    <h5 {...props} className="text-foreground mb-1 mt-4 scroll-mt-24 text-sm font-semibold">
      {children}
    </h5>
  ),
  h6: ({ children, ...props }) => (
    <h6
      {...props}
      className="text-muted-foreground mb-1 mt-4 scroll-mt-24 text-sm font-semibold uppercase tracking-wide"
    >
      {children}
    </h6>
  ),
  p: ({ children, ...props }) => (
    <p {...props} className="text-foreground/90 my-3 leading-relaxed">
      {children}
    </p>
  ),
  a: ({ children, href, ...props }: AnchorHTMLAttributes<HTMLAnchorElement>) => {
    const isExternal = typeof href === "string" && /^https?:\/\//i.test(href);
    return (
      <a
        {...props}
        href={href}
        className="text-primary font-medium underline-offset-2 hover:underline"
        {...(isExternal ? { target: "_blank", rel: "noopener noreferrer" } : {})}
      >
        {children}
      </a>
    );
  },
  ul: ({ children, ...props }) => (
    <ul {...props} className="text-foreground/90 my-3 ml-6 list-disc space-y-1">
      {children}
    </ul>
  ),
  ol: ({ children, ...props }) => (
    <ol {...props} className="text-foreground/90 my-3 ml-6 list-decimal space-y-1">
      {children}
    </ol>
  ),
  li: ({ children, ...props }) => (
    <li {...props} className="leading-relaxed">
      {children}
    </li>
  ),
  blockquote: ({ children, ...props }) => (
    <blockquote
      {...props}
      className="border-primary/40 text-muted-foreground my-4 border-l-4 pl-4 italic"
    >
      {children}
    </blockquote>
  ),
  hr: (props) => <hr {...props} className="border-border my-6" />,
  table: ({ children, ...props }) => (
    <div className="my-4 overflow-x-auto">
      <table {...props} className="border-border w-full border-collapse border text-sm">
        {children}
      </table>
    </div>
  ),
  thead: ({ children, ...props }) => (
    <thead {...props} className="bg-muted/50">
      {children}
    </thead>
  ),
  th: ({ children, ...props }) => (
    <th {...props} className="border-border border px-3 py-2 text-left font-semibold">
      {children}
    </th>
  ),
  td: ({ children, ...props }) => (
    <td {...props} className="border-border border px-3 py-2 align-top">
      {children}
    </td>
  ),
  img: ({ ...props }) => (
    // eslint-disable-next-line @next/next/no-img-element
    <img {...props} alt={props.alt ?? ""} className="my-4 max-w-full rounded-md" />
  ),
  pre: ({ children, ...props }) => (
    // Block-level wrapper for fenced code; the inner <code> carries the
    // hljs token spans. Mermaid fences short-circuit in `code` below.
    <pre
      {...props}
      className="border-border bg-muted/40 my-4 overflow-x-auto rounded-lg border p-4 text-sm leading-relaxed"
    >
      {children}
    </pre>
  ),
  code: ({ className, children, ...props }: ComponentPropsWithoutRef<"code">) => {
    const match = /language-(\w+)/.exec(className ?? "");
    const language = match?.[1];

    // ```mermaid → render a diagram instead of a code block.
    if (language === "mermaid") {
      return <MermaidDiagram code={nodeText(children).replace(/\n$/, "")} />;
    }

    // Inline code: no language class and not inside a <pre>. react-markdown
    // gives fenced code a `language-*` class; inline code has none.
    const isInline = language === undefined && !(className ?? "").includes("hljs");
    if (isInline) {
      return (
        <code
          {...props}
          className="bg-muted text-foreground rounded px-1.5 py-0.5 font-mono text-[0.85em]"
        >
          {children}
        </code>
      );
    }

    return (
      <code {...props} className={cn("hljs font-mono", className)}>
        {children}
      </code>
    );
  },
};

export function MarkdownRenderer({ content }: { content: string }) {
  return (
    <div className="text-[0.95rem]" data-testid="docs-markdown">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeSlug, [rehypeHighlight, { detect: true, ignoreMissing: true }]]}
        // No `rehype-raw`: raw HTML in the source stays inert text — safe by
        // default, no injection surface.
        skipHtml
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
