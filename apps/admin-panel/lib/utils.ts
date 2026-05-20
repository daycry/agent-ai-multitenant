import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/**
 * Compose Tailwind class names safely:
 *   clsx flattens conditions, twMerge resolves Tailwind conflicts so the
 *   last specified utility wins (e.g. cn("p-2", "p-4") -> "p-4").
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
