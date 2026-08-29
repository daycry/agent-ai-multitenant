import { describe, expect, it } from "vitest";

import {
  DEFAULT_EXPECTED_SIGNAL,
  MAX_ACCEPTANCE_CRITERIA,
  MAX_CRITERION_LEN,
  cleanCriteria,
  criteriaCheckSummary,
  criterionCheckState,
  criterionErrors,
  criterionText,
  draftFromCriterion,
  newCheck,
} from "@/lib/acceptance-criteria";

describe("criterionText", () => {
  it("returns a plain string criterion verbatim", () => {
    expect(criterionText("composer audit limpio")).toBe("composer audit limpio");
  });

  it("flattens a structured criterion to its description/text/criterion/name", () => {
    expect(criterionText({ description: "endpoint responde 200" })).toBe("endpoint responde 200");
    expect(criterionText({ text: "lock fija versiones" })).toBe("lock fija versiones");
    expect(criterionText({ criterion: "sin vulnerabilidades" })).toBe("sin vulnerabilidades");
    expect(criterionText({ name: "tests en verde" })).toBe("tests en verde");
  });

  it("degrades an unknown-shaped object to JSON rather than throwing", () => {
    expect(criterionText({ weird: 42 })).toContain("42");
  });
});

describe("cleanCriteria", () => {
  it("trims each criterion and drops empty / whitespace-only rows", () => {
    const out = cleanCriteria([
      { text: "  un criterio  ", original: null },
      { text: "   ", original: null },
      { text: "", original: null },
      { text: "otro", original: null },
    ]);
    expect(out).toEqual(["un criterio", "otro"]);
  });

  it("emits plain strings for new rows and string-backed rows", () => {
    const out = cleanCriteria([
      { text: "nuevo", original: null },
      { text: "editado", original: "viejo" },
    ]);
    expect(out).toEqual(["nuevo", "editado"]);
  });

  it("preserves a structured (dict) criterion, overwriting only its description text", () => {
    const out = cleanCriteria([
      {
        text: "texto editado",
        original: { id: "c1", kind: "manual", check_type: "descriptive", description: "viejo" },
      },
    ]);
    expect(out).toEqual([
      { id: "c1", kind: "manual", check_type: "descriptive", description: "texto editado" },
    ]);
  });

  it("drops a structured criterion whose text was cleared", () => {
    const out = cleanCriteria([{ text: "   ", original: { id: "c1", description: "viejo" } }]);
    expect(out).toEqual([]);
  });

  it("caps the number of criteria", () => {
    const drafts = Array.from({ length: MAX_ACCEPTANCE_CRITERIA + 3 }, (_, i) => ({
      text: `criterio ${i}`,
      original: null,
    }));
    expect(cleanCriteria(drafts)).toHaveLength(MAX_ACCEPTANCE_CRITERIA);
  });

  it("caps the length of each criterion", () => {
    const long = "x".repeat(MAX_CRITERION_LEN + 50);
    const out = cleanCriteria([{ text: long, original: null }]);
    expect(out).toEqual([long.slice(0, MAX_CRITERION_LEN)]);
  });

  it("treats an array original as not-structured (emits a string)", () => {
    // An array is an object in JS but is not a structured criterion; never wrap.
    const out = cleanCriteria([{ text: "txt", original: ["a", "b"] }]);
    expect(out).toEqual(["txt"]);
  });
});

// ---------------------------------------------------------------------------
// ADR 0162 — que un humano pueda declarar CÓMO se comprueba un criterio.
//
// Hasta hoy una fila nueva emitía SIEMPRE una cadena, y el worker sólo ejecuta
// los criterios que son un dict con `runtime` y `command`
// (`execution.py::_run_task_tests`). O sea: no existía ningún camino humano
// para declarar que una tarea se verifica ejecutando algo.
// ---------------------------------------------------------------------------

describe("newCheck", () => {
  it("una declaración recién abierta por un humano nace MANUAL, no automática", () => {
    // El default implícito del worker es `automated` (test_runtime.py:200) y es
    // justo el defecto que el ADR 0162 denuncia: un valor ausente que significa
    // algo más fuerte que «desconocido». Quien despliega la fila todavía no ha
    // dicho nada, así que lo que se le presupone es lo que NO da un verde.
    expect(newCheck().checkType).toBe("manual");
    expect(newCheck().expectedSignal).toBe(DEFAULT_EXPECTED_SIGNAL);
  });
});

