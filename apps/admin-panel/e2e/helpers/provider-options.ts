/**
 * Fixture de `GET /agents/provider-options` (ADR 0082).
 *
 * Desde el ADR 0082 el selector de proveedor de la persona NO ofrece los cuatro
 * *kinds* del catálogo cerrado: ofrece las FILAS de proveedor configuradas en el
 * tenant, y el kind lo gobierna la fila. `validateDraft` exige `provider_id`, así
 * que sin esta respuesta el borrador nunca es válido y el botón Guardar / Crear
 * queda deshabilitado para siempre — que es exactamente el fallo con el que
 * `agent-create` y `agent-edit-delete` se quedaban esperando un click imposible.
 *
 * Se declara una fila por cada kind del catálogo cerrado (ADR 0021) para que un
 * spec pueda comprobar que la pantalla no inventa proveedores: enseña las filas
 * que le da la API, ni una más.
 */

import type { Page } from "@playwright/test";

import { apiRoute } from "./api";

export interface ProviderOptionFixture {
  id: string;
  kind: string;
  display_name: string;
  slug: string | null;
  models: string[];
  reasoning_options: string[];
}

/** Los cuatro kinds del catálogo cerrado del ADR 0021. Ningún quinto. */
export const CLOSED_PROVIDER_KINDS = ["claude_sdk", "copilot", "azure_foundry", "ollama"] as const;

export const PROVIDER_OPTIONS: ProviderOptionFixture[] = [
  {
    id: "prov-claude-1",
    kind: "claude_sdk",
    display_name: "Claude (suscripción)",
    slug: "claude",
    models: ["claude-opus-4", "claude-sonnet-4"],
    reasoning_options: [],
  },
  {
    id: "prov-copilot-1",
    kind: "copilot",
    display_name: "GitHub Copilot",
    slug: "copilot",
    models: ["gpt-4o", "gpt-4.1"],
    reasoning_options: [],
  },
  {
    id: "prov-foundry-1",
    kind: "azure_foundry",
    display_name: "Azure AI Foundry",
    slug: "foundry",
    models: ["gpt-4o-mini"],
    reasoning_options: [],
  },
  {
    id: "prov-ollama-1",
    kind: "ollama",
    display_name: "Ollama local",
    slug: "ollama",
    models: ["llama3"],
    reasoning_options: [],
  },
];

/** Registra el mock de `/agents/provider-options` con las cuatro filas. */
export async function mockProviderOptions(
  page: Page,
  providers: ProviderOptionFixture[] = PROVIDER_OPTIONS,
): Promise<void> {
  await page.route(apiRoute("/agents/provider-options"), (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ providers }),
    }),
  );
}
