// @vitest-environment jsdom
// prod-16 `task_prod16_07` — las «Opciones avanzadas» del formulario de MCP
// server, sacadas de `mcp-server-dialog.tsx` a su propio fichero.
//
// **Este bloque NO tenía ni un test, y ahí estaba el riesgo del troceo.** Los 15
// tests del módulo cubren el alta, el cambio de transporte y «Probar conexión»
// (`page.test.tsx` pulsa `mcp-form-test` y comprueba el panel de resultados),
// pero ninguno abría las opciones avanzadas. O sea que la otra mitad del corte
// —112 líneas de JSX con la tarjeta de credencial gestionada, la escotilla de
// «Detalles técnicos» y el timeout— se habría podido romper en silencio y salir
// verde: el diálogo sigue montando, `tsc` no ve nada raro y el bloque está
// colapsado por defecto, así que ni se renderiza.
//
// Lo que clava:
//   - el resumen colapsado dice el timeout, y «credencial • » sólo si hay
//     `auth_ref` (es la única señal de que hay secreto sin abrir el bloque);
//   - abrir/cerrar va por `onOpenChange`, no por estado propio — el diálogo lo
//     abre solo al aplicar una plantilla con secretos;
//   - editar `auth_ref` emite el `onChange` con el valor Y avisa con
//     `onManualAuthEdit` de que la plantilla deja de gobernar la ruta;
//   - con plantilla que pide credencial se ve la TARJETA, no el `vault:…` en
//     crudo, y «Detalles técnicos» pide el cambio por `onShowRawAuthChange`;
//   - el timeout emite número, y un valor no numérico cae al default de 30 (no
//     a `NaN`, que viajaría al backend).

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { McpAdvancedOptionsSection } from "@/app/admin/projects/[id]/mcp-servers/mcp-advanced-options-section";
import {
  type McpCatalogEntry,
  type McpServerConfig,
} from "@/app/admin/projects/[id]/mcp-servers/mcp-server-types";

const SERVER: McpServerConfig = {
  name: "files-server",
  transport: "stdio",
  command: "docling-mcp",
  args: [],
  env: {},
  url: null,
  headers: {},
  auth_ref: null,
  timeout_s: 30,
};

const TEMPLATE_WITH_SECRET: McpCatalogEntry = {
  id: "github",
  display_name: "GitHub MCP",
  description: "Issues y PRs",
  transport: "stdio",
  command: "github-mcp",
  args: [],
  url: null,
  secret_keys: ["GITHUB_TOKEN"],
  vault_path_template: "vault:secret/data/mcp/github/{project_id}",
  default_timeout_s: 30,
  static_env: {},
  static_headers: {},
  maintainer: "github",
  repo_url: "https://example.invalid/repo",
  docs_url: "https://example.invalid/docs",
  category: "git",
  requires_auth: true,
};

function renderSection(overrides: Partial<React.ComponentProps<typeof McpAdvancedOptionsSection>>) {
  const props = {
    state: SERVER,
    onChange: vi.fn(),
    appliedTemplate: null,
    onManualAuthEdit: vi.fn(),
    open: false,
    onOpenChange: vi.fn(),
    showRawAuth: false,
    onShowRawAuthChange: vi.fn(),
    ...overrides,
  };
  render(<McpAdvancedOptionsSection {...props} />);
  return props;
}

afterEach(cleanup);

