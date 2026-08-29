// @vitest-environment jsdom
/**
 * ADR 0162 — que un humano pueda declarar CÓMO se comprueba un criterio.
 *
 * El worker sólo ejecuta los criterios que son un dict con `runtime` y
 * `command`; el editor emitía siempre una cadena. Estos tests fijan las dos
 * mitades que cierran ese hueco: que el formulario EXISTE y emite el dict, y
 * que la ficha enseña cuántos criterios se comprueban de verdad.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

const apiFetchMock = vi.fn();
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return { ...actual, apiFetch: (...args: unknown[]) => apiFetchMock(...args) };
});

import { CriteriaSection } from "@/components/tasks/task-criteria-section";
import { dictionary } from "@/lib/i18n";
import { LANGS } from "@/lib/i18n";

const RUNTIMES = [
  {
    id: "php-phpunit",
    label: { es: "PHP · PHPUnit", en: "PHP · PHPUnit" },
    dep_cache_mount: null,
    network_policy: "restricted",
  },
  {
    id: "python-pytest",
    label: { es: "Python · pytest", en: "Python · pytest" },
    dep_cache_mount: null,
    network_policy: "restricted",
  },
];

function renderSection(criteria: unknown[]) {
  apiFetchMock.mockImplementation((path: string) => {
    if (path === "/runtime-templates") return Promise.resolve(RUNTIMES);
    return Promise.resolve({ id: "t-1", acceptance_criteria: [] });
  });
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <CriteriaSection projectId="p-1" taskId="t-1" criteria={criteria} />
    </QueryClientProvider>,
  );
}

/**
 * Espera a que `GET /runtime-templates` haya poblado el `<select>`.
 *
 * El plazo se sube de los 1000 ms que trae testing-library por defecto porque
 * **el plazo no es lo que este test afirma**: afirma que la opción aparece, no
 * que aparezca antes de un segundo. Con la máquina cargada —dos suites en
 * paralelo— la resolución de react-query más el re-render pasaban del segundo y
 * el fichero salía en rojo dos veces de cada tres, verde en aislado. Un rojo que
 * depende de lo ocupado que esté el ordenador es exactamente el falso fallo que
 * el encargo del ADR 0162 manda evitar, y encima enseña a desconfiar del rojo.
 *
 * Lo que NO se toca: la opción sigue teniendo que aparecer de verdad, y el
 * `fireEvent.change` posterior sólo prende si existe.
 */
function awaitRuntimeCatalog(): Promise<HTMLElement> {
  return screen.findByRole(
    "option",
    { name: "PHP · PHPUnit" },
    // Por debajo de los 5000 ms de `testTimeout` de vitest, para que un fallo
    // real se lea como «no apareció la opción» y no como «se acabó el test».
    { timeout: 4000 },
  );
}

