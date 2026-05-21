import { Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Small spinning indicator built on top of lucide's Loader2.
 * Inline by default so it can live next to button text without
 * disturbing the line height.
 */
export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("h-4 w-4 animate-spin", className)} aria-hidden="true" />;
}