describe("cleanCriteria · criterios con forma", () => {
  it("una fila NUEVA en modo automático emite un DICT con runtime y command", () => {
    const out = cleanCriteria([
      {
        text: "la suite unitaria pasa",
        original: null,
        check: {
          checkType: "automated",
          runtime: "php-phpunit",
          command: "vendor/bin/phpunit --testsuite Unit",
          expectedSignal: DEFAULT_EXPECTED_SIGNAL,
          manualReason: "",
        },
      },
    ]);
    expect(out).toEqual([
      {
        description: "la suite unitaria pasa",
        check_type: "automated",
        runtime: "php-phpunit",
        command: "vendor/bin/phpunit --testsuite Unit",
        expected_signal: "exit_code == 0",
      },
    ]);
  });

  it("una fila manual emite el motivo, y ni runtime ni command", () => {
    const out = cleanCriteria([
      {
        text: "el PDF generado se ve bien",
        original: null,
        check: {
          checkType: "manual",
          runtime: "php-phpunit",
          command: "vendor/bin/phpunit",
          expectedSignal: DEFAULT_EXPECTED_SIGNAL,
          manualReason: "hay que mirarlo a ojo",
        },
      },
    ]);
    // Los campos del OTRO modo se retiran: un criterio manual que conserva el
    // comando de cuando fue automático deja dos declaraciones contradictorias
    // dentro del mismo objeto, y quien lo lea después no sabe cuál manda.
    expect(out).toEqual([
      {
        description: "el PDF generado se ve bien",
        check_type: "manual",
        manual_reason: "hay que mirarlo a ojo",
      },
    ]);
  });

  it("una fila de prosa pura sigue emitiendo una cadena (no-regresión)", () => {
    const out = cleanCriteria([{ text: "el endpoint responde 200", original: null }]);
    expect(out).toEqual(["el endpoint responde 200"]);
  });

  it("conserva los metadatos de un criterio automático preexistente", () => {
    // Editar sólo la descripción no puede perder `id` ni `timeout_s`. Lo que SÍ
    // se añade es el `check_type` explícito: hacer visible lo que hoy es un
    // default implícito no cambia el comportamiento, lo hace auditable.
    const out = cleanCriteria([
      {
        text: "texto editado",
        original: {
          id: "auto_01",
          runtime: "python-pytest",
          command: "pytest -q",
          timeout_s: 600,
          description: "viejo",
        },
        check: {
          checkType: "automated",
          runtime: "python-pytest",
          command: "pytest -q",
          expectedSignal: DEFAULT_EXPECTED_SIGNAL,
          manualReason: "",
        },
      },
    ]);
    expect(out).toEqual([
      {
        id: "auto_01",
        timeout_s: 600,
        description: "texto editado",
        check_type: "automated",
        runtime: "python-pytest",
        command: "pytest -q",
        expected_signal: "exit_code == 0",
      },
    ]);
  });

  it("retirar la declaración (check: null) limpia los campos que declaraban", () => {
    // `undefined` = la fila nunca pasó por el editor de declaración (se preserva
    // tal cual); `null` = el operador la retiró a propósito. Sin esa distinción,
    // «quitar la declaración» dejaba el comando puesto y el botón mentía.
    const out = cleanCriteria([
      {
        text: "sigue siendo prosa",
        original: {
          id: "auto_01",
          check_type: "automated",
          runtime: "python-pytest",
          command: "pytest -q",
        },
        check: null,
      },
    ]);
    expect(out).toEqual([{ id: "auto_01", description: "sigue siendo prosa" }]);
  });
});

describe("draftFromCriterion", () => {
  it("siembra la declaración de un criterio automático preexistente", () => {
    const draft = draftFromCriterion({
      description: "tests en verde",
      runtime: "go-test",
      command: "go test ./...",
    });
    expect(draft.check).toEqual({
      checkType: "automated",
      runtime: "go-test",
      command: "go test ./...",
      expectedSignal: DEFAULT_EXPECTED_SIGNAL,
      manualReason: "",
    });
  });

  it("NO reclama un criterio con un vocabulario de check_type que no es nuestro", () => {
    // `check_type: "descriptive"` no lo produce este editor. Reclamarlo lo
    // reescribiría a "automated"/"manual" al guardar, cambiando el significado
    // de un criterio que nadie tocó.
    const draft = draftFromCriterion({ id: "c1", check_type: "descriptive", description: "x" });
    expect(draft.check).toBeUndefined();
  });

  it("una cadena se siembra como prosa, sin declaración", () => {
    expect(draftFromCriterion("prosa").check).toBeUndefined();
  });
});

