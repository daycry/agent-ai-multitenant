// @vitest-environment jsdom
/**
 * La barra contextual del proyecto (`task_ui_10`): las ocho entradas del plan, la
 * vuelta a «Proyectos» y la entrada activa.
 */

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

import { SidebarProject } from "./sidebar-project";

const PROJECT_ID = "8f14e45f-ceea-467a-9f0a-1a2b3c4d5e6f";
const ROOT = `/admin/projects/${PROJECT_ID}`;

afterEach(cleanup);

function renderAt(pathname: string) {
  return render(
    <LanguageProvider>
      <SidebarProject projectId={PROJECT_ID} pathname={pathname} onItemClick={() => {}} />
    </LanguageProvider>,
  );
}

const ids = () =>
  Array.from(document.querySelectorAll('[data-testid^="nav-project-"]')).map((el) =>
    el.getAttribute("data-testid"),
  );

describe("SidebarProject", () => {
  it("pinta las ocho entradas en su orden y la vuelta al portfolio", () => {
    renderAt(ROOT);
    expect(ids()).toEqual([
      "nav-project-overview",
      "nav-project-board",
      "nav-project-runs",
      "nav-project-team",
      "nav-project-capabilities",
      "nav-project-knowledge",
      "nav-project-costs",
      "nav-project-settings",
    ]);
    expect(screen.getByTestId("nav-back-to-projects").getAttribute("href")).toBe("/admin/projects");
  });

  it("enlaza las que existen a la ruta actual del proyecto", () => {
    renderAt(ROOT);
    expect(screen.getByTestId("nav-project-board").getAttribute("href")).toBe(`${ROOT}/board`);
    expect(screen.getByTestId("nav-project-capabilities").getAttribute("href")).toBe(
      `${ROOT}/capabilities`,
    );
    // Ajustes es la ficha del proyecto (edición, git, gobierno), no otra pantalla.
    expect(screen.getByTestId("nav-project-settings").getAttribute("href")).toBe(
      `${ROOT}#settings`,
    );
    expect(screen.getByTestId("nav-project-knowledge").getAttribute("href")).toBe(
      `${ROOT}/knowledge-bases`,
    );
  });

  it("enlaza Runs, Equipo y Costes a sus pantallas (task_ui_12)", () => {
    renderAt(ROOT);
    for (const id of ["runs", "team", "costs"]) {
      const el = screen.getByTestId(`nav-project-${id}`);
      expect(el.getAttribute("href"), id).toBe(`${ROOT}/${id}`);
      expect(el.getAttribute("aria-disabled"), id).toBeNull();
    }
  });

  it("marca el resumen SÓLO en la raíz del proyecto", () => {
    renderAt(ROOT);
    expect(screen.getByTestId("nav-project-overview").getAttribute("aria-current")).toBe("page");
    cleanup();
    renderAt(`${ROOT}/mcp-servers`);
    expect(screen.getByTestId("nav-project-overview").getAttribute("aria-current")).toBeNull();
  });

  it("mantiene activa la pestaña de las pantallas que agrupa", () => {
    renderAt(`${ROOT}/commands`);
    expect(screen.getByTestId("nav-project-capabilities").getAttribute("aria-current")).toBe(
      "page",
    );
    cleanup();
    renderAt(`${ROOT}/plans/abc`);
    expect(screen.getByTestId("nav-project-board").getAttribute("aria-current")).toBe("page");
    cleanup();
    renderAt(`${ROOT}/dep-cache`);
    expect(screen.getByTestId("nav-project-settings").getAttribute("aria-current")).toBe("page");
  });
});
