"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * Minimal controlled-via-context tabs primitive.
 * No external dependency on @radix-ui/react-tabs; if we need keyboard
 * roving / aria-orientation later, swap to Radix without changing the
 * call sites.
 */

type TabsContextValue = {
  value: string;
  setValue: (next: string) => void;
};

const TabsContext = React.createContext<TabsContextValue | null>(null);

function useTabs(): TabsContextValue {
  const ctx = React.useContext(TabsContext);
  if (!ctx) {
    throw new Error("Tabs* components must be used inside <Tabs>");
  }
  return ctx;
}

interface TabsProps extends Omit<React.HTMLAttributes<HTMLDivElement>, "defaultValue"> {
  defaultValue: string;
  value?: string;
  onValueChange?: (next: string) => void;
}

export function Tabs({
  defaultValue,
  value,
  onValueChange,
  className,
  children,
  ...rest
}: TabsProps) {
  const [internal, setInternal] = React.useState(defaultValue);
  const active = value ?? internal;
  const setValue = React.useCallback(
    (next: string) => {
      if (value === undefined) setInternal(next);
      onValueChange?.(next);
    },
    [onValueChange, value],
  );

  return (
    <TabsContext.Provider value={{ value: active, setValue }}>
      <div className={cn("flex flex-col gap-4", className)} {...rest}>
        {children}
      </div>
    </TabsContext.Provider>
  );
}

export const TabsList = React.forwardRef<HTMLDivElement, React.HTMLAttributes<HTMLDivElement>>(
  ({ className, ...props }, ref) => (
    <div
      ref={ref}
      role="tablist"
      className={cn(
        "bg-muted text-muted-foreground inline-flex h-9 items-center justify-start rounded-md p-1",
        className,
      )}
      {...props}
    />
  ),
);
TabsList.displayName = "TabsList";

interface TabsTriggerProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  value: string;
}

export const TabsTrigger = React.forwardRef<HTMLButtonElement, TabsTriggerProps>(
  ({ className, value, ...props }, ref) => {
    const { value: active, setValue } = useTabs();
    const selected = active === value;
    return (
      <button
        ref={ref}
        type="button"
        role="tab"
        aria-selected={selected}
        data-state={selected ? "active" : "inactive"}
        onClick={() => setValue(value)}
        className={cn(
          "inline-flex items-center justify-center whitespace-nowrap rounded-sm px-3 py-1 text-sm font-medium",
          "transition-all focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          "disabled:pointer-events-none disabled:opacity-50",
          selected ? "bg-background text-foreground shadow-sm" : "hover:text-foreground",
          className,
        )}
        {...props}
      />
    );
  },
);
TabsTrigger.displayName = "TabsTrigger";

interface TabsContentProps extends React.HTMLAttributes<HTMLDivElement> {
  value: string;
}

export const TabsContent = React.forwardRef<HTMLDivElement, TabsContentProps>(
  ({ className, value, ...props }, ref) => {
    const { value: active } = useTabs();
    if (active !== value) return null;
    return (
      <div
        ref={ref}
        role="tabpanel"
        data-state="active"
        className={cn("focus-visible:outline-none", className)}
        {...props}
      />
    );
  },
);
TabsContent.displayName = "TabsContent";
