// @vitest-environment jsdom
// Plan 06.16 (polyglot-tool-catalog) + Plan 06.18 punto 4, sobre la pantalla real
// "Comandos & runtime" del proyecto:
//
//   - 06.16: pulsar el preset PHP rellena los chips de la allowlist con
//     php / composer / vendor/bin/phpunit / pest. La allowlist es
//     deny-by-default (principio 2), así que el preset es el atajo que evita
//     teclear cuatro binarios a mano — y el que se rompe en silencio si alguien
//     edita STACK_PRESETS. El preset MEZCLA: nunca pisa lo que ya había.
//   - 06.18 punto 4: el selector de runtime muestra los nombres legibles que
//     sirve `GET /runtime-templates`, NUNCA el slug (`php-phpunit` es un
//     identificador, no un nombre). Ningún e2e mockeaba ese endpoint.

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: "proj-1" }),
  usePathname: () => "/admin/projects/proj-1/commands",
}));

vi.mock("@/lib/use-current-user", () => ({
  useCurrentUser: () => ({
    isSystemAdmin: false,
    isSystemOwner: false,
    isTenantAdmin: true,
    isTenantMember: true,
    isLoading: false,
  }),
}));

import { LanguageProvider } from "@/lib/lang-context";
import ProjectCommandsPage from "@/app/admin/projects/[id]/commands/page";

const RUNTIME_TEMPLATES = [
  {
    id: "php-phpunit",
    label: { es: "PHP · PHPUnit", en: "PHP · PHPUnit" },
    dep_cache_mount: "/composer",
    network_policy: "restricted",
  },
  {
    id: "node-jest",
    label: { es: "Node · Jest", en: "Node · Jest" },
    dep_cache_mount: "/node_modules",
    network_policy: "restricted",
  },
];

function project(overrides: Record<string, unknown> = {}) {
  return {
    id: "proj-1",
    name: "Proyecto Demo",
    allowed_commands: [] as string[],
    default_runtime_template: null,
    allowed_domains: [] as string[],
    ...overrides,
  };
}

function wireApi(proj = project(), runtimes: unknown[] = RUNTIME_TEMPLATES) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/projects/proj-1" && opts?.method === "PUT") {
      return Promise.resolve({ ...proj, ...(opts.body as Record<string, unknown>) });
    }
    if (path === "/projects/proj-1") return Promise.resolve(proj);
    if (path === "/runtime-templates") return Promise.resolve(runtimes);
    return Promise.resolve([]);
  });
}

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <ProjectCommandsPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

// Los `waitFor` de este fichero esperan transiciones de TanStack Query. El
// timeout por defecto de RTL (1s) se queda corto cuando la suite corre entera en
// paralelo y la máquina va cargada: se vio un rojo fantasma así. Se sube aquí
// (por fichero) en vez de tocar la config compartida.
configure({ asyncUtilTimeout: 5000 });

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
});

