// @vitest-environment jsdom
// task_mk_02 (ADR 0165 D6/D7): el control `string_list` y el bloque de egress.
//
// Lo que clava:
//   - una entrada por línea, sin vacías ni repetidas, y el tope del registry
//     bloquea el guardado en vez de descubrirlo en el 422;
//   - tras guardar, el panel dice «pendiente de aplicar» con el comando, y JAMÁS
//     «permitido» (D7.2);
//   - el sondeo pregunta al proxy (`POST /admin/egress/mcp-allowlist/probe`) y
//     pinta un veredicto por host; sin proxy configurado lo dice (D7.3);
//   - el bloque de egress sólo aparece para `egress.mcp_allowed_hosts`: el
//     control es genérico.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import {
  APPLY_COMMAND,
  EGRESS_ALLOWLIST_KEY,
  StringListControl,
  parseLines,
} from "@/app/admin/settings/platform-defaults/string-list-control";

function mount(props: Partial<React.ComponentProps<typeof StringListControl>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onSave = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <StringListControl
        settingKey={EGRESS_ALLOWLIST_KEY}
        value={["mcp.atlassian.com", "api.githubcopilot.com"]}
        maxItems={100}
        onSave={onSave}
        pending={false}
        saved={false}
        {...props}
      />
    </QueryClientProvider>,
  );
  return { onSave };
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("parseLines", () => {
  it("una por línea, sin vacías ni repetidas, recortadas", () => {
    expect(parseLines(" a.example.com \n\nb.example.com\r\na.example.com\n")).toEqual([
      "a.example.com",
      "b.example.com",
    ]);
  });
});

describe("StringListControl", () => {
  it("pinta el valor actual una entrada por línea y cuenta contra el tope", () => {
    mount();
    const editor = screen.getByTestId("string-list-editor") as HTMLTextAreaElement;
    expect(editor.value).toBe("mcp.atlassian.com\napi.githubcopilot.com");
    expect(screen.getByText("2 de 100 entradas")).toBeTruthy();
  });

  it("guardar manda la lista parseada, no el texto", () => {
    const { onSave } = mount();
    fireEvent.change(screen.getByTestId("string-list-editor"), {
      target: { value: "mcp.atlassian.com\n\n mcp.atlassian.com \nnuevo.example.com" },
    });
    fireEvent.click(screen.getByTestId("platform-setting-save"));
    expect(onSave).toHaveBeenCalledWith(["mcp.atlassian.com", "nuevo.example.com"]);
  });

  it("por encima del tope no deja guardar", () => {
    const { onSave } = mount({ maxItems: 1 });
    const save = screen.getByTestId("platform-setting-save") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    expect(screen.getByText("2 de 1 entradas")).toBeTruthy();
    fireEvent.click(save);
    expect(onSave).not.toHaveBeenCalled();
  });

  it("tras guardar dice «pendiente de aplicar» con el comando, y nunca «permitido»", () => {
    mount({ saved: true });
    const pending = screen.getByTestId("egress-pending-apply");
    expect(pending.textContent).toContain("pendiente de aplicar al proxy");
    expect(pending.textContent).toContain(APPLY_COMMAND);
    expect(pending.textContent?.toLowerCase()).not.toContain("permitido");
  });

  it("antes de guardar no hay bloque de «pendiente de aplicar»", () => {
    mount({ saved: false });
    expect(screen.queryByTestId("egress-pending-apply")).toBeNull();
  });

  it("enseña el hecho global de los puertos y que no caben comodines", () => {
    mount();
    expect(screen.getByText(/443 y 8443/)).toBeTruthy();
    expect(screen.getByText(/no caben comodines/i)).toBeTruthy();
    expect(screen.getByText(/NO es control de exfiltración/)).toBeTruthy();
  });

  it("el sondeo pregunta al proxy y pinta un veredicto por host", async () => {
    apiFetchMock.mockResolvedValue({
      proxy_url_configured: true,
      results: [
        { host: "mcp.atlassian.com", verdict: "permitido", detail: "200" },
        { host: "api.githubcopilot.com", verdict: "bloqueado", detail: "403 Filtered" },
      ],
    });
    mount();
    fireEvent.click(screen.getByTestId("egress-probe-button"));
    await waitFor(() => expect(screen.getByTestId("egress-probe-results")).toBeTruthy());
    expect(apiFetchMock).toHaveBeenCalledWith(
      "/admin/egress/mcp-allowlist/probe",
      expect.objectContaining({ method: "POST" }),
    );
    expect(screen.getByTestId("egress-probe-mcp.atlassian.com").textContent).toContain("permitido");
    expect(screen.getByTestId("egress-probe-api.githubcopilot.com").textContent).toContain(
      "bloqueado",
    );
  });

  it("sin proxy configurado el sondeo lo dice en vez de inventar un veredicto", async () => {
    apiFetchMock.mockResolvedValue({ proxy_url_configured: false, results: [] });
    mount();
    fireEvent.click(screen.getByTestId("egress-probe-button"));
    await waitFor(() => expect(screen.getByTestId("egress-probe-no-proxy")).toBeTruthy());
    expect(screen.getByTestId("egress-probe-no-proxy").textContent).toContain(
      "API_SERVER_EGRESS_PROXY_URL",
    );
  });

  it("el bloque de egress no aparece para otra lista", () => {
    mount({ settingKey: "otra.lista" });
    expect(screen.queryByTestId("egress-probe")).toBeNull();
    expect(screen.queryByText(/443 y 8443/)).toBeNull();
    expect(screen.getByTestId("string-list-editor")).toBeTruthy();
  });
});
