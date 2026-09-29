// @vitest-environment jsdom

/**
 * Patrón de pantalla de recurso (`task_ui_30`): cabecera con contador y acción,
 * barra de búsqueda/filtros, tarjeta con los campos siempre en el mismo sitio y
 * panel lateral de detalle con enlace a la ficha completa.
 */

import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ResourceCard,
  ResourceDetailPanel,
  ResourceHeader,
  ResourceToolbar,
} from "@/components/resource";
import { LanguageProvider } from "@/lib/lang-context";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
});

function wrap(node: React.ReactElement, lang: "es" | "en" = "es") {
  window.localStorage.setItem("admin-panel.lang", lang);
  return render(<LanguageProvider>{node}</LanguageProvider>);
}

describe("ResourceHeader", () => {
  it("pinta título, contador y la acción principal", () => {
    wrap(
      <ResourceHeader
        title="Agentes"
        count={3}
        countLabel="3 agentes"
        action={<button>Nuevo</button>}
      />,
    );
    expect(screen.getByText("Agentes")).toBeTruthy();
    expect(screen.getByTestId("resource-count").textContent).toBe("3 agentes");
    expect(screen.getByRole("button", { name: "Nuevo" })).toBeTruthy();
  });
});

describe("ResourceToolbar", () => {
  it("la búsqueda es controlada y los filtros van como hijos", () => {
    const onSearch = vi.fn();
    wrap(
      <ResourceToolbar
        search=""
        onSearchChange={onSearch}
        searchPlaceholder="Buscar"
        searchTestId="q"
      >
        <span data-testid="extra-filter">filtro</span>
      </ResourceToolbar>,
    );
    fireEvent.change(screen.getByTestId("q"), { target: { value: "ada" } });
    expect(onSearch).toHaveBeenCalledWith("ada");
    expect(screen.getByTestId("extra-filter")).toBeTruthy();
  });
});

describe("ResourceCard", () => {
  it("pinta los campos del patrón en el mismo orden y omite los que no existen", () => {
    wrap(
      <ResourceCard
        testId="card-1"
        name="Ada"
        provenance="Plataforma"
        scope="Plantilla"
        usage="2 equipos"
        onSelect={() => {}}
      />,
    );
    const card = within(screen.getByTestId("card-1"));
    const keys = card.getAllByTestId(/^resource-field-/).map((n) => n.dataset.testid);
    expect(keys).toEqual([
      "resource-field-provenance",
      "resource-field-scope",
      "resource-field-usage",
    ]);
    expect(card.queryByTestId("resource-field-lastActivity")).toBeNull();
  });

  it("seleccionar la tarjeta avisa y marca aria-pressed", () => {
    const onSelect = vi.fn();
    wrap(<ResourceCard testId="c" name="Ada" selected onSelect={onSelect} />);
    const btn = screen.getByTestId("resource-select-c");
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(btn);
    expect(onSelect).toHaveBeenCalled();
  });
});

describe("ResourceDetailPanel", () => {
  it("es un resumen con enlace a la ficha completa y se cierra", () => {
    const onClose = vi.fn();
    wrap(
      <ResourceDetailPanel
        title="Ada"
        fields={[{ key: "role", label: "Rol", value: "backend_dev" }]}
        detailHref="/admin/agents/a1"
        onClose={onClose}
      />,
    );
    expect(screen.getByText("backend_dev")).toBeTruthy();
    expect(screen.getByTestId("resource-panel-detail-link").getAttribute("href")).toBe(
      "/admin/agents/a1",
    );
    fireEvent.click(screen.getByTestId("resource-panel-close"));
    expect(onClose).toHaveBeenCalled();
  });

  it("sin detailHref no hay enlace", () => {
    wrap(<ResourceDetailPanel title="Ada" fields={[]} onClose={() => {}} />);
    expect(screen.queryByTestId("resource-panel-detail-link")).toBeNull();
  });
});
