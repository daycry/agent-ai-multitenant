// @vitest-environment jsdom

/**
 * El botón «que se proponga él»: la puerta por la que se usa la co-construcción.
 *
 * `POST /owner/cortex/identity/onboarding` se entregó el 2026-08-19 con sus
 * tests de backend, y **no tenía llamante**: el owner seguía viendo únicamente
 * el formulario manual. Es el patrón que este repo tiene documentado como
 * dominante —mecanismo entregado, cero llamantes— y por eso el arnés vive aquí,
 * en la pantalla, y no sólo en el endpoint.
 *
 * Lo que fija este fichero, que son las cuatro propiedades que hacen que la
 * pantalla no mienta:
 *
 *   1. El botón sólo aparece cuando hace falta (córtex SIN onboardar).
 *   2. Proponer NO guarda: el primer POST va sin `confirm`.
 *   3. Lo propuesto se puede LEER y EDITAR antes de aceptar — el turno literal
 *      se pinta, y la propuesta siembra el formulario.
 *   4. Aceptar manda `confirm: true` **con lo que hay en el formulario**, no con
 *      lo que propuso el modelo: si el owner edita el nombre, se guarda el suyo.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: true,
    isSystemOwner: true,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import CortexIdentityPage from "@/app/admin/cortex/identity/page";

const SIN_ONBOARDAR = {
  name: null,
  core_values: [] as string[],
  narrative: "",
  language: "es",
  learning_goals: [] as string[],
  traits: {
    openness: 0.5,
    conscientiousness: 0.5,
    extraversion: 0.5,
    agreeableness: 0.5,
    stability: 0.5,
  },
  mood_baseline: { valence: 0, arousal: 0, dominance: 0 },
  version: 0,
  updated_by: "default",
  onboarded_at: null,
  updated_at: "2026-08-19T09:00:00Z",
};

const CANDIDATO = {
  ...SIN_ONBOARDAR,
  name: "Vera",
  core_values: ["honestidad", "curiosidad"],
  narrative: "Aprendo contigo y digo lo que no sé.",
  learning_goals: ["multi-tenancy"],
};

const PROPUESTA = {
  already_onboarded: false,
  applied: false,
  proposal: "Me gustaría llamarme Vera, por la verdad. Valoro la honestidad y la curiosidad.",
  identity: CANDIDATO,
  diff: { name: { before: null, after: "Vera" } },
  honesty: {
    note_es: "Modelo computacional de identidad — no es consciencia.",
    note_en: "Computational identity model — not consciousness.",
  },
};

function mount() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <CortexIdentityPage />
    </QueryClientProvider>,
  );
}

/** Las llamadas POST al endpoint de onboarding, con su cuerpo. */
function onboardingCalls(): { body: Record<string, unknown> }[] {
  return apiFetchMock.mock.calls
    .filter(
      ([path, options]) =>
        String(path).includes("/identity/onboarding") && options?.method === "POST",
    )
    .map(([, options]) => ({ body: (options?.body ?? {}) as Record<string, unknown> }));
}

beforeEach(() => {
  configure({ testIdAttribute: "data-testid" });
  apiFetchMock.mockReset();
});

afterEach(cleanup);

describe("co-construcción de la identidad del córtex", () => {
  it("ofrece el botón cuando el córtex NO tiene identidad, y proponer no guarda nada", async () => {
    apiFetchMock.mockImplementation((path: string, options?: { method?: string }) => {
      if (String(path).includes("/identity/onboarding")) return Promise.resolve(PROPUESTA);
      if (String(path).includes("/identity/history")) return Promise.resolve([]);
      if (String(path).includes("/identity")) return Promise.resolve(SIN_ONBOARDAR);
      return Promise.resolve({});
    });

    mount();
    const boton = await screen.findByTestId("cortex-identity-propose");

    fireEvent.click(boton);

    // El turno literal se PINTA: el owner acepta lo que ha leído, no un resumen.
    await waitFor(() => expect(screen.getByTestId("cortex-identity-proposal-text")).toBeTruthy());
    expect(screen.getByTestId("cortex-identity-proposal-text").textContent).toContain("Vera");

    // Y proponer NO persiste: el primer POST va sin `confirm`.
    const llamadas = onboardingCalls();
    expect(llamadas.length).toBe(1);
    expect(llamadas[0].body.confirm).toBeUndefined();
  });

  it("aceptar guarda lo que hay en el FORMULARIO, no lo que propuso el modelo", async () => {
    apiFetchMock.mockImplementation(
      (path: string, options?: { method?: string; body?: unknown }) => {
        if (String(path).includes("/identity/onboarding")) {
          const body = (options?.body ?? {}) as { confirm?: boolean };
          return Promise.resolve(
            body.confirm ? { ...PROPUESTA, applied: true, proposal: "" } : PROPUESTA,
          );
        }
        if (String(path).includes("/identity/history")) return Promise.resolve([]);
        if (String(path).includes("/identity")) return Promise.resolve(SIN_ONBOARDAR);
        return Promise.resolve({});
      },
    );

    mount();
    fireEvent.click(await screen.findByTestId("cortex-identity-propose"));
    await waitFor(() => expect(screen.getByTestId("cortex-identity-proposal")).toBeTruthy());

    // La propuesta siembra el formulario…
    const nombre = screen.getByTestId("cortex-identity-name") as HTMLInputElement;
    await waitFor(() => expect(nombre.value).toBe("Vera"));

    // …y el owner puede cambiarla antes de aceptar.
    fireEvent.change(nombre, { target: { value: "Nadia" } });
    fireEvent.click(screen.getByTestId("cortex-identity-proposal-accept"));

    await waitFor(() => expect(onboardingCalls().length).toBe(2));
    const confirmacion = onboardingCalls()[1].body;
    expect(confirmacion.confirm).toBe(true);
    expect(confirmacion.name).toBe("Nadia");
  });

  it("descartar la propuesta la retira sin llamar al backend", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (String(path).includes("/identity/onboarding")) return Promise.resolve(PROPUESTA);
      if (String(path).includes("/identity/history")) return Promise.resolve([]);
      if (String(path).includes("/identity")) return Promise.resolve(SIN_ONBOARDAR);
      return Promise.resolve({});
    });

    mount();
    fireEvent.click(await screen.findByTestId("cortex-identity-propose"));
    await waitFor(() => expect(screen.getByTestId("cortex-identity-proposal")).toBeTruthy());

    fireEvent.click(screen.getByTestId("cortex-identity-proposal-discard"));

    await waitFor(() => expect(screen.queryByTestId("cortex-identity-proposal")).toBeNull());
    expect(onboardingCalls().length).toBe(1);
  });

  it("un córtex YA onboardado no ve el botón: no hay nada que proponer", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (String(path).includes("/identity/history")) return Promise.resolve([]);
      if (String(path).includes("/identity")) {
        return Promise.resolve({ ...CANDIDATO, onboarded_at: "2026-08-01T10:00:00Z", version: 3 });
      }
      return Promise.resolve({});
    });

    mount();
    // Espera a que la identidad aterrice antes de afirmar la AUSENCIA: sin esto
    // el test pasaría por lo que todavía no se ha pintado, no por la regla.
    await waitFor(() =>
      expect((screen.getByTestId("cortex-identity-name") as HTMLInputElement).value).toBe("Vera"),
    );
    expect(screen.queryByTestId("cortex-identity-propose")).toBeNull();
  });
});
