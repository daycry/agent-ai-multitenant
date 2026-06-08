import { resolve } from "node:path";

import { defineConfig } from "vitest/config";

/**
 * Minimal Vitest config (Plan 06.18 task_06_18_10).
 *
 * Unit tests for framework-free logic (e.g. the shared tools taxonomy).
 * The `@/*` alias mirrors tsconfig so test files import modules exactly like
 * the app does. Node environment is enough — these tests touch pure functions
 * and data, not the DOM.
 */
export default defineConfig({
  resolve: {
    alias: {
      "@": resolve(__dirname, "."),
    },
  },
  test: {
    environment: "node",
    include: ["**/*.test.ts", "**/*.test.tsx"],
    exclude: ["e2e/**", "node_modules/**", ".next/**"],
  },
});
