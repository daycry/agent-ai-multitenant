import { defineConfig } from "vitest/config";

/**
 * Vitest config for the admin-panel.
 *
 * Scope is deliberately narrow: only `*.test.ts(x)` unit files. The
 * Playwright e2e suite under `e2e/` uses `*.spec.ts` and its own runner
 * (`npm run e2e`), so it must NOT be collected here — otherwise Playwright's
 * `test()` is called outside its runner and every spec errors.
 */
export default defineConfig({
  test: {
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules", ".next", "e2e", "test-results"],
    environment: "node",
  },
});