describe("McpAdvancedOptionsSection (prod-16 task_prod16_07)", () => {
  it("colapsada, resume el timeout y no pinta los campos", () => {
    renderSection({});

    expect(screen.getByTestId("mcp-form-advanced-toggle").textContent).toContain("timeout 30s");
    expect(screen.getByTestId("mcp-form-advanced-toggle").textContent).not.toContain("credencial");
    expect(screen.queryByTestId("mcp-form-auth-ref")).toBeNull();
    expect(screen.queryByTestId("mcp-form-timeout")).toBeNull();
  });

  it("con auth_ref, el resumen avisa de que hay credencial sin abrir el bloque", () => {
    renderSection({ state: { ...SERVER, auth_ref: "vault:secret/data/mcp/x/y", timeout_s: 120 } });

    const summary = screen.getByTestId("mcp-form-advanced-toggle").textContent ?? "";
    expect(summary).toContain("credencial");
    expect(summary).toContain("timeout 120s");
  });

  it("el toggle no guarda estado propio: pide el cambio al diálogo", () => {
    const props = renderSection({ open: false });

    fireEvent.click(screen.getByTestId("mcp-form-advanced-toggle"));

    expect(props.onOpenChange).toHaveBeenCalledWith(true);
    // Y sigue colapsada: quien manda es la prop, no un useState de aquí.
    expect(screen.queryByTestId("mcp-form-auth-ref")).toBeNull();
  });

  it("abierta y sin plantilla, edita auth_ref y no avisa de plantilla alguna", () => {
    const props = renderSection({ open: true });

    fireEvent.change(screen.getByTestId("mcp-form-auth-ref"), {
      target: { value: "vault:secret/data/mcp/github/p1" },
    });

    expect(props.onChange).toHaveBeenCalledWith({
      ...SERVER,
      auth_ref: "vault:secret/data/mcp/github/p1",
    });
    expect(props.onManualAuthEdit).not.toHaveBeenCalled();
  });

  it("con plantilla aplicada, editar la ruta a mano rompe la invariante y lo avisa", () => {
    const props = renderSection({
      open: true,
      appliedTemplate: TEMPLATE_WITH_SECRET,
      showRawAuth: true,
    });

    fireEvent.change(screen.getByTestId("mcp-form-auth-ref"), {
      target: { value: "vault:otra/convencion" },
    });

    expect(props.onChange).toHaveBeenCalledWith({ ...SERVER, auth_ref: "vault:otra/convencion" });
    expect(props.onManualAuthEdit).toHaveBeenCalledTimes(1);
  });

  it("plantilla con secreto: tarjeta amable, no el vault: en crudo", () => {
    const props = renderSection({
      open: true,
      appliedTemplate: TEMPLATE_WITH_SECRET,
      showRawAuth: false,
    });

    const card = screen.getByTestId("mcp-form-auth-managed");
    expect(card.textContent).toContain("GITHUB_TOKEN");
    expect(screen.queryByTestId("mcp-form-auth-ref")).toBeNull();

    fireEvent.click(screen.getByTestId("mcp-form-show-raw-auth"));
    expect(props.onShowRawAuthChange).toHaveBeenCalledWith(true);
  });

  it("la escotilla abierta enseña el input y ofrece volver a esconderlo", () => {
    const props = renderSection({
      open: true,
      appliedTemplate: TEMPLATE_WITH_SECRET,
      showRawAuth: true,
    });

    expect(screen.getByTestId("mcp-form-auth-ref")).toBeTruthy();
    expect(screen.queryByTestId("mcp-form-auth-managed")).toBeNull();

    fireEvent.click(screen.getByTestId("mcp-form-hide-raw-auth"));
    expect(props.onShowRawAuthChange).toHaveBeenCalledWith(false);
  });

  it("el timeout emite número, y un valor no numérico cae a 30 en vez de a NaN", () => {
    const props = renderSection({ open: true, state: { ...SERVER, timeout_s: 120 } });

    fireEvent.change(screen.getByTestId("mcp-form-timeout"), { target: { value: "45" } });
    expect(props.onChange).toHaveBeenCalledWith({ ...SERVER, timeout_s: 45 });

    fireEvent.change(screen.getByTestId("mcp-form-timeout"), { target: { value: "" } });
    expect(props.onChange).toHaveBeenLastCalledWith({ ...SERVER, timeout_s: 30 });
  });
});
