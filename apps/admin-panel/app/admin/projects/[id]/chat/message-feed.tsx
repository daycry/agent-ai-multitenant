"use client";

/**
 * Feed de mensajes del chat de proyecto, troceado en prod-16 `task_prod16_08`.
 *
 * Tres piezas que sólo se usan entre ellas: la lista (`MessageFeed`), la fila de
 * un mensaje (`MessageRow`) y la de un resumen plegado (`SummaryRow`).
 */

import { useState } from "react";

import { summaryFoldedCount } from "@/lib/chat-feed";
import { renderPlanDraft } from "@/lib/plan-draft-md";
import { cn } from "@/lib/utils";

import type { Message } from "./chat-types";

interface MessageFeedProps {
  messages: Message[];
  loading: boolean;
}

export function MessageFeed({ messages, loading }: MessageFeedProps) {
  if (loading) {
    return <p className="text-muted-foreground text-sm">Cargando mensajes…</p>;
  }
  if (messages.length === 0) {
    return (
      <p className="text-muted-foreground text-sm" data-testid="chat-feed-empty">
        La conversación está vacía. Empieza a escribir para comenzar.
      </p>
    );
  }
  return (
    <ol className="space-y-3" data-testid="chat-feed">
      {messages.map((m) => (
        <li key={m.id}>
          <MessageRow message={m} />
        </li>
      ))}
    </ol>
  );
}

/**
 * A folded-history summary (task_wf_06 d).
 *
 * Summaries are `system`-authored, so without this they rendered as the tiny
 * italic centred banner used for "modo cambiado" — a multi-paragraph digest of
 * a dozen messages squeezed into a notice the eye skips. The reader could not
 * tell the team's memory had been rewritten, nor how much history sat behind it.
 *
 * Collapsed by default (it stands in for history the reader already scrolled
 * past) and expandable. The originals are NOT hidden: `GET /messages` still
 * returns them, so they remain above in the feed — folding only affects the
 * context the model reads.
 */
function SummaryRow({ message, folded }: { message: Message; folded: number }) {
  const [open, setOpen] = useState(false);
  return (
    <div
      className="rounded border border-amber-500/40 bg-amber-500/5 px-3 py-2 text-sm"
      data-testid="chat-message-summary"
      data-message-id={message.id}
    >
      <button
        type="button"
        className="flex w-full items-center justify-between gap-2 text-left"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        data-testid="chat-summary-toggle"
      >
        <span className="text-xs font-medium">
          🗂️ Resumen de {folded} {folded === 1 ? "mensaje anterior" : "mensajes anteriores"}
        </span>
        <span className="text-muted-foreground text-[10px] uppercase tracking-wide">
          {open ? "ocultar" : "ver resumen"}
        </span>
      </button>
      {open ? (
        <div className="mt-2 border-t border-amber-500/20 pt-2" data-testid="chat-summary-body">
          {renderPlanDraft(message.content)}
          <p className="text-muted-foreground mt-2 text-[10px]">
            El equipo lee este resumen en lugar de esos mensajes. Los originales siguen más arriba
            en la conversación.
          </p>
        </div>
      ) : null}
    </div>
  );
}

function MessageRow({ message }: { message: Message }) {
  const folded = summaryFoldedCount(message.attachments);
  if (message.is_summary && folded > 0) {
    return <SummaryRow message={message} folded={folded} />;
  }
  if (message.author_kind === "system") {
    return (
      <div
        className={cn(
          "border-muted-foreground/40 rounded border border-dashed",
          "bg-muted/40 text-muted-foreground px-3 py-2 text-center text-xs italic",
        )}
        data-testid="chat-system-banner"
        data-message-id={message.id}
      >
        {message.content}
      </div>
    );
  }
  const tone =
    message.author_kind === "agent"
      ? "border-indigo-500/40 bg-indigo-500/5"
      : "border-emerald-500/40 bg-emerald-500/5";
  // Agents may emit structured plan drafts as markdown (tables, lists,
  // headings). Users type plain text so we only run the renderer for
  // agent turns; user messages stay verbatim.
  const body =
    message.author_kind === "agent" ? (
      renderPlanDraft(message.content)
    ) : (
      <p className="whitespace-pre-wrap">{message.content}</p>
    );
  return (
    <div
      className={cn("rounded border px-3 py-2 text-sm", tone)}
      data-testid={`chat-message-${message.author_kind}`}
    >
      {body}
      <p className="text-muted-foreground mt-1 text-[10px] uppercase tracking-wide">
        {message.author_kind} · {message.mode}
      </p>
    </div>
  );
}
