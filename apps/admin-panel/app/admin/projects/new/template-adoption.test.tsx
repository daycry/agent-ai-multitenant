// @vitest-environment jsdom

/**
 * Lo que el asistente HEREDA de la plantilla — H1 y H6 del recorrido E2E
 * (`docs/roadmap/2026-08-29-hallazgos-e2e-hello-world-v2.md`).
 *
 * La causa raíz de los dos hallazgos es la MISMA y no estaba en el backend:
 * `POST /projects` ya hereda de la plantilla todo campo que el caller no envíe
 * (`_resolve_template_adoption`, PROJ-01) y ya forkea el equipo por defecto
 * cuando hay plantilla (`fork_team = template is not None`). El asistente
 * enviaba SIEMPRE los dos campos —`default_runtime_template: null` y
 * `fork_team: false`— así que `model_fields_set` los daba por elegidos y la
 * herencia del servidor no llegaba a correr nunca.
 *
 * Consecuencias medidas en la instalación viva:
 *
 *   * H1 — la plantilla «App CodeIgniter 4» declara `php-phpunit` y el proyecto
 *     nacía en «sin runtime», que NO es sin runtime: es `python-pytest`. Un
 *     proyecto PHP ejecutando `composer` en una imagen de Python.
 *   * H6 — el proyecto nacía referenciando el equipo BUILT-IN, cuyos agentes son
 *     del tenant `Platform`. Ni el chat (`team_role_agents`) ni el despacho
 *     (`Dispatcher._candidates`) los ven: los dos filtran por
 *     `Agent.tenant_id == tenant del proyecto`. El camino por defecto producía
 *     un proyecto que no podía planificar.
 *
 * Por eso los asserts miran el CUERPO de `POST /projects` y el valor del
 * desplegable: son los dos sitios donde el dato nace.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  useParams: () => ({}),
  useSearchParams: () => new URLSearchParams(),
}));

import NewProjectWizardPage from "@/app/admin/projects/new/page";
import { LanguageProvider } from "@/lib/lang-context";

const STORAGE_KEY = "admin-panel.lang";

/** Plantilla de plataforma cuyo equipo es BUILT-IN — el caso de H1 + H6. */
const CI4_TEMPLATE = {
  id: "tpl-ci4",
  name: "Plantilla: App CodeIgniter 4",
  description: "Web PHP con CodeIgniter 4.",
  status: "active",
  team_id: "team-builtin",
  default_runtime_template: "php-phpunit",
  worker_config: {},
  repository_config: null,
  human_approval_policy: null,
  is_template: true,
};

/**
 * Plantilla que declara un runtime que el catálogo NO sirve — defecto detectado
 * revisando la tanda de H1. Ver el describe del final.
 */
const STALE_RUNTIME_TEMPLATE = {
  id: "tpl-stale",
  name: "Plantilla: Runtime retirado",
  description: "Declara un runtime que ya no está en el catálogo.",
  status: "active",
  team_id: "team-tenant",
  default_runtime_template: "php-phpunit-8",
  worker_config: {},
  repository_config: null,
  human_approval_policy: null,
  is_template: true,
};

/** Plantilla cuyo equipo YA es del tenant: copiar sigue siendo opcional. */
const TENANT_TEMPLATE = {
  ...CI4_TEMPLATE,
  id: "tpl-tenant",
  name: "Plantilla: Interna",
  team_id: "team-tenant",
  default_runtime_template: null,
};

const TEAMS = [
  { id: "team-builtin", name: "CodeIgniter 4", is_builtin: true },
  { id: "team-tenant", name: "CodeIgniter 4 (copia)", is_builtin: false },
];

const RUNTIMES = [
  {
    id: "python-pytest",
    label: { es: "Python · pytest", en: "Python · pytest" },
    dep_cache_mount: null,
    network_policy: "restricted",
  },
  {
    id: "php-phpunit",
    label: { es: "PHP · PHPUnit", en: "PHP · PHPUnit" },
    dep_cache_mount: null,
    network_policy: "restricted",
  },
];

/** `runtimeCatalog: "error"` = el catálogo no contesta (no sabemos qué sirve). */
function wireApi({ runtimeCatalog = RUNTIMES }: { runtimeCatalog?: unknown } = {}) {
  apiFetchMock.mockImplementation((path: string, opts?: { method?: string; body?: unknown }) => {
    if (path === "/projects" && opts?.method === "POST") {
      return Promise.resolve({ id: "proj-new", name: "Nuevo" });
    }
    if (path.startsWith("/projects?include_templates")) {
      return Promise.resolve([CI4_TEMPLATE, TENANT_TEMPLATE, STALE_RUNTIME_TEMPLATE]);
    }
    if (path === "/teams") return Promise.resolve(TEAMS);
    if (path === "/runtime-templates") {
      return runtimeCatalog === "error"
        ? Promise.reject(new Error("catálogo caído"))
        : Promise.resolve(runtimeCatalog);
    }
    return Promise.resolve([]);
  });
}

