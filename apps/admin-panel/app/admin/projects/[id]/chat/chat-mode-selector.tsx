"use client";

/**
 * Selector de modo del chat — tres píldoras en un control segmentado
 * (task_03_05, troceado en prod-16 `task_prod16_08`).
 */

import { useT } from "@/lib/i18n";
import { cn } from "@/lib/utils";

import { BUILT_IN_MODES } from "./chat-types";

interface ChatModeSelectorProps {
  current: string;
  pending: boolean;
  onChange: (next: string) => void;
}

export function ChatModeSelector({ current, pending, onChange }: ChatModeSelectorProps) {
  const t = useT("projectChat");

  return (
    <div
      role="group"
      aria-label={t("modeSelectorAria")}
      data-testid="chat-mode-selector"
      className="bg-muted inline-flex rounded-md p-0.5"
    >
      {BUILT_IN_MODES.map((mode) => {
        const active = mode.value === current;
        return (
          <button
            key={mode.value}
            type="button"
            disabled={pending}
            data-testid={`chat-mode-${mode.value}`}
            data-active={active ? "true" : "false"}
            aria-pressed={active}
            title={t(mode.descriptionKey)}
            onClick={() => {
              if (!active && !pending) onChange(mode.value);
            }}
            className={cn(
              "px-3 py-1.5 text-sm font-medium rounded transition-colors",
              active
                ? "bg-background text-foreground shadow"
                : "text-muted-foreground hover:text-foreground",
              pending && "cursor-wait opacity-60",
            )}
          >
            {t(mode.labelKey)}
          </button>
        );
      })}
    </div>
  );
}
