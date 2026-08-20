"use client";

import { useId, useState } from "react";
import { Eye, Pencil } from "lucide-react";

import { cn } from "@/lib/utils";
import { useT } from "@/lib/i18n";
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
   *
   * El default NO puede ser una constante de módulo (prod-16 `task_prod16_03`):
   * el texto depende del idioma activo, así que se compone dentro del
   * componente. Antes era un `const DEFAULT_HINT` con castellano fijo.
   */
  hint?: React.ReactNode;
}

/**
 * Tabbed markdown editor. Edit-tab is a plain textarea; preview-tab
 * renders via `renderPlanDraft` (our zero-dep markdown renderer).
 * Behaves as a controlled input — never owns state.
 *
 * Lo montan 22 pantallas, varias ya migradas al diccionario: su barra de
 * pestañas en castellano era la mitad castellana de un diálogo por lo demás
 * inglés, y ninguna de las dos guardas de `check-i18n` la contaba como deuda
 * de esas pantallas (miran ficheros, no pantallas).
 */
export function MarkdownTextarea({
  value,
  onChange,
  placeholder,
  rows = 4,
  disabled,
  className,
  hint,
  ...props
}: MarkdownTextareaProps) {
  const [mode, setMode] = useState<Mode>("edit");
  const t = useT("markdownTextarea");
  const id = useId();
  const testid = props["data-testid"] ?? "markdown-textarea";

  const effectiveHint = hint ?? (
    <>
      {t("hintLead")} <code>**bold**</code>, <code>*italic*</code>, <code>`code`</code>,{" "}
      <code>[link](url)</code>
      {t("hintTail")}
      <code>##</code>.
    </>
  );

  return (
    <div className={cn("flex flex-col gap-1.5", className)} data-testid={testid}>
      <div
        className="bg-muted inline-flex w-fit rounded-md p-0.5"
        role="tablist"
        aria-label={t("tablistLabel")}
      >
        <TabButton
          active={mode === "edit"}
          onClick={() => setMode("edit")}
          icon={<Pencil className="h-3 w-3" />}
          label={t("tabEdit")}
          testid={`${testid}-tab-edit`}
        />
        <TabButton
          active={mode === "preview"}
          onClick={() => setMode("preview")}
          icon={<Eye className="h-3 w-3" />}
          label={t("tabPreview")}
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
          {effectiveHint && <p className="text-muted-foreground text-[11px]">{effectiveHint}</p>}
        </>
      ) : (
        <div
          className="border-input bg-muted/30 min-h-[6rem] rounded-md border px-3 py-2 text-sm"
          data-testid={`${testid}-preview`}
        >
          {value.trim().length === 0 ? (
            <p className="text-muted-foreground/60 text-xs italic">{t("emptyPreview")}</p>
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
