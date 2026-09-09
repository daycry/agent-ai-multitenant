// @vitest-environment jsdom
// task_mk_11 (UI-02): el bloque compartido que enseña lo que un despliegue dijo
// de sí mismo — avisos, OAuth pendiente, `created_refs` y el tipo diferido.

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import {
  DeployResultNotes,
  mentionsDeferredType,
  summariseCreatedRefs,
} from "@/components/marketplace/deploy-result-notes";

afterEach(cleanup);

describe("summariseCreatedRefs", () => {
  it("aplana listas y objetos, y omite lo vacío", () => {
    expect(
      summariseCreatedRefs({
        mcp_servers: ["jira"],
        mcp_tool_roles: [],
        mcp_import: { server: "jira", queue: "marketplace" },
        agent_tools: ["a1", "a2"],
        nada: null,
      }),
    ).toEqual([
      ["mcp_servers", "jira"],
      ["mcp_import", "server=jira · queue=marketplace"],
      ["agent_tools", "a1, a2"],
    ]);
  });

  it("con nada no hay líneas", () => {
    expect(summariseCreatedRefs({})).toEqual([]);
    expect(summariseCreatedRefs(null)).toEqual([]);
  });
});

describe("mentionsDeferredType", () => {
  it("detecta el aviso del tipo diferido en cualquiera de los dos idiomas", () => {
    expect(mentionsDeferredType(["no tiene fila de catálogo materializada (tipo diferido…)"])).toBe(
      true,
    );
    expect(mentionsDeferredType(["deferred implementation type"])).toBe(true);
    expect(mentionsDeferredType(["sin roles en el role_map"])).toBe(false);
  });
});

describe("DeployResultNotes", () => {
  it("pinta avisos, OAuth, referencias con etiqueta traducida y la nota de diferido con enlace", () => {
    render(
      <DeployResultNotes
        warnings={["tipo diferido por el sandbox del ADR 0081"]}
        oauthPending
        createdRefs={{ agent_skills: ["reviewer"], desconocida: ["x"] }}
        testId="p1"
      />,
    );
    expect(screen.getByTestId("deploy-warnings-p1").textContent).toContain("ADR 0081");
    expect(screen.getByTestId("deploy-oauth-p1")).toBeTruthy();
    expect(screen.getByTestId("deploy-deferred-p1").textContent).toContain(
      "autorizada, sin capacidad",
    );
    const refs = screen.getByTestId("deploy-created-refs-p1").textContent ?? "";
    expect(refs).toContain("Agentes que recibieron la skill");
    expect(refs).toContain("reviewer");
    // Una clave que el backend añada y este mapa no conozca se pinta cruda.
    expect(refs).toContain("desconocida");
  });

  it("sin nada que decir no pinta ningún bloque", () => {
    render(<DeployResultNotes warnings={[]} oauthPending={false} createdRefs={{}} testId="p2" />);
    expect(screen.queryByTestId("deploy-warnings-p2")).toBeNull();
    expect(screen.queryByTestId("deploy-oauth-p2")).toBeNull();
    expect(screen.queryByTestId("deploy-created-refs-p2")).toBeNull();
    expect(screen.queryByTestId("deploy-deferred-p2")).toBeNull();
  });
});
