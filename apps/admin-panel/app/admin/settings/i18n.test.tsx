// @vitest-environment jsdom

/**
 * Las pantallas de `settings/` migradas al diccionario (plan prod-16,
 * `task_prod16_03`).
 *
 * Cinco pantallas, en dos familias que se verifican distinto:
 *
 *   1. **Las que no dependen del registry** — `security` (alta/baja del segundo
 *      factor) y `hourly-rate` (tarifa del cálculo de coste humano). Todo su
 *      texto se conoce al compilar, así que sale del diccionario.
 *   2. **Las que SÍ dependen del registry** — `settings/page.tsx` (el índice de
 *      categorías), `settings/memories/page.tsx` y
 *      `settings/platform-defaults/page.tsx` (con su sección del modelo del
 *      córtex). Aquí el texto viene MEZCLADO: el marco (cabeceras, botones,
 *      avisos) del diccionario, y las etiquetas y descripciones de cada
 *      categoría/ajuste del backend, que las sirve bilingües en
 *      `label_es`/`label_en` y `description_es`/`description_en`
 *      (`api_server/settings_registry.py` y `platform_settings_registry.py`).
 *      Esa mitad se resuelve con `pickLang`, no con una clave.
 *
 * Las tres del grupo 2 estuvieron BLOQUEADAS por backend hasta el 2026-08-19:
 * el registry sólo servía `label_es`/`description_es`, y traducir sólo el marco
 * dejaba la pantalla mitad en inglés y mitad en castellano — exactamente el
 * fallo que prod-16 viene a cerrar. Con el par `_en` en las dos registries
 * (`require_language_pair` lo valida al importar el módulo), el bloqueo cayó.
 *
 * Por eso los fixtures de abajo traen las DOS caras: si la pantalla se quedara
 * pintando `label_es` sin mirar el idioma, los casos en inglés lo cazan.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LanguageProvider } from "@/lib/lang-context";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (path: string, init?: unknown) => apiFetchMock(path, init) };
});

import HourlyRatePage from "@/app/admin/settings/hourly-rate/page";
import MemoriesSettingsPage from "@/app/admin/settings/memories/page";
import SettingsIndexPage from "@/app/admin/settings/page";
import PlatformDefaultsPage from "@/app/admin/settings/platform-defaults/page";
import SecuritySettingsPage from "@/app/admin/settings/security/page";

const STORAGE_KEY = "admin-panel.lang";

// ---------------------------------------------------------------------------
// Fixtures del registry — espejo de `registry_to_dict()` y
// `platform_registry_to_dict()`, con las dos caras de cada texto.
// ---------------------------------------------------------------------------

const TENANT_REGISTRY = {
  categories: {
    memories: {
      label_es: "Memorias",
      label_en: "Memories",
      icon: "Brain",
      description_es: "Cómo el sistema detecta memorias semánticamente similares.",
      description_en: "How the system spots semantically similar memories.",
      external_page: null,
      settings: {
        "similarity.threshold": {
          type: "float",
          default: 0.85,
          label_es: "Umbral de similitud",
          label_en: "Similarity threshold",
          description_es: "Similitud coseno mínima para considerar dos memorias duplicadas.",
          description_en: "Minimum cosine similarity for two memories to count as duplicates.",
          min_value: 0.5,
          max_value: 0.99,
        },
        "similarity.limit": {
          type: "int",
          default: 5,
          label_es: "Número de candidatos",
          label_en: "Candidate count",
          description_es: "Número máximo de candidatos devueltos por memoria.",
          description_en: "Maximum number of candidates returned per memory.",
          min_value: 1,
          max_value: 20,
        },
      },
    },
    costs: {
      label_es: "Costes",
      label_en: "Costs",
      icon: "Coins",
      description_es: "Tarifa horaria del tenant para el coste humano de los planes.",
      description_en: "The tenant's hourly rate, used to work out the human cost of a plan.",
      external_page: "/admin/settings/hourly-rate",
      settings: {},
    },
  },
};

const PLATFORM_REGISTRY = {
  categories: {
    ejecucion: {
      label_es: "Ejecución",
      label_en: "Execution",
      icon: "Gauge",
      description_es: "Límites y reintentos de las ejecuciones de agentes.",
      description_en: "Limits and retries for agent runs.",
      settings: {
        max_review_retries: {
          type: "int",
          default: 3,
          label_es: "Reintentos máximos de revisión",
          label_en: "Maximum review retries",
          description_es: "Cuántas veces un agente puede reworkear su salida tras un reject.",
          description_en: "How many times an agent may rework its output after a reject.",
          min_value: 0,
          max_value: 10,
        },
      },
    },
    mantenimiento: {
      label_es: "Mantenimiento",
      label_en: "Maintenance",
      icon: "Wrench",
      description_es: "Barridos periódicos de la plataforma.",
      description_en: "Periodic platform sweeps.",
      settings: {
        approvals_expiry_sweep_enabled: {
          type: "bool",
          default: true,
          label_es: "Barrido de caducidad",
          label_en: "Expiry sweep",
          description_es: "Si el barrido periódico caduca las aprobaciones vencidas.",
          description_en: "Whether the periodic sweep expires overdue approvals.",
          min_value: null,
          max_value: null,
        },
      },
    },
    modelos: {
      label_es: "Modelos",
      label_en: "Models",
      icon: "Cpu",
      description_es: "Modelo que heredan los agentes que no fijan uno propio.",
      description_en: "The model agents inherit when they do not pin one of their own.",
      settings: {
        "model.default_config": {
          type: "model_config",
          default: {},
          label_es: "Modelo por defecto de agentes",
          label_en: "Default agent model",
          description_es: "Proveedor + modelo + temperatura que heredan los agentes.",
          description_en: "Provider + model + temperature inherited by agents.",
          min_value: null,
          max_value: null,
        },
      },
    },
  },
};

const SYSTEM_OWNER = {
  user_id: "u-1",
  email: "owner@example.com",
  full_name: "Owner",
  is_system_admin: true,
  is_system_owner: true,
  memberships: [],
  active_tenant_id: null,
};

function routeApi(path: string): unknown {
  if (path === "/auth/mfa/totp") {
    return { enrolled: false, confirmed: false, recovery_codes_remaining: 0 };
  }
  if (path === "/auth/mfa/totp/enroll") {
    return {
      secret: "JBSWY3DPEHPK3PXP",
      provisioning_uri: "otpauth://totp/demo",
      recovery_codes: ["aaaa-1111"],
    };
  }
  if (path === "/tenant-settings/hourly-rate") {
    return { hourly_rate: "50.00", hourly_rate_currency: "EUR" };
  }
  if (path === "/tenant-settings/_registry") return TENANT_REGISTRY;
  if (path === "/tenant-settings/memories") {
    return [
      { category: "memories", key: "similarity.threshold", value: 0.85, is_default: true },
      { category: "memories", key: "similarity.limit", value: 5, is_default: true },
    ];
  }
  if (path.startsWith("/tenant-settings/memories/")) {
    const key = path.slice("/tenant-settings/memories/".length);
    return { category: "memories", key, value: 1, is_default: false };
  }
  // Sonda de honestidad: con embedding, para que el detector NO salga
  // "No disponible aún" y los controles se puedan leer.
  if (path === "/memories?limit=200") return [{ has_embedding: true }];
  if (path === "/me") return SYSTEM_OWNER;
  if (path === "/admin/platform-settings/_registry") return PLATFORM_REGISTRY;
  if (path === "/admin/platform-settings") {
    return [
      { key: "max_review_retries", value: 3, is_default: true },
      { key: "approvals_expiry_sweep_enabled", value: true, is_default: true },
      { key: "model.default_config", value: {}, is_default: true },
    ];
  }
  if (path === "/agents/provider-options") {
    return { providers: [{ id: "p-1", kind: "ollama", display_name: "Local", models: ["qwen3"] }] };
  }
  if (path === "/owner/cortex/model-options") {
    return {
      providers: [
        {
          provider_id: "p-1",
          kind: "ollama",
          slug: "local",
          display_name: "Local",
          models: ["qwen3"],
        },
      ],
      reasoning_by_kind: { ollama: ["off", "high"] },
    };
  }
  if (path === "/owner/cortex/model") {
    return {
      provider_id: null,
      model_id: null,
      is_valid: true,
      provider_display_name: null,
      reasoning_effort: null,
    };
  }
  throw new Error(`unexpected endpoint in test: ${path}`);
}

function renderIn(lang: "es" | "en", node: React.ReactElement) {
  apiFetchMock.mockImplementation((path: string) => Promise.resolve(routeApi(path)));
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>{node}</LanguageProvider>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("settings/security — verificación en dos pasos", () => {
  it("en castellano rinde el estado apagado y los tres pasos del alta", async () => {
    renderIn("es", <SecuritySettingsPage />);

    expect(await screen.findByText("Seguridad")).toBeDefined();
    expect(screen.getAllByText("Verificación en dos pasos").length).toBeGreaterThan(0);

    fireEvent.click(await screen.findByTestId("mfa-enroll-button"));
    expect(await screen.findByText("3 · Confirma con el código de la app")).toBeDefined();
    expect(screen.getByLabelText("Código")).toBeDefined();
    expect(screen.getByRole("button", { name: "Confirmar" })).toBeDefined();
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <SecuritySettingsPage />);

    expect(await screen.findByText("Security")).toBeDefined();
    expect(screen.getAllByText("Two-step verification").length).toBeGreaterThan(0);

    fireEvent.click(await screen.findByTestId("mfa-enroll-button"));
    expect(await screen.findByText("3 · Confirm with the app's code")).toBeDefined();
    expect(screen.getByLabelText("Code")).toBeDefined();
    expect(screen.getByRole("button", { name: "Confirm" })).toBeDefined();

    expect(screen.queryByText("Seguridad")).toBeNull();
    expect(screen.queryByLabelText("Código")).toBeNull();
    expect(screen.queryByRole("button", { name: "Confirmar" })).toBeNull();
  });
});

describe("settings/hourly-rate — tarifa horaria", () => {
  it("en castellano rinde cabecera, campos y botón", async () => {
    renderIn("es", <HourlyRatePage />);

    expect(await screen.findByText("Tarifa horaria del tenant")).toBeDefined();
    expect(await screen.findByLabelText("Tarifa por hora")).toBeDefined();
    expect(screen.getByLabelText("Moneda")).toBeDefined();
    expect(screen.getByRole("button", { name: "Guardar" })).toBeDefined();
  });

  it("en inglés rinde lo mismo traducido y no deja castellano por debajo", async () => {
    renderIn("en", <HourlyRatePage />);

    expect(await screen.findByText("Tenant hourly rate")).toBeDefined();
    expect(await screen.findByLabelText("Rate per hour")).toBeDefined();
    expect(screen.getByLabelText("Currency")).toBeDefined();
    expect(screen.getByRole("button", { name: "Save" })).toBeDefined();

    expect(screen.queryByText("Tarifa horaria del tenant")).toBeNull();
    expect(screen.queryByLabelText("Moneda")).toBeNull();
    expect(screen.queryByRole("button", { name: "Guardar" })).toBeNull();
  });
});

// ---------------------------------------------------------------------------
// Grupo 2 — las tres pantallas guiadas por el registry.
// ---------------------------------------------------------------------------

describe("settings/ (índice) — categorías del registry", () => {
  it("en castellano rinde el marco y las etiquetas castellanas del registry", async () => {
    renderIn("es", <SettingsIndexPage />);

    expect(
      await screen.findByText("Configuración del tenant — agrupada por categoría."),
    ).toBeDefined();
    expect(await screen.findByText("Memorias")).toBeDefined();
    expect(
      screen.getByText("Cómo el sistema detecta memorias semánticamente similares."),
    ).toBeDefined();
    expect(screen.getByText("Costes")).toBeDefined();
    // `costs` tiene `external_page`, así que su tarjeta dice "página dedicada"
    // en vez del recuento de ajustes.
    expect(screen.getByTestId("settings-category-costs-external").textContent).toBe(
      "página dedicada",
    );
    expect(screen.getByTestId("settings-category-memories-count").textContent).toBe("2 ajustes");
  });

  it("en inglés rinde el marco traducido y el par inglés del registry", async () => {
    renderIn("en", <SettingsIndexPage />);

    expect(await screen.findByText("Tenant settings — grouped by category.")).toBeDefined();
    expect(await screen.findByText("Memories")).toBeDefined();
    expect(screen.getByText("How the system spots semantically similar memories.")).toBeDefined();
    expect(screen.getByText("Costs")).toBeDefined();
    expect(screen.getByTestId("settings-category-costs-external").textContent).toBe(
      "dedicated page",
    );
    expect(screen.getByTestId("settings-category-memories-count").textContent).toBe("2 settings");

    // Y ni una de las caras castellanas por debajo — que es lo que pasaba
    // mientras el registry sólo servía `label_es`.
    expect(screen.queryByText("Memorias")).toBeNull();
    expect(screen.queryByText("Costes")).toBeNull();
    expect(
      screen.queryByText("Cómo el sistema detecta memorias semánticamente similares."),
    ).toBeNull();
  });
});

describe("settings/memories — detector de similares", () => {
  it("en castellano rinde el marco y las etiquetas castellanas de los dos ajustes", async () => {
    renderIn("es", <MemoriesSettingsPage />);

    expect(await screen.findByText("Detector de similares")).toBeDefined();
    expect(await screen.findByText(/Umbral de similitud/)).toBeDefined();
    expect(screen.getByText("Número de candidatos")).toBeDefined();
    expect(
      screen.getByText("Similitud coseno mínima para considerar dos memorias duplicadas."),
    ).toBeDefined();
    expect(screen.getByRole("button", { name: "Guardar" })).toBeDefined();
  });

  it("en inglés rinde el marco traducido y el par inglés de los dos ajustes", async () => {
    renderIn("en", <MemoriesSettingsPage />);

    expect(await screen.findByText("Similar-memory detector")).toBeDefined();
    expect(await screen.findByText(/Similarity threshold/)).toBeDefined();
    expect(screen.getByText("Candidate count")).toBeDefined();
    expect(
      screen.getByText("Minimum cosine similarity for two memories to count as duplicates."),
    ).toBeDefined();
    expect(screen.getByRole("button", { name: "Save" })).toBeDefined();

    expect(screen.queryByText("Detector de similares")).toBeNull();
    expect(screen.queryByText("Número de candidatos")).toBeNull();
    expect(screen.queryByRole("button", { name: "Guardar" })).toBeNull();
  });

  it("el estado de guardado se traduce", async () => {
    renderIn("en", <MemoriesSettingsPage />);

    fireEvent.click(await screen.findByTestId("settings-memories-save"));

    await waitFor(() =>
      expect(screen.getByTestId("settings-memories-status").textContent).toBe("Saved"),
    );
  });

  /**
   * El color del estado se decidía con `status.startsWith("Error")` sobre el
   * MENSAJE. Traducido, el mensaje inglés empieza por "Could not", así que el
   * texto de error saldría con el color de un guardado correcto: un fallo que no
   * rompe nada y se ve en producción. Este caso fija el discriminante, no el
   * prefijo — con el PUT rechazado el mensaje va en inglés Y con clase de error.
   */
  it("el error de guardado se traduce y se colorea por el caso, no por el prefijo", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path.startsWith("/tenant-settings/memories/")) {
        return Promise.reject(new Error("boom"));
      }
      return Promise.resolve(routeApi(path));
    });
    window.localStorage.setItem(STORAGE_KEY, "en");
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <LanguageProvider>
          <MemoriesSettingsPage />
        </LanguageProvider>
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByTestId("settings-memories-save"));

    const status = await waitFor(() => {
      const node = screen.getByTestId("settings-memories-status");
      expect(node.textContent).toBe("Could not save: boom");
      return node;
    });
    expect(status.className).toContain("text-danger-soft-foreground");
  });
});