function mount(lang: "es" | "en" = "es", opts: { runtimeCatalog?: unknown } = {}) {
  wireApi(opts);
  window.localStorage.setItem(STORAGE_KEY, lang);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LanguageProvider>
        <NewProjectWizardPage />
      </LanguageProvider>
    </QueryClientProvider>,
  );
}

/**
 * Paso 1 → 2 eligiendo una plantilla concreta.
 *
 * Espera al NOMBRE del equipo, no al testid del aviso: la vista previa pinta el
 * `team_id` crudo hasta que `/teams` resuelve, así que esperar el nombre es lo
 * único que garantiza que el `is_builtin` ya está en mano. Un `findByTestId` del
 * aviso mediría la implementación en vez de la carrera.
 */
async function pickTemplate(templateId: string, teamName: string) {
  fireEvent.click(await screen.findByTestId(`template-pick-${templateId}`));
  await screen.findByTestId("wizard-step-2");
  await screen.findByText(teamName);
}

/** El cuerpo del último `POST /projects`. */
function createBody(): Record<string, unknown> {
  const call = apiFetchMock.mock.calls
    .filter(([p, o]) => p === "/projects" && (o as { method?: string })?.method === "POST")
    .pop();
  expect(call).toBeTruthy();
  return (call?.[1] as { body: Record<string, unknown> }).body;
}

afterEach(() => {
  cleanup();
  apiFetchMock.mockReset();
  window.localStorage.clear();
});

describe("H1 — el runtime de la plantilla llega al formulario", () => {
  it("elegir la plantilla preselecciona SU runtime, no «sin runtime»", async () => {
    mount();
    await pickTemplate("tpl-ci4", "CodeIgniter 4");

    const select = screen.getByTestId("wizard-runtime-select") as HTMLSelectElement;
    expect(select.value).toBe("php-phpunit");

    fireEvent.click(screen.getByTestId("wizard-submit"));
    await waitFor(() => expect(createBody().default_runtime_template).toBe("php-phpunit"));
  });

  it("sigue siendo editable y «sin runtime» sigue siendo elegible a propósito", async () => {
    mount();
    await pickTemplate("tpl-ci4", "CodeIgniter 4");

    const select = screen.getByTestId("wizard-runtime-select") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "" } });
    expect(select.value).toBe("");

    fireEvent.click(screen.getByTestId("wizard-submit"));
    await waitFor(() => expect(createBody().default_runtime_template).toBeNull());
  });

  it("una plantilla sin runtime declarado deja el desplegable vacío", async () => {
    mount();
    await pickTemplate("tpl-tenant", "CodeIgniter 4 (copia)");

    expect((screen.getByTestId("wizard-runtime-select") as HTMLSelectElement).value).toBe("");
  });
});

describe("H6 — un equipo built-in no puede quedarse referenciado", () => {
  it("con equipo built-in la copia va marcada y NO se puede desmarcar", async () => {
    mount();
    await pickTemplate("tpl-ci4", "CodeIgniter 4");

    const box = screen.getByTestId("wizard-fork-team-checkbox") as HTMLInputElement;
    expect(box.checked).toBe(true);
    expect(box.disabled).toBe(true);
    expect(screen.getByTestId("wizard-fork-team-required")).toBeTruthy();

    fireEvent.click(screen.getByTestId("wizard-submit"));
    await waitFor(() => expect(createBody().fork_team).toBe(true));
  });

  it("con un equipo DEL TENANT la copia va marcada pero se puede desmarcar", async () => {
    mount();
    await pickTemplate("tpl-tenant", "CodeIgniter 4 (copia)");

    const box = screen.getByTestId("wizard-fork-team-checkbox") as HTMLInputElement;
    expect(box.checked).toBe(true);
    expect(box.disabled).toBe(false);
    expect(screen.queryByTestId("wizard-fork-team-required")).toBeNull();

    fireEvent.click(box);
    fireEvent.click(screen.getByTestId("wizard-submit"));
    await waitFor(() => expect(createBody().fork_team).toBe(false));
  });

  it("el aviso de equipo de plataforma se traduce y no deja castellano debajo", async () => {
    mount("en");
    await pickTemplate("tpl-ci4", "CodeIgniter 4");

    const note = screen.getByTestId("wizard-fork-team-required");
    expect(note.textContent).toContain("platform");
    expect(note.textContent).not.toContain("plataforma");
  });

  it("en castellano el aviso explica por qué la copia es obligatoria", async () => {
    mount("es");
    await pickTemplate("tpl-ci4", "CodeIgniter 4");

    expect(screen.getByTestId("wizard-fork-team-required").textContent).toContain("plataforma");
  });
});

/**
 * El proyecto EN BLANCO tiene el mismo agujero que H9a describe en «Editar
 * proyecto»: su desplegable de equipo pinta en una lista plana los built-in de
 * plataforma y las copias del tenant. Y aquí es peor, porque este camino no
 * tiene casilla de «personalizar»: elegir un built-in deja el proyecto sin
 * agentes utilizables y sin nada que lo remedie desde esta pantalla.
 */
