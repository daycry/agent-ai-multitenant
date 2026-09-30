// @vitest-environment jsdom

/**
 * El diálogo del Device Flow bajo `React.StrictMode` (el modo de `next dev`):
 * montar → limpiar → montar de nuevo NO puede dejar el diálogo «cancelado». Con
 * `cancelledRef` sin rearmar, el `start` respondía y su resultado se descartaba en
 * silencio: el operador se quedaba en «Iniciando…» para siempre.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { CopilotDeviceFlowDialog } from "@/app/admin/llm-providers/copilot-device-flow-dialog";
import type { LlmProvider } from "@/app/admin/llm-providers/llm-provider-types";

const PROVIDER: LlmProvider = {
  id: "p-1",
  kind: "github_copilot",
  slug: "copilot",
  display_name: "Copilot empresa",
  base_url: null,
  is_active: true,
  config: {},
  secret_vault_path: null,
  has_credential: false,
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
};

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("CopilotDeviceFlowDialog", () => {
  it("enseña el código tras iniciar aunque StrictMode monte dos veces", async () => {
    apiFetchMock.mockResolvedValue({
      provider_id: "p-1",
      device_code: "dev",
      user_code: "WXYZ-1234",
      verification_uri: "https://github.com/login/device",
      expires_in: 900,
      interval: 60,
    });
    render(
      <React.StrictMode>
        <LanguageProvider>
          <CopilotDeviceFlowDialog provider={PROVIDER} onClose={() => {}} onAuthorized={() => {}} />
        </LanguageProvider>
      </React.StrictMode>,
    );
    fireEvent.click(screen.getByTestId("device-flow-start"));
    await waitFor(() =>
      expect(screen.getByTestId("device-flow-user-code").textContent).toBe("WXYZ-1234"),
    );
  });
});