describe("settings/platform-defaults — valores por defecto de plataforma", () => {
  it("en castellano rinde el marco y las etiquetas castellanas del registry", async () => {
    renderIn("es", <PlatformDefaultsPage />);

    expect(await screen.findByText("Valores por defecto de plataforma")).toBeDefined();
    expect(await screen.findByText("Ejecución")).toBeDefined();
    expect(screen.getByText("Límites y reintentos de las ejecuciones de agentes.")).toBeDefined();
    expect(screen.getByText("Reintentos máximos de revisión")).toBeDefined();
    expect(screen.getByText("Barrido de caducidad")).toBeDefined();
    expect(screen.getByText("Activado")).toBeDefined();
    expect(screen.getAllByRole("button", { name: "Guardar" }).length).toBeGreaterThan(0);
    // El selector de modelo por defecto de agentes.
    expect(screen.getByText("Temperatura")).toBeDefined();
  });

  it("en inglés rinde el marco traducido y el par inglés del registry", async () => {
    renderIn("en", <PlatformDefaultsPage />);

    expect(await screen.findByText("Platform defaults")).toBeDefined();
    expect(await screen.findByText("Execution")).toBeDefined();
    expect(screen.getByText("Limits and retries for agent runs.")).toBeDefined();
    expect(screen.getByText("Maximum review retries")).toBeDefined();
    expect(screen.getByText("Expiry sweep")).toBeDefined();
    expect(screen.getByText("Enabled")).toBeDefined();
    expect(screen.getAllByRole("button", { name: "Save" }).length).toBeGreaterThan(0);
    expect(screen.getByText("Temperature")).toBeDefined();

    expect(screen.queryByText("Ejecución")).toBeNull();
    expect(screen.queryByText("Reintentos máximos de revisión")).toBeNull();
    expect(screen.queryByText("Activado")).toBeNull();
    expect(screen.queryByText("Temperatura")).toBeNull();
  });

  it("la sección del modelo del córtex también se traduce (la ve sólo el System Owner)", async () => {
    renderIn("en", <PlatformDefaultsPage />);

    expect(await screen.findByText("Cortex model")).toBeDefined();
    // El cuerpo de la sección llega con las dos queries del córtex, así que se
    // espera por él y no por el título (que es estático y ya está montado).
    expect(
      await screen.findByText(
        "No model configured. The cortex will not answer until you pick one.",
      ),
    ).toBeDefined();
    expect(screen.getByLabelText("Provider")).toBeDefined();
    expect(screen.getByRole("button", { name: "Save model" })).toBeDefined();

    expect(screen.queryByText("Modelo del córtex")).toBeNull();
    expect(screen.queryByRole("button", { name: "Guardar modelo" })).toBeNull();
  });

  it("en castellano la sección del córtex sigue en castellano", async () => {
    renderIn("es", <PlatformDefaultsPage />);

    expect(await screen.findByText("Modelo del córtex")).toBeDefined();
    expect(
      await screen.findByText(
        "Sin modelo configurado. El córtex no responderá hasta que elijas uno.",
      ),
    ).toBeDefined();
    expect(screen.getByRole("button", { name: "Guardar modelo" })).toBeDefined();
  });
});
