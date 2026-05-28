"use client";

import { useId, useState } from "react";
import { Eye, Pencil } from "lucide-react";

import { cn } from "@/lib/utils";
import { renderPlanDraft } from "@/lib/plan-draft-md";

type Mode = "edit" | "preview";

interface MarkdownTextareaProps {
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  rows?: number;
  disabled?: boolean;
  className?: string;
  "data-testid"?: string;
  /**
   * Optional hint displayed under the editor when in edit mode.
   * Defaults to a short summary of supported syntax.
   */
  hint?: React.ReactNode;
}

const DEFAULT_HINT = (
  <>
    Soporta markdown: <code>**bold**</code>, <code>*italic*</code>, <code>`code`</code>,{" "}
    <code>[link](url)</code>, listas, tablas y headings <code>##</code>.
  </>
);

/**
 * Tabbed markdown editor. Edit-tab is a plain textarea; preview-tab
 * renders via `renderPlanDraft` (our zero-dep markdown renderer).
 * Behaves as a controlled input — never owns state.
 */
export function MarkdownTextarea({
  value,
  onChange,
  placeholder,
  rows = 4,
  disabled,
  className,
  hint = DEFAULT_HINT,
  ...props
}: MarkdownTextareaProps) {
  const [mode, setMode] = useState<Mode>("edit");
  const id = useId();
  const testid = props["data-testid"] ?? "markdown-textarea";

  return (
    <div className={cn("flex flex-col gap-1.5", className)} data-testid={testid}>
      <div
        className="bg-muted inline-flex w-fit rounded-md p-0.5"
        role="tablist"
        aria-label="Modo del editor markdown"
      >
        <TabButton
          active={mode === "edit"}
          onClick={() => setMode("edit")}
          icon={<Pencil className="h-3 w-3" />}
          label="Editar"
          testid={`${testid}-tab-edit`}
        />
        <TabButton
          active={mode === "preview"}
          onClick={() => setMode("preview")}
          icon={<Eye className="h-3 w-3" />}
          label="Vista previa"
          testid={`${testid}-tab-preview`}
        />
      </div>

      {mode === "edit" ? (
        <>
          <textarea
            id={id}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={placeholder}
            rows={rows}
            disabled={disabled}
            data-testid={`${testid}-edit`}
            className={cn(
              "border-input bg-background focus-visible:ring-ring rounded-md border px-3 py-2 text-sm",
              "font-mono leading-relaxed focus-visible:outline-none focus-visible:ring-2",
              disabled && "cursor-not-allowed opacity-60",
            )}
          />
          {hint && <p className="text-muted-foreground text-[11px]">{hint}</p>}
        </>
      ) : (
        <div
          className="border-input bg-muted/30 min-h-[6rem] rounded-md border px-3 py-2 text-sm"
          data-testid={`${testid}-preview`}
        >
          {value.trim().length === 0 ? (
            <p className="text-muted-foreground/60 text-xs italic">
              Sin contenido para previsualizar.
            </p>
          ) : (
            renderPlanDraft(value)
          )}
        </div>
      )}
    </div>
  );
}

function TabButton({
  active,
  onClick,
  icon,
  label,
  testid,
}: {
  active: boolean;
  onClick: () => void;
  icon: React.ReactNode;
  label: string;
  testid: string;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={active}
      onClick={onClick}
      data-testid={testid}
      data-active={active ? "true" : "false"}
      className={cn(
        "inline-flex items-center gap-1 rounded px-2 py-0.5 text-[11px] font-medium transition-colors",
        active
          ? "bg-background text-foreground shadow"
          : "text-muted-foreground hover:text-foreground",
      )}
    >
      {icon}
      <span>{label}</span>
    </button>
  );
}
