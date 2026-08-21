// @vitest-environment jsdom

/**
 * El catálogo de tools, migrado al diccionario (plan prod-16, `task_prod16_04`).
 *
 * La pantalla tenía TODO el texto cableado en castellano y, además, cuatro de
 * los nueve ternarios de idioma que quedaban en el panel — los cuatro
 * resolviendo etiquetas de la taxonomía de ADR 0049, que es texto bilingüe en
 * DATOS y por tanto va con `pickLang`/`label()`, no con claves de diccionario.
 * (El anti-patrón no se escribe aquí en su forma literal: `check-i18n.mjs` no
 * distingue comentarios de código y lo contaría como deuda.)
 * Este test fija las dos mitades a la vez:
 *
 *   1. El marco (cabecera, facetas, grupos, diálogos) sale del diccionario.
 *   2. Las etiquetas de taxonomía siguen el idioma activo — que es lo que el
 *      ternario hacía y lo que se rompería en silencio si `label()` se llamara
 *      con el idioma equivocado.
 *
 * Los diálogos entran a propósito: es donde vive la mitad del texto y donde un
 * `useT()` olvidado no se ve hasta que alguien pulsa el botón.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: false,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import ToolsCatalogPage from "@/app/admin/tools/page";

const STORAGE_KEY = "admin-panel.lang";

const BUILTIN = {
  id: "tool-1",
  tenant_id: "t1",
  name: "read_file",
  description: "Lee un fichero del worktree",
  category: "file",
  implementation_type: "builtin",
  implementation_ref: null,
  security_level: "safe",
  is_builtin: true,
  is_runtime_wired: true,
};

const CUSTOM = {
  ...BUILTIN,
  id: "tool-2",
  name: "deploy_preview",
  description: null,
  category: "custom",
  implementation_type: "http_endpoint",
  security_level: "privileged",
  is_builtin: false,
  is_runtime_wired: false,
};

function renderIn(lang: "es" | "en") {
  apiFetchMock.mockImplementation((path: string) => {
    if (typeof path === "string" && path.startsWith("/tools")) {
      return Promise.resolve([BUILTIN, CUSTOM]);
    }
    return Promise.resolve([]);
  });
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <ToolsCatalogPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("tools — catálogo en castellano", () => {
  it("rinde cabecera, facetas y los dos grupos", async () => {
    renderIn("es");

    expect(await screen.findByText("Catálogo de tools")).toBeDefined();
    expect(screen.getByRole("button", { name: "Nueva tool" })).toBeDefined();
    expect(screen.getByLabelText("Buscar tool por nombre o descripción")).toBeDefined();
    expect(screen.getByLabelText("Función")).toBeDefined();
    expect(screen.getByLabelText("Seguridad")).toBeDefined();
    expect(screen.getByLabelText("Origen")).toBeDefined();
    expect(await screen.findByText("De plataforma (built-in)")).toBeDefined();
    expect(screen.getByText("Personalizadas del tenant")).toBeDefined();
  });

  it("las etiquetas de taxonomía (datos bilingües) salen en castellano", async () => {
    renderIn("es");

    // Dentro de la FILA, no en toda la pantalla: los mismos términos aparecen en
    // las opciones de las facetas, que son estáticas y se pintan ANTES de que la
    // query resuelva. Afirmar sobre la pantalla entera daba verde con cero filas
    // renderizadas — el test pasaba sin mirar lo que decía mirar.
    const builtinRow = within(await screen.findByTestId("tool-row-tool-1"));
    const customRow = within(await screen.findByTestId("tool-row-tool-2"));

    // `file` → Archivos, `privileged` → Privilegiada: las resuelve la taxonomía,
    // no el diccionario. Si `label()` recibiera el idioma equivocado, esto sale
    // en inglés sin que ninguna clave falte.
    expect(builtinRow.getByText("Archivos")).toBeDefined();
    expect(customRow.getByText("Privilegiada")).toBeDefined();
    expect(builtinRow.getByText("Solo lectura")).toBeDefined();
    expect(customRow.getByText("No disponible aún")).toBeDefined();
  });

  it("el diálogo de alta rinde sus campos en castellano", async () => {
    renderIn("es");

    fireEvent.click(await screen.findByTestId("tools-create-button"));

    expect(await screen.findByText("Nueva tool personalizada")).toBeDefined();
    expect(screen.getByLabelText("Nombre")).toBeDefined();
    expect(screen.getByLabelText("Descripción")).toBeDefined();
    expect(screen.getByLabelText("Referencia de implementación")).toBeDefined();
    expect(screen.getByRole("button", { name: "Crear tool" })).toBeDefined();
  });
});

describe("tools — catálogo en inglés", () => {
  it("rinde cabecera, facetas y grupos traducidos, sin castellano por debajo", async () => {
    renderIn("en");

    expect(await screen.findByText("Tools catalog")).toBeDefined();
    expect(screen.getByRole("button", { name: "New tool" })).toBeDefined();
    expect(screen.getByLabelText("Search tool by name or description")).toBeDefined();
    expect(screen.getByLabelText("Function")).toBeDefined();
    expect(screen.getByLabelText("Security")).toBeDefined();
    expect(screen.getByLabelText("Origin")).toBeDefined();
    expect(await screen.findByText("Platform (built-in)")).toBeDefined();
    expect(screen.getByText("Tenant custom tools")).toBeDefined();

    expect(screen.queryByText("Catálogo de tools")).toBeNull();
    expect(screen.queryByRole("button", { name: "Nueva tool" })).toBeNull();
    expect(screen.queryByLabelText("Función")).toBeNull();
  });

  it("las etiquetas de taxonomía siguen el idioma activo", async () => {
    renderIn("en");

    const builtinRow = within(await screen.findByTestId("tool-row-tool-1"));
    const customRow = within(await screen.findByTestId("tool-row-tool-2"));

    expect(builtinRow.getByText("Files")).toBeDefined();
    expect(customRow.getByText("Privileged")).toBeDefined();
    expect(builtinRow.getByText("Read-only")).toBeDefined();
    expect(customRow.getByText("Not available yet")).toBeDefined();

    expect(screen.queryByText("Archivos")).toBeNull();
    expect(screen.queryByText("Privilegiada")).toBeNull();
    expect(screen.queryByText("Solo lectura")).toBeNull();
  });

  it("el diálogo de alta rinde sus campos traducidos", async () => {
    renderIn("en");

    fireEvent.click(await screen.findByTestId("tools-create-button"));

    expect(await screen.findByText("New custom tool")).toBeDefined();
    expect(screen.getByLabelText("Name")).toBeDefined();
    expect(screen.getByLabelText("Description")).toBeDefined();
    expect(screen.getByLabelText("Implementation reference")).toBeDefined();
    expect(screen.getByRole("button", { name: "Create tool" })).toBeDefined();

    expect(screen.queryByLabelText("Nombre")).toBeNull();
    expect(screen.queryByRole("button", { name: "Crear tool" })).toBeNull();
  });

  it("el diálogo de borrado rinde su aviso traducido", async () => {
    renderIn("en");

    fireEvent.click(await screen.findByTestId("tool-delete-tool-2"));

    expect(await screen.findByText("Delete tool")).toBeDefined();
    expect(screen.getByTestId("tool-delete-confirm").textContent).toContain("Delete");
    expect(screen.queryByText("Borrar tool")).toBeNull();
  });
});
