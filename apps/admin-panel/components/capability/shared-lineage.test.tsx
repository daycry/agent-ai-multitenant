// @vitest-environment jsdom
/**
 * Aviso de linaje compartido en el Hub de Capacidad (`task_gov_07`).
 *
 * Plan `gov-01`, fase 3. El backend ya emite el aviso en `warnings` con el
 * `code` estable `shared_model_lineage` y su texto en `es`/`en`; aquí se fija
 * que el Hub lo PINTA — sin esta mitad el aviso viajaría en el JSON y no lo
 * vería nadie, que es el patrón que `verificar-antes-de-implementar.md` §5
 * documenta como el modo de fallo dominante de esta base.
 *
 * Dos reglas que estos tests protegen y que ya se rompieron una vez en el Hub:
 *
 *  - **emparejar por `code`, nunca por el texto castellano** — hacerlo por texto
 *    dejó muerta la rama EN hasta el follow-up bilingüe de 06.17;
 *  - **el aviso de agente global (ADR 0054) sigue siendo el suyo** — el nuevo no
 *    lo pisa ni se pisa con él; pueden coexistir.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

import { CapabilityHub } from "@/components/capability/capability-hub";
import {
  WARN_SHARED_LINEAGE,
  sharedLineageNotice,
  type CapabilitiesResponse,
  type CapabilityWarning,
} from "@/lib/capability/hub";

const LINEAGE_WARNING: CapabilityWarning = {
  code: "shared_model_lineage",
  es: "Linaje compartido: este agente y su revisor «QA» resuelven modelos de la misma familia (anthropic).",
  en: "Shared lineage: this agent and its reviewer “QA” resolve models from the same family (anthropic).",
};

const GLOBAL_WARNING: CapabilityWarning = {
  code: "global_agent_no_project_context",
  es: "Agente global: plantilla read-only.",
  en: "Global agent: read-only template.",
};

function caps(overrides: Partial<CapabilitiesResponse> = {}): CapabilitiesResponse {
  return {
    entity_type: "agent",
    entity_id: "11111111-1111-1111-1111-111111111111",
    saber: { knowledge_bases: [] },
    recordar: { memory_scope: "project_shared", memory: [] },
    ser: {
      model_configured: true,
      provider: "claude_sdk",
      model: "claude-sonnet-4",
      temperature: 0.1,
      system_prompt_present: true,
      model_origin: "agent",
    },
    hacer: { effective: [], unrestricted: false, shell_exec_effective: false },
    warnings: [],
    ...overrides,
  } as CapabilitiesResponse;
}

describe("sharedLineageNotice", () => {
  it("expone el código estable que espeja al backend", () => {
    expect(WARN_SHARED_LINEAGE).toBe("shared_model_lineage");
  });

  it("devuelve el texto en el idioma activo", () => {
    const data = caps({ warnings: [LINEAGE_WARNING] });
    expect(sharedLineageNotice(data, "es")).toContain("Linaje compartido");
    expect(sharedLineageNotice(data, "en")).toContain("Shared lineage");
  });

  it("empareja por `code`, no por el texto: un aviso ajeno con prosa parecida no cuenta", () => {
    const impostor: CapabilityWarning = {
      code: "otro_aviso",
      es: "Linaje compartido con alguien, pero este aviso es otro.",
      en: "Shared lineage with someone, but this is a different warning.",
    };
    expect(sharedLineageNotice(caps({ warnings: [impostor] }), "es")).toBeNull();
  });

  it("devuelve null cuando el aviso no viaja", () => {
    expect(sharedLineageNotice(caps(), "es")).toBeNull();
  });

  it("no se confunde con el aviso de agente global: coexisten", () => {
    const data = caps({ warnings: [GLOBAL_WARNING, LINEAGE_WARNING] });
    expect(sharedLineageNotice(data, "es")).toContain("Linaje compartido");
  });

  it("solo aplica a un agente: un proyecto no tiene revisor propio", () => {
    const data = caps({ entity_type: "project", warnings: [LINEAGE_WARNING] });
    expect(sharedLineageNotice(data, "es")).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// El render: sin esta mitad el aviso viajaría en el JSON y no lo vería nadie.
// ---------------------------------------------------------------------------
describe("CapabilityHub pinta el aviso de linaje", () => {
  afterEach(() => {
    cleanup();
    apiFetchMock.mockReset();
    window.localStorage.clear();
  });

  function renderHub(lang: "es" | "en", warnings: CapabilityWarning[]) {
    apiFetchMock.mockImplementation(() => Promise.resolve(caps({ warnings })));
    window.localStorage.setItem("admin-panel.lang", lang);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(
      <QueryClientProvider client={client}>
        <LanguageProvider>
          <CapabilityHub entityType="agent" entityId="a-1" />
        </LanguageProvider>
      </QueryClientProvider>,
    );
  }

  it("lo muestra en castellano cuando el backend lo emite", async () => {
    renderHub("es", [LINEAGE_WARNING]);
    const box = await screen.findByTestId("capability-hub-shared-lineage");
    expect(box.textContent).toContain("Linaje compartido");
    expect(box.textContent).toContain("QA");
  });

  it("lo muestra en inglés sin dejar castellano por debajo", async () => {
    renderHub("en", [LINEAGE_WARNING]);
    const box = await screen.findByTestId("capability-hub-shared-lineage");
    expect(box.textContent).toContain("Shared lineage");
    expect(box.textContent).not.toContain("Linaje compartido");
  });

  it("no pinta la caja cuando no hay aviso", async () => {
    renderHub("es", []);
    await screen.findByTestId("capability-hub-sections");
    expect(screen.queryByTestId("capability-hub-shared-lineage")).toBeNull();
  });
});