describe("Comandos & runtime — presets por stack (06.16)", () => {
  it("el preset PHP rellena los chips php/composer/vendor/bin/phpunit/pest", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("commands-presets")).toBeTruthy());
    // Deny-by-default: se arranca sin nada autorizado.
    expect(screen.getByTestId("commands-empty")).toBeTruthy();

    fireEvent.click(screen.getByTestId("commands-preset-php"));

    await waitFor(() => expect(screen.getByTestId("commands-chips")).toBeTruthy());
    for (const cmd of ["php", "composer", "vendor/bin/phpunit", "pest"]) {
      expect(screen.getByTestId(`command-chip-${cmd}`)).toBeTruthy();
    }
    // Y solo esos cuatro: un preset que arrastre binarios de otro stack amplía
    // la superficie sin que el operador lo haya pedido.
    expect(screen.getByTestId("commands-chips").querySelectorAll("li")).toHaveLength(4);
    expect(screen.queryByTestId("commands-empty")).toBeNull();
  });

  it("el preset MEZCLA con lo que ya había, sin duplicar ni pisar", async () => {
    wireApi(project({ allowed_commands: ["composer", "git"] }));
    mount();
    await waitFor(() => expect(screen.getByTestId("commands-preset-php")).toBeTruthy());
    fireEvent.click(screen.getByTestId("commands-preset-php"));

    await waitFor(() =>
      expect(screen.getByTestId("commands-chips").querySelectorAll("li")).toHaveLength(5),
    );
    // `git` no estaba en el preset y sobrevive; `composer` no se duplica.
    expect(screen.getByTestId("command-chip-git")).toBeTruthy();
    const chips = Array.from(screen.getByTestId("commands-chips").querySelectorAll("li")).map(
      (li) => li.textContent ?? "",
    );
    expect(chips.filter((c) => c.includes("composer"))).toHaveLength(1);
  });

  it("guardar manda la allowlist entera en el PUT del proyecto", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("commands-preset-php")).toBeTruthy());
    fireEvent.click(screen.getByTestId("commands-preset-php"));
    await waitFor(() =>
      expect((screen.getByTestId("commands-save-button") as HTMLButtonElement).disabled).toBe(
        false,
      ),
    );
    fireEvent.click(screen.getByTestId("commands-save-button"));
    await waitFor(() => {
      const put = apiFetchMock.mock.calls.find(
        ([p, o]) => p === "/projects/proj-1" && (o as { method?: string })?.method === "PUT",
      );
      expect((put?.[1] as { body: { allowed_commands: string[] } }).body.allowed_commands).toEqual([
        "php",
        "composer",
        "vendor/bin/phpunit",
        "pest",
      ]);
    });
  });

  it("ofrece un preset por stack soportado (la lista no se quedó en uno)", async () => {
    wireApi();
    mount();
    await waitFor(() => expect(screen.getByTestId("commands-presets")).toBeTruthy());
    const buttons = screen.getByTestId("commands-presets").querySelectorAll("button");
    // Guarda de inventario: si alguien vacía STACK_PRESETS, esto se cae en vez
    // de pasar en vacío.
    expect(buttons.length).toBeGreaterThanOrEqual(4);
    for (const key of ["php", "node", "dotnet", "python"]) {
      expect(screen.getByTestId(`commands-preset-${key}`)).toBeTruthy();
    }
  });
});

describe("Comandos & runtime — el selector de runtime enseña nombres, no slugs (06.18 §4)", () => {
  it("cada opción del selector muestra la etiqueta servida, nunca el id", async () => {
    wireApi();
    mount();
    const select = await waitFor(() => {
      const el = screen.getByTestId("commands-runtime-select") as HTMLSelectElement;
      // 1 placeholder + 2 plantillas: esperar a que el catálogo llegue.
      expect(el.options.length).toBe(3);
      return el;
    });
    const real = Array.from(select.options).filter((o) => o.value !== "");
    expect(real.map((o) => o.value)).toEqual(["php-phpunit", "node-jest"]);
    // El texto es el nombre legible; el slug queda SOLO en el value.
    expect(real.map((o) => o.textContent)).toEqual(["PHP · PHPUnit", "Node · Jest"]);
    for (const opt of real) {
      expect(opt.textContent).not.toBe(opt.value);
    }
  });

  it("el placeholder explica el fallback por-tool en vez de dejarlo en blanco", async () => {
    wireApi();
    mount();
    const select = await waitFor(() => {
      const el = screen.getByTestId("commands-runtime-select") as HTMLSelectElement;
      expect(el.options.length).toBe(3);
      return el;
    });
    expect(select.options[0].value).toBe("");
    expect(select.options[0].textContent).toContain("Sin runtime por defecto");
  });

  it("si el catálogo falla lo dice, en vez de ofrecer un desplegable vacío", async () => {
    apiFetchMock.mockImplementation((path: string) => {
      if (path === "/projects/proj-1") return Promise.resolve(project());
      if (path === "/runtime-templates") return Promise.reject(new Error("boom"));
      return Promise.resolve([]);
    });
    mount();
    await waitFor(() => expect(screen.getByTestId("commands-runtime-error")).toBeTruthy());
  });
});