describe("H9a en el asistente — el desplegable del proyecto en blanco", () => {
  async function startBlank() {
    fireEvent.click(await screen.findByTestId("wizard-blank-project-pick"));
    const select = (await screen.findByTestId("wizard-team-select")) as HTMLSelectElement;
    await screen.findByRole("option", { name: "CodeIgniter 4 (copia)" });
    return select;
  }

  it("separa plataforma de tenant y no deja elegir los de plataforma", async () => {
    mount();
    const select = await startBlank();

    const groups = Array.from(select.querySelectorAll("optgroup")).map((g) => g.label);
    expect(groups).toHaveLength(2);
    expect(groups.join(" | ")).toMatch(/plataforma/i);

    const options = Array.from(select.querySelectorAll("option"));
    expect(options.find((o) => o.value === "team-builtin")?.disabled).toBe(true);
    expect(options.find((o) => o.value === "team-tenant")?.disabled).toBe(false);
  });

  it("los rótulos se traducen", async () => {
    mount("en");
    const select = await startBlank();

    const groups = Array.from(select.querySelectorAll("optgroup")).map((g) => g.label);
    expect(groups.join(" | ")).toMatch(/platform/i);
    expect(groups.join(" | ")).not.toMatch(/plataforma/i);
  });
});

/**
 * El runtime que la plantilla declara pero el catálogo NO sirve — defecto
 * detectado revisando la tanda de H1.
 *
 * `pickTemplate` hace `setRuntime(template.default_runtime_template ?? "")` sin
 * mirar el catálogo. Si ese id no está entre las opciones, el `<select>` se
 * queda **sin nada seleccionado**: el operador ve un desplegable en blanco —
 * indistinguible de «— Sin runtime por defecto —» — y el formulario envía un id
 * que nadie ha visto. Es H1 al revés y con el mismo desenlace: «sin runtime» NO
 * es sin runtime, es `DEFAULT_RUN_RUNTIME_ID` = `python-pytest`.
 *
 * Lo que NO puede hacer el arreglo es caer a `""` por su cuenta: eso sería
 * elegir por el operador el valor peligroso. La regla del ADR 0162 aplica tal
 * cual — «un valor ausente no puede significar nada más fuerte que
 * desconocido»—, así que el id se conserva, se enseña marcado como no
 * disponible y se avisa. Decide el operador.
 */
describe("El runtime de la plantilla que no está en el catálogo", () => {
  it("conserva el id, lo enseña y avisa en vez de dejar el desplegable en blanco", async () => {
    mount();
    await pickTemplate("tpl-stale", "CodeIgniter 4 (copia)");

    const select = screen.getByTestId("wizard-runtime-select") as HTMLSelectElement;
    // 1) No se pierde ni se sustituye por «sin runtime».
    expect(select.value).toBe("php-phpunit-8");
    // 2) Y se VE: hay una opción para él, así que el desplegable no está en blanco.
    const shown = Array.from(select.querySelectorAll("option")).find(
      (o) => o.value === "php-phpunit-8",
    );
    expect(shown).toBeTruthy();
    // 3) Con el aviso de que el catálogo no lo sirve.
    expect(screen.getByTestId("wizard-runtime-unknown")).toBeTruthy();

    // 4) Y lo que se envía es lo que se ve.
    fireEvent.click(screen.getByTestId("wizard-submit"));
    await waitFor(() => expect(createBody().default_runtime_template).toBe("php-phpunit-8"));
  });

  it("el aviso se traduce", async () => {
    mount("en");
    await pickTemplate("tpl-stale", "CodeIgniter 4 (copia)");

    const note = screen.getByTestId("wizard-runtime-unknown");
    expect(note.textContent).toMatch(/catalog/i);
    expect(note.textContent).not.toMatch(/catálogo/i);
  });

  it("un runtime que SÍ está en el catálogo no dispara el aviso (no-regresión)", async () => {
    mount();
    await pickTemplate("tpl-ci4", "CodeIgniter 4");

    expect((screen.getByTestId("wizard-runtime-select") as HTMLSelectElement).value).toBe(
      "php-phpunit",
    );
    expect(screen.queryByTestId("wizard-runtime-unknown")).toBeNull();
  });

  it("«sin runtime» tampoco lo dispara", async () => {
    mount();
    await pickTemplate("tpl-tenant", "CodeIgniter 4 (copia)");

    expect(screen.queryByTestId("wizard-runtime-unknown")).toBeNull();
  });

  it("si el catálogo no contesta NO se acusa al runtime de no existir", async () => {
    // La misma regla del ADR 0162 una vuelta más: no saber qué sirve el catálogo
    // no es saber que ese runtime no está. Acusarlo sería inventar un fallo —
    // exactamente el falso rojo que el operador puso por delante de todo.
    mount("es", { runtimeCatalog: "error" });
    await pickTemplate("tpl-stale", "CodeIgniter 4 (copia)");

    await screen.findByTestId("wizard-runtime-error");
    expect(screen.queryByTestId("wizard-runtime-unknown")).toBeNull();
    // Y el valor sigue intacto para enviarse.
    expect((screen.getByTestId("wizard-runtime-select") as HTMLSelectElement).value).toBe(
      "php-phpunit-8",
    );
  });
});