describe("criterionErrors", () => {
  it("una fila automática sin comando no se puede guardar", () => {
    expect(
      criterionErrors({
        text: "los tests pasan",
        original: null,
        check: {
          checkType: "automated",
          runtime: "python-pytest",
          command: "   ",
          expectedSignal: DEFAULT_EXPECTED_SIGNAL,
          manualReason: "",
        },
      }),
    ).toEqual(["errorCriterionCommandRequired"]);
  });

  it("una fila automática sin runtime tampoco", () => {
    expect(
      criterionErrors({
        text: "los tests pasan",
        original: null,
        check: {
          checkType: "automated",
          runtime: "",
          command: "pytest -q",
          expectedSignal: DEFAULT_EXPECTED_SIGNAL,
          manualReason: "",
        },
      }),
    ).toEqual(["errorCriterionRuntimeRequired"]);
  });

  it("una fila manual exige motivo", () => {
    // El ADR 0162 lo pide por su nombre: «el silencio deja de ser una respuesta
    // válida». Declarar algo como no-automatizable sin decir por qué es el mismo
    // hueco, sólo que firmado.
    expect(
      criterionErrors({
        text: "revisar la maqueta",
        original: null,
        check: {
          checkType: "manual",
          runtime: "",
          command: "",
          expectedSignal: DEFAULT_EXPECTED_SIGNAL,
          manualReason: "  ",
        },
      }),
    ).toEqual(["errorCriterionReasonRequired"]);
  });

  it("una fila declarada sin enunciado avisa en vez de desaparecer al guardar", () => {
    const errs = criterionErrors({
      text: "  ",
      original: null,
      check: {
        checkType: "manual",
        runtime: "",
        command: "",
        expectedSignal: DEFAULT_EXPECTED_SIGNAL,
        manualReason: "porque sí",
      },
    });
    expect(errs).toEqual(["errorCriterionTextRequired"]);
  });

  it("una fila de prosa vacía no es un error: se descarta y ya", () => {
    expect(criterionErrors({ text: "   ", original: null })).toEqual([]);
  });

  it("una fila automática completa no tiene errores", () => {
    expect(
      criterionErrors({
        text: "los tests pasan",
        original: null,
        check: {
          checkType: "automated",
          runtime: "python-pytest",
          command: "pytest -q",
          expectedSignal: DEFAULT_EXPECTED_SIGNAL,
          manualReason: "",
        },
      }),
    ).toEqual([]);
  });
});

describe("criterionCheckState", () => {
  // Espejo de `_coerce_check` + `_run_task_tests`: el rótulo describe lo que va
  // a PASAR, no lo que el objeto dice de sí mismo. Un badge que promete
  // «automático» sobre un criterio que nadie ejecuta es el mismo falso verde
  // que el ADR mide, un piso más arriba.
  it("una cadena no declara nada", () => {
    expect(criterionCheckState("prosa")).toBe("undeclared");
  });

  it("un dict con runtime y command se ejecuta de verdad", () => {
    expect(criterionCheckState({ runtime: "python-pytest", command: "pytest -q" })).toBe(
      "automated",
    );
  });

  it("un check_type no automático es manual, lo ejecute quien lo ejecute", () => {
    expect(criterionCheckState({ check_type: "manual", manual_reason: "a ojo" })).toBe("manual");
    expect(criterionCheckState({ check_type: "human", description: "revisar UI" })).toBe("manual");
  });

  it("un dict que dice «automated» pero no trae comando NO se comprueba", () => {
    // El worker lo descarta en silencio: contarlo como automático sería
    // prometer una ejecución que no ocurre.
    expect(criterionCheckState({ check_type: "automated", description: "x" })).toBe("undeclared");
  });
});

describe("criteriaCheckSummary", () => {
  it("cuenta cuántos criterios se comprueban DE VERDAD", () => {
    expect(
      criteriaCheckSummary([
        "prosa",
        { runtime: "python-pytest", command: "pytest -q" },
        { check_type: "manual", manual_reason: "a ojo" },
        { check_type: "automated", description: "sin comando" },
      ]),
    ).toEqual({ automated: 1, manual: 1, undeclared: 2, total: 4 });
  });
});
