"use client";

/**
 * Composer con autocompletado de @-menciones (task_03_12), troceado en prod-16
 * `task_prod16_08`.
 */

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { renderPlanDraft } from "@/lib/plan-draft-md";
import { cn } from "@/lib/utils";

interface ChatComposerProps {
  disabled: boolean;
  /** Roles del equipo REAL del proyecto (`GET /projects/{id}/planning-roles`).
   * Vacío mientras carga o si el proyecto no tiene equipo: sin sugerencias es
   * preferible a sugerir a alguien que no va a contestar. */
  roles: readonly string[];
  onSubmit: (content: string) => void;
}

export function ChatComposer({ disabled, roles, onSubmit }: ChatComposerProps) {
  const [value, setValue] = useState("");
  // Markdown preview toggle. The edit view keeps the raw <textarea> so @-mention
  // tracking (cursor/onChange) stays intact; preview renders the same markdown
  // renderer the chat messages use.
  const [preview, setPreview] = useState(false);
  // The @-trigger is open when the cursor sits in the middle of a
  // partial mention token ("@" followed by 0+ word-chars, no space).
  const mention = parsePendingMention(value);

  const suggestions = mention ? roles.filter((r) => r.startsWith(mention.query.toLowerCase())) : [];

  // Take the text+mention to operate on as explicit args rather than
  // relying on the enclosing closure (frontend-admin-panel-4): the click
  // handler then always splices into exactly the text that produced the
  // visible suggestion list, with no chance of reading a stale `value`.
  const pickMention = (role: string, currentValue: string, target = mention) => {
    if (!target) return;
    const before = currentValue.slice(0, target.start);
    const after = currentValue.slice(target.start + target.length);
    setValue(`${before}@${role} ${after}`);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = value.trim();
    if (!trimmed || disabled) return;
    onSubmit(trimmed);
    setValue("");
  };

  return (
    <form className="mt-4 relative" onSubmit={handleSubmit} data-testid="chat-composer">
      <div className="bg-muted mb-1.5 inline-flex w-fit rounded-md p-0.5" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={!preview}
          onClick={() => setPreview(false)}
          data-testid="chat-input-tab-edit"
          className={cn(
            "rounded px-2 py-0.5 text-[11px] font-medium transition-colors",
            !preview ? "bg-background text-foreground shadow" : "text-muted-foreground",
          )}
        >
          Editar
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={preview}
          onClick={() => setPreview(true)}
          data-testid="chat-input-tab-preview"
          className={cn(
            "rounded px-2 py-0.5 text-[11px] font-medium transition-colors",
            preview ? "bg-background text-foreground shadow" : "text-muted-foreground",
          )}
        >
          Vista previa
        </button>
      </div>
      {preview ? (
        <div
          data-testid="chat-input-preview"
          className="bg-muted/30 min-h-[5.5rem] w-full rounded border px-3 py-2 text-sm"
        >
          {value.trim().length === 0 ? (
            <p className="text-muted-foreground/60 text-xs italic">
              Sin contenido para previsualizar.
            </p>
          ) : (
            renderPlanDraft(value)
          )}
        </div>
      ) : (
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder="Escribe un mensaje. Usa @ para mencionar a un agente. Soporta markdown."
          rows={3}
          disabled={disabled}
          data-testid="chat-input"
          className={cn(
            "w-full resize-none rounded border px-3 py-2 text-sm",
            "bg-background focus:outline-none focus:ring-2 focus:ring-indigo-500/40",
          )}
        />
      )}
      {suggestions.length > 0 ? (
        <ul
          data-testid="mention-suggestions"
          className={cn(
            "bg-popover border-muted absolute left-0 z-10 -mt-2 rounded border",
            "max-h-48 w-64 overflow-y-auto py-1 shadow-md",
          )}
        >
          {suggestions.map((role) => (
            <li key={role}>
              <button
                type="button"
                data-testid={`mention-suggestion-${role}`}
                onClick={() => pickMention(role, value, mention)}
                className="hover:bg-muted w-full px-3 py-1 text-left text-sm"
              >
                @{role}
              </button>
            </li>
          ))}
        </ul>
      ) : null}
      <div className="mt-2 flex justify-end">
        <Button
          type="submit"
          disabled={disabled || value.trim().length === 0}
          data-testid="chat-send"
        >
          Enviar
        </Button>
      </div>
    </form>
  );
}

/**
 * Returns metadata about the @-mention the user is currently typing
 * (the token immediately before the cursor / value end), or null if
 * there isn't one.
 */
export function parsePendingMention(
  value: string,
): { start: number; length: number; query: string } | null {
  // Match `@word` at the end of the buffer (we don't track caret
  // position here — a simple end-of-text match is good enough for the
  // common "type @ and pick" flow).
  const match = /@(\w*)$/.exec(value);
  if (!match) return null;
  return {
    start: match.index,
    length: match[0].length,
    query: match[1],
  };
}
