import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";

/**
 * Vitest config for the admin-panel.
 *
 * Scope is deliberately narrow: only `*.test.ts(x)` unit files. The
 * Playwright e2e suite under `e2e/` uses `*.spec.ts` and its own runner
 * (`npm run e2e`), so it must NOT be collected here — otherwise Playwright's
 * `test()` is called outside its runner and every spec errors.
 *
 * The `@/` alias mirrors `tsconfig.json` `paths` so modules that import
 * sibling app code (e.g. `lib/assistant.ts` → `@/lib/api`) resolve under
 * vitest the same way they do under Next.js / tsc.
 */
export default defineConfig({
  // Next compila JSX con el runtime automático (los componentes no importan
  // React); esbuild bajo vitest debe hacer lo mismo o los render-tests jsdom
  // caen con "React is not defined".
  esbuild: { jsx: "automatic" },
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    include: ["**/*.test.{ts,tsx}"],
    exclude: ["node_modules", ".next", "e2e", "test-results"],
    // Node por defecto (tests de lógica pura); los render-tests declaran
    // `// @vitest-environment jsdom` por fichero.
    environment: "node",
  },
});
