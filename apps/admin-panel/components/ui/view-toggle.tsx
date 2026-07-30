"use client";

import { KanbanSquare, List } from "lucide-react";

import { cn } from "@/lib/utils";

export type ViewMode = "list" | "kanban";

interface ViewToggleProps {
  value: ViewMode;
  onChange: (next: ViewMode) => void;
  className?: string;
  "data-testid"?: string;
}

export function ViewToggle({ value, onChange, className, ...props }: ViewToggleProps) {
  return (
    <div
      className={cn("bg-muted inline-flex rounded-md p-1", className)}
      role="tablist"
      aria-label="Cambiar vista"
      data-testid={props["data-testid"] ?? "view-toggle"}
    >
      <ToggleButton
        active={value === "list"}
        onClick={() => onChange("list")}
        icon={<List className="h-3.5 w-3.5" />}
        label="Lista"
        testid="view-toggle-list"
      />
      <ToggleButton
        active={value === "kanban"}
        onClick={() => onChange("kanban")}
        icon={<KanbanSquare className="h-3.5 w-3.5" />}
        label="Kanban"
        testid="view-toggle-kanban"
      />
    </div>
  );
}

function ToggleButton({
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
        "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors",
        // human_ui_01: el foco debe VERSE al tabular. Mismo anillo (token `ring`
        // + offset sobre `background`) que Input/Select/Tabs, para que el
        // recorrido por teclado se lea igual en todo el panel.
        "focus-visible:ring-ring focus-visible:ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2",
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