/** El cuerpo del `PUT` de criterios, que es lo que acaba en la BD. */
function savedCriteria(): unknown[] {
  const call = apiFetchMock.mock.calls.find(
    ([path, init]) =>
      path === "/projects/p-1/tasks/t-1" && (init as { method?: string })?.method === "PUT",
  );
  const body = (call?.[1] as { body: { acceptance_criteria: unknown[] } }).body;
  return body.acceptance_criteria;
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CriteriaSection · declarar cómo se comprueba (ADR 0162)", () => {
  it("una fila nueva se puede declarar automática y viaja como DICT con runtime y command", async () => {
    // Este es el camino que no existía: hasta hoy esta misma secuencia emitía
    // la cadena "la suite unitaria pasa" y el worker no ejecutaba nada.
    renderSection([]);
    fireEvent.click(screen.getByTestId("task-criteria-edit"));
    fireEvent.click(screen.getByTestId("task-criterion-add"));
    fireEvent.change(screen.getByTestId("task-criterion-input"), {
      target: { value: "la suite unitaria pasa" },
    });

    fireEvent.click(screen.getByTestId("task-criterion-declare-0"));
    fireEvent.change(screen.getByTestId("task-criterion-check-type-0"), {
      target: { value: "automated" },
    });
    // El catálogo llega por `GET /runtime-templates`: sin esperar, el `<select>`
    // no tendría todavía la `<option>` y el cambio no prendería.
    await awaitRuntimeCatalog();
    fireEvent.change(screen.getByTestId("task-criterion-runtime-0"), {
      target: { value: "php-phpunit" },
    });
    fireEvent.change(screen.getByTestId("task-criterion-command-0"), {
      target: { value: "vendor/bin/phpunit --testsuite Unit" },
    });

    fireEvent.click(screen.getByTestId("task-criteria-save"));

    await waitFor(() => expect(savedCriteria()).toHaveLength(1));
    expect(savedCriteria()[0]).toEqual({
      description: "la suite unitaria pasa",
      check_type: "automated",
      runtime: "php-phpunit",
      command: "vendor/bin/phpunit --testsuite Unit",
      expected_signal: "exit_code == 0",
    });
  });

  it("una declaración recién abierta nace manual y no deja guardar sin motivo", async () => {
    renderSection([]);
    fireEvent.click(screen.getByTestId("task-criteria-edit"));
    fireEvent.click(screen.getByTestId("task-criterion-add"));
    fireEvent.change(screen.getByTestId("task-criterion-input"), {
      target: { value: "el PDF se ve bien" },
    });
    fireEvent.click(screen.getByTestId("task-criterion-declare-0"));

    // Manual por defecto: el default implícito del worker es `automated`, y ése
    // es justo el defecto que el ADR denuncia.
    expect((screen.getByTestId("task-criterion-check-type-0") as HTMLSelectElement).value).toBe(
      "manual",
    );
    expect((screen.getByTestId("task-criteria-save") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("task-criterion-errors-0").textContent).toContain(
      "por qué no se puede comprobar a máquina",
    );

    fireEvent.change(screen.getByTestId("task-criterion-reason-0"), {
      target: { value: "hay que mirarlo a ojo" },
    });
    await waitFor(() =>
      expect((screen.getByTestId("task-criteria-save") as HTMLButtonElement).disabled).toBe(false),
    );

    fireEvent.click(screen.getByTestId("task-criteria-save"));
    await waitFor(() => expect(savedCriteria()).toHaveLength(1));
    expect(savedCriteria()[0]).toEqual({
      description: "el PDF se ve bien",
      check_type: "manual",
      manual_reason: "hay que mirarlo a ojo",
    });
  });

  it("una fila automática sin comando no se puede guardar", async () => {
    renderSection([]);
    fireEvent.click(screen.getByTestId("task-criteria-edit"));
    fireEvent.click(screen.getByTestId("task-criterion-add"));
    fireEvent.change(screen.getByTestId("task-criterion-input"), {
      target: { value: "los tests pasan" },
    });
    fireEvent.click(screen.getByTestId("task-criterion-declare-0"));
    fireEvent.change(screen.getByTestId("task-criterion-check-type-0"), {
      target: { value: "automated" },
    });
    await awaitRuntimeCatalog();
    fireEvent.change(screen.getByTestId("task-criterion-runtime-0"), {
      target: { value: "php-phpunit" },
    });

    expect((screen.getByTestId("task-criteria-save") as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByTestId("task-criterion-errors-0").textContent).toContain(
      "el comando que comprueba el criterio",
    );
  });

  it("la prosa sin declarar se sigue guardando como cadena", async () => {
    renderSection([]);
    fireEvent.click(screen.getByTestId("task-criteria-edit"));
    fireEvent.click(screen.getByTestId("task-criterion-add"));
    fireEvent.change(screen.getByTestId("task-criterion-input"), {
      target: { value: "el endpoint responde 200" },
    });
    fireEvent.click(screen.getByTestId("task-criteria-save"));

    await waitFor(() => expect(savedCriteria()).toEqual(["el endpoint responde 200"]));
  });
});

describe("CriteriaSection · visibilidad de lo que se comprueba", () => {
  it("marca cada criterio y cuenta las TRES categorías por separado", () => {
    renderSection([
      "prosa suelta",
      { description: "tests unitarios", runtime: "python-pytest", command: "pytest -q" },
      { description: "revisar la maqueta", check_type: "manual", manual_reason: "a ojo" },
    ]);

    expect(screen.getByTestId("task-criterion-state-0").textContent).toBe("Sin comprobación");
    expect(screen.getByTestId("task-criterion-state-1").textContent).toBe(
      "Comprobación automática",
    );
    expect(screen.getByTestId("task-criterion-state-2").textContent).toBe("Comprobación manual");

    // Las tres cuentas por separado. «1 de 3 se comprueban solos» dejaba fuera
    // justo la distinción que el ADR 0162 pide: «declarado manual» y «nadie ha
    // declarado nada» caían los dos en el mismo «no».
    expect(screen.getByTestId("task-criteria-coverage-automated").textContent).toContain("1");
    expect(screen.getByTestId("task-criteria-coverage-manual").textContent).toContain("1");
    expect(screen.getByTestId("task-criteria-coverage-undeclared").textContent).toContain("1");
  });

  it("un criterio que dice «automated» sin comando NO se cuenta como automático", () => {
    // El worker lo descarta en silencio; pintarlo como automático sería la
    // misma promesa vacía que el ADR mide, un piso más arriba.
    renderSection([{ description: "x", check_type: "automated" }]);
    expect(screen.getByTestId("task-criterion-state-0").textContent).toBe("Sin comprobación");
    expect(screen.getByTestId("task-criteria-coverage-undeclared").textContent).toContain("1");
    expect(screen.queryByTestId("task-criteria-coverage-automated")).toBeNull();
  });

  it("distingue A SIMPLE VISTA «declarado manual» de «sin declarar»", () => {
    // No basta con que el rótulo diga cosas distintas: los dos casos caían en el
    // mismo «no se comprueba solo», y el operador no podía saber si alguien
    // había decidido algo o si nadie había dicho nada.
    renderSection([
      { description: "revisar la maqueta", check_type: "manual", manual_reason: "a ojo" },
      "prosa suelta",
    ]);
    const manual = screen.getByTestId("task-criterion-state-0");
    const undeclared = screen.getByTestId("task-criterion-state-1");
    expect(manual.textContent).not.toBe(undeclared.textContent);
    expect(manual.className).not.toBe(undeclared.className);
  });

  it("el motivo de una comprobación manual se lee sin abrir el editor", () => {
    // «Cuántos están declarados manuales CON SU MOTIVO»: el motivo es la mitad
    // que convierte «esto no se automatiza» en una decisión auditable. Estaba
    // escrito en la BD y sólo se veía abriendo el formulario de edición.
    renderSection([
      {
        description: "revisar la maqueta",
        check_type: "manual",
        manual_reason: "hay que mirar el PDF a ojo",
      },
    ]);
    expect(screen.getByTestId("task-criterion-detail-0").textContent).toContain(
      "hay que mirar el PDF a ojo",
    );
  });

  it("el comando de una comprobación automática se lee sin abrir el editor", () => {
    renderSection([
      { description: "tests unitarios", runtime: "python-pytest", command: "pytest -q" },
    ]);
    expect(screen.getByTestId("task-criterion-detail-0").textContent).toContain("pytest -q");
  });

  it("un criterio que el worker descarta NO enseña su comando como si fuera a correr", () => {
    // Declara `command` pero le falta el runtime, así que `_run_task_tests` lo
    // salta. Enseñar el comando ahí sería prometer una ejecución que no ocurre.
    renderSection([{ description: "x", check_type: "automated", command: "pytest -q" }]);
    expect(screen.queryByTestId("task-criterion-detail-0")).toBeNull();
  });
});

describe("CriteriaSection · una tarea sin nada que automatizar NO parece un fallo", () => {
  /*
   * Es la mitad del encargo que manda sobre el resto: el sistema tiene que
   * evitar fallos Y FALSOS FALLOS. Una tarea de análisis con todos sus
   * criterios declarados manuales está perfectamente bien, y pintarla en ámbar
   * —o resumirla como «0 de 3»— fabrica el falso fallo en versión visual.
   */
  it("no enseña ceros, ni avisos, ni un solo píxel de alarma", () => {
    const { container } = renderSection([
      {
        description: "el informe explica la causa raíz",
        check_type: "manual",
        manual_reason: "lo lee una persona",
      },
      {
        description: "el ADR lista las opciones descartadas",
        check_type: "manual",
        manual_reason: "lo lee una persona",
      },
    ]);

    expect(screen.getByTestId("task-criteria-coverage-manual").textContent).toContain("2");
    // Las categorías vacías NO se pintan: un «0 en automático» se lee como una
    // carencia, y aquí no falta nada.
    expect(screen.queryByTestId("task-criteria-coverage-automated")).toBeNull();
    expect(screen.queryByTestId("task-criteria-coverage-undeclared")).toBeNull();
    expect(screen.queryByTestId("task-criteria-coverage-note")).toBeNull();
    expect(screen.getByTestId("task-criteria-coverage").textContent).not.toContain("0");

    // Ni ámbar ni rojo: los tokens semánticos de aviso y de error no aparecen.
    expect(
      container.querySelector(".bg-warning-soft, .bg-danger-soft, .text-destructive"),
    ).toBeNull();
  });

  it("en cambio, sí avisa cuando hay criterios sin declarar", () => {
    // La contraparte del test de arriba: si el aviso no apareciera NUNCA, aquél
    // pasaría por construcción y no mediría nada.
    renderSection(["prosa suelta"]);
    expect(screen.getByTestId("task-criteria-coverage-note")).toBeTruthy();
  });
});

describe("i18n de la declaración", () => {
  it("todas las claves nuevas existen en ES y EN", () => {
    const keys = [
      "criterionTextLabel",
      "checkDeclare",
      "checkUndeclare",
      "checkHeading",
      "checkTypeLabel",
      "checkTypeAutomated",
      "checkTypeManual",
      "checkRuntimeLabel",
      "checkRuntimeNone",
      "checkRuntimeLoading",
      "checkRuntimeUnknown",
      "checkRuntimeError",
      "checkCommandLabel",
      "checkCommandPlaceholder",
      "checkCommandHint",
      "checkSignalLabel",
      "checkSignalHint",
      "checkReasonLabel",
      "checkReasonPlaceholder",
      "checkManualHint",
      "checkStateAutomated",
      "checkStateManual",
      "checkStateUndeclared",
      "checkUndeclaredHint",
      "coverageLabel",
      "coverageAutomated",
      "coverageManual",
      "coverageUndeclared",
      "detailCommand",
      "detailReason",
      "errorCriterionTextRequired",
      "errorCriterionRuntimeRequired",
      "errorCriterionCommandRequired",
      "errorCriterionReasonRequired",
    ] as const;
    const ns = dictionary.taskDetail as Record<string, Record<string, string> | undefined>;
    const missing: string[] = [];
    for (const key of keys) {
      for (const lang of LANGS) {
        if (!ns[key]?.[lang]?.trim()) missing.push(`taskDetail.${key}.${lang}`);
      }
    }
    expect(missing).toEqual([]);
  });
});
