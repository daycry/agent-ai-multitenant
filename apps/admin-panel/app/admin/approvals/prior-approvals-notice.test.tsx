// @vitest-environment jsdom
// ADR 0135 (N3) — un «casi igual» vuelve a preguntar, pero ENSEÑANDO qué cambió.
//
// La decisión del operador acepta que el bucle no se cierre al 100 %: lo que
// ataca es el COSTE de cada vuelta. Para el humano ese coste es releer una
// acción entera para descubrir que solo cambió un salto de línea. Este bloque
// es la mitad visible de esa decisión, así que se prueba: si el delta no se
// pinta, N3 es una nota en un ADR y nada más.

import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it } from "vitest";

import { PriorApprovalsNotice } from "@/app/admin/approvals/prior-approvals-notice";

afterEach(cleanup);

describe("PriorApprovalsNotice", () => {
  it("no pinta nada cuando la solicitud no tiene contexto previo", () => {
    const { container } = render(<PriorApprovalsNotice action={{ tool: "write_file" }} />);
    expect(container.innerHTML).toBe("");
  });

  it("enseña el delta de un casi igual", () => {
    render(
      <PriorApprovalsNotice
        action={{
          tool: "write_file",
          args: { path: "src/app.py" },
          prior_approvals: {
            same_action_approved_times: 0,
            closest_prior: {
              request_id: "r-1",
              resolved_at: "2026-07-31T10:00:00+00:00",
              args: { path: "src/viejo.py" },
              changed_args: { path: { before: "src/viejo.py", after: "src/app.py" } },
            },
          },
        }}
      />,
    );
    expect(screen.getByTestId("approval-delta")).toBeTruthy();
    expect(screen.getByTestId("approval-delta-key-path").textContent).toContain("path");
    expect(screen.getByTestId("approval-delta-before-path").textContent).toContain("src/viejo.py");
    expect(screen.getByTestId("approval-delta-after-path").textContent).toContain("src/app.py");
  });

  it("avisa cuando la MISMA acción ya se aprobó antes", () => {
    // La otra recomendación del ADR: quien ve «aprobada 3 veces» deja de
    // aprobar y llama a alguien, que es la respuesta correcta.
    render(
      <PriorApprovalsNotice
        action={{
          tool: "write_file",
          prior_approvals: { same_action_approved_times: 3, closest_prior: null },
        }}
      />,
    );
    expect(screen.getByTestId("approval-repeat-warning").textContent).toContain("3");
  });

  it("no avisa de repeticiones cuando no las hay", () => {
    render(
      <PriorApprovalsNotice
        action={{
          tool: "write_file",
          prior_approvals: {
            same_action_approved_times: 0,
            closest_prior: {
              request_id: "r-1",
              resolved_at: null,
              args: {},
              changed_args: { path: { before: null, after: "a.py" } },
            },
          },
        }}
      />,
    );
    expect(screen.queryByTestId("approval-repeat-warning")).toBeNull();
  });

  it("tolera un contexto malformado sin romper la tarjeta", () => {
    const { container } = render(
      <PriorApprovalsNotice action={{ prior_approvals: "no soy un objeto" }} />,
    );
    expect(container.innerHTML).toBe("");
  });
});
