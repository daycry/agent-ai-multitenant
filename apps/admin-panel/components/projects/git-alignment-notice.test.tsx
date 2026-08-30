// @vitest-environment jsdom

/**
 * El aviso de alineación de la rama por defecto — H3 del recorrido E2E
 * (`docs/roadmap/2026-08-29-hallazgos-e2e-hello-world-v2.md`).
 *
 * El formulario precarga `main` y el repositorio de pruebas usa `master`. Nada
 * en la pantalla avisa de la discrepancia hasta que el worker intenta alinear la
 * rama CONFIGURADA y `align_default_branch` devuelve `remote_empty`.
 *
 * Ese estado es AMBIGUO por construcción —lo fija
 * `tests/integration/test_remote_empty_is_not_only_an_empty_repo.py`: un remoto
 * con todo su trabajo en `master` produce exactamente el mismo `remote_empty`
 * que un remoto de verdad vacío— y el aviso afirmaba la causa igualmente («repo
 * vacío») y recomendaba «haz un push inicial». Con la rama mal configurada, ese
 * push crea en el remoto una rama que no debería existir.
 *
 * Aquí se comprueba lo único que puede comprobar el panel: que el aviso NOMBRE
 * la rama que falta y NO invente la causa.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: vi.fn() };
});

import { GitConfigSection, type LastGitSync } from "@/components/projects/git-config-section";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

const GIT_CONFIG = {
  provider: "github",
  remote_url: "https://github.com/daycry/test-hello-world.git",
  // Lo que el formulario precarga, y lo que el repositorio real NO tiene.
  default_branch: "main",
  auth_mode: "none",
};

function renderSection(lastSync: LastGitSync, lang: "es" | "en" = "es", branch = "main") {
  window.localStorage.setItem(STORAGE_KEY, lang);
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <LanguageProvider>
        <GitConfigSection
          projectId="p-1"
          value={{ ...GIT_CONFIG, default_branch: branch }}
          lastSync={lastSync}
        />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

function noticeText(): string {
  return screen.getByTestId("git-alignment").textContent ?? "";
}

const REMOTE_EMPTY: LastGitSync = {
  at: "2026-08-29T10:00:00Z",
  status: "ok",
  default_branch_alignment: "remote_empty",
};

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

describe("H3 — el aviso de «el remoto no tiene la rama por defecto»", () => {
  // La rama de las aserciones NO puede ser `main` ni `master`: las dos aparecen
  // literalmente en el texto como ejemplo, así que un `toContain("main")` daba
  // verde con la interpolación quitada. Verificado revirtiendo la línea de
  // producción — es exactamente el test que no mide nada.
  it("nombra la rama CONFIGURADA, no «la rama por defecto» a secas", () => {
    renderSection(REMOTE_EMPTY, "es", "trunk");
    expect(noticeText()).toContain("trunk");
    expect(noticeText()).not.toContain("{branch}");
  });

  it("no afirma que el repositorio esté vacío: ofrece la otra causa", () => {
    // La causa real de H3 —el remoto usa otra rama por defecto— tiene que estar
    // en el texto. Sin ella, el consejo de «haz un push inicial» crea en el
    // remoto una rama que no debería existir.
    renderSection(REMOTE_EMPTY);
    expect(noticeText()).toContain("master");
  });

  it("se traduce, y sigue nombrando la rama", () => {
    renderSection(REMOTE_EMPTY, "en", "trunk");
    expect(noticeText()).toContain("trunk");
    expect(noticeText()).not.toContain("{branch}");
    expect(noticeText()).toContain("master");
    expect(noticeText()).not.toMatch(/vacío|rama por defecto/i);
  });

  it("los estados que NO son ambiguos siguen diciendo lo suyo (no-regresión)", () => {
    renderSection({ ...REMOTE_EMPTY, default_branch_alignment: "created" });
    expect(noticeText()).toMatch(/creada|created/i);
  });
});
