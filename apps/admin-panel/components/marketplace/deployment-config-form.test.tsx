// @vitest-environment jsdom
//
// `DeploymentConfigForm` — la pieza que reutilizan las TRES puertas de
// despliegue (ADR 0142, `task_mkt2_06`). Lo que este test clava es justo lo que
// el plan exige de ella:
//
//   * los defaults del `config_schema` ya están puestos al abrir el formulario;
//   * un campo `secret: true` pinta un input de PUNTERO A VAULT, y meter el
//     secreto en claro lo rechaza con un error que **no ecoa el valor**;
//   * los roles llegan pre-marcados desde los `targets` del manifest (D5) y son
//     editables;
//   * con errores de validación el submit queda bloqueado y los errores se ven.
//
// El botón de submit no vive en el formulario (cada puerta submitea a su
// manera): el arnés de abajo replica el contrato que las tres usan —deshabilitar
// mientras `draftErrors` no esté vacío—, que es lo que hay que verificar.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { afterEach, describe, expect, it } from "vitest";

import { DeploymentConfigForm } from "./deployment-config-form";
import {
  draftErrors,
  initialDraft,
  type CapabilityShape,
  type DeploymentDraft,
} from "./deployment-types";

const CAP: CapabilityShape = {
  config_schema: {
    properties: {
      base_url: { type: "string", title: "Base URL", default: null },
      timeout_ms: { type: "integer", title: "Timeout (ms)", default: 30000, minimum: 1 },
      headless: { type: "boolean", title: "Headless", default: true },
      screenshots: {
        type: "string",
        title: "Screenshots",
        enum: ["off", "on", "only-on-failure"],
        default: "only-on-failure",
      },
      browsers: {
        type: "array",
        title: "Navegadores",
        items: { enum: ["chromium", "firefox", "webkit"] },
        minItems: 1,
        default: ["chromium"],
      },
      api_token: { type: "string", title: "API token", secret: true },
    },
    required: ["timeout_ms"],
  },
  targets: ["backend_dev", "qa"],
};

/** Arnés: estado del borrador + el submit que las tres puertas replican. */
function Harness({ capability = CAP }: { capability?: CapabilityShape }) {
  const [draft, setDraft] = useState<DeploymentDraft>(() => initialDraft(capability));
  const errors = draftErrors(capability, draft);
  return (
    <div>
      <DeploymentConfigForm
        idPrefix="dep"
        capability={capability}
        draft={draft}
        onChange={setDraft}
      />
      <button type="button" data-testid="dep-submit" disabled={errors.length > 0}>
        deploy
      </button>
    </div>
  );
}

afterEach(cleanup);

describe("DeploymentConfigForm", () => {
  it("aplica los defaults del esquema al abrir", () => {
    render(<Harness />);
    expect((screen.getByTestId("dep-field-timeout_ms") as HTMLInputElement).value).toBe("30000");
    expect((screen.getByTestId("dep-field-headless") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId("dep-field-screenshots") as HTMLSelectElement).value).toBe(
      "only-on-failure",
    );
    expect(
      (screen.getByTestId("dep-item-browsers-chromium") as HTMLElement).getAttribute(
        "aria-pressed",
      ),
    ).toBe("true");
    // `default: null` es "opcional, vacío": el input nace en blanco, no con "null".
    expect((screen.getByTestId("dep-field-base_url") as HTMLInputElement).value).toBe("");
  });

  it("pre-marca los roles de `targets` y deja desmarcarlos", () => {
    render(<Harness />);
    expect((screen.getByTestId("dep-role-backend_dev") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId("dep-role-qa") as HTMLInputElement).checked).toBe(true);
    expect((screen.getByTestId("dep-role-reviewer") as HTMLInputElement).checked).toBe(false);

    fireEvent.click(screen.getByTestId("dep-role-qa"));
    expect((screen.getByTestId("dep-role-qa") as HTMLInputElement).checked).toBe(false);
    // Quedarse sin roles no es un error de validación, pero sí un aviso visible.
    fireEvent.click(screen.getByTestId("dep-role-backend_dev"));
    expect(screen.getByTestId("dep-roles-empty-warning")).toBeTruthy();
  });

  it("un campo `secret` pide un puntero a Vault y su error NO ecoa el secreto", () => {
    render(<Harness />);
    // El campo se anuncia como puntero, no como "escribe aquí tu token".
    expect(screen.getByTestId("dep-secret-help-api_token")).toBeTruthy();

    const secret = "sk-live-0123456789";
    fireEvent.change(screen.getByTestId("dep-field-api_token"), { target: { value: secret } });

    const errorList = screen.getByTestId("dep-errors");
    expect(errorList.textContent).toContain("api_token");
    expect(errorList.textContent ?? "").not.toContain(secret);
    expect((screen.getByTestId("dep-submit") as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(screen.getByTestId("dep-field-api_token"), {
      target: { value: "vault:kv/data/tenant/token" },
    });
    expect(screen.queryByTestId("dep-errors")).toBeNull();
    expect((screen.getByTestId("dep-submit") as HTMLButtonElement).disabled).toBe(false);
  });

  it("bloquea el submit con los errores del esquema visibles", () => {
    render(<Harness />);
    expect((screen.getByTestId("dep-submit") as HTMLButtonElement).disabled).toBe(false);

    // Desmarcar el único navegador incumple `minItems: 1`.
    fireEvent.click(screen.getByTestId("dep-item-browsers-chromium"));
    expect(screen.getByTestId("dep-errors").textContent).toContain("browsers");
    expect((screen.getByTestId("dep-submit") as HTMLButtonElement).disabled).toBe(true);

    // Y un entero por debajo del mínimo.
    fireEvent.click(screen.getByTestId("dep-item-browsers-chromium"));
    fireEvent.change(screen.getByTestId("dep-field-timeout_ms"), { target: { value: "0" } });
    expect(screen.getByTestId("dep-errors").textContent).toContain("timeout_ms");
    expect((screen.getByTestId("dep-submit") as HTMLButtonElement).disabled).toBe(true);
  });

  it("una capacidad sin `config_schema` lo dice en vez de pintar un formulario vacío", () => {
    render(<Harness capability={{ config_schema: null, targets: [] }} />);
    expect(screen.getByTestId("dep-no-config")).toBeTruthy();
    expect(screen.queryByTestId("dep-errors")).toBeNull();
    // Los roles se siguen eligiendo: sin `config_schema` sigue habiendo a quién dárselo.
    expect(screen.getByTestId("dep-role-backend_dev")).toBeTruthy();
  });
});
