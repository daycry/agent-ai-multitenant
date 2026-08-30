// `task_wf_42`: las reglas del editor del spec que merecen un test propio.

import { describe, expect, it } from "vitest";

import { ApiError } from "@/lib/api";
import {
  describeSaveError,
  localSpecProblems,
  nextTaskId,
  removeTask,
  specEditable,
  toDrafts,
  toTaskSpecs,
  type TaskDraft,
} from "@/lib/plan-spec-edit";

function drafts(): TaskDraft[] {
  return toDrafts([
    { id: "t1", title: "Migrar el esquema", depends_on: [] },
    { id: "t2", title: "Cargar los datos", depends_on: ["t1"] },
    { id: "t3", title: "Verificar", depends_on: ["t2"] },
  ]);
}

describe("specEditable", () => {
  it("only offers the editor before anyone has signed the plan", () => {
    expect(specEditable("draft")).toBe(true);
    expect(specEditable("pending_approval")).toBe(true);
    // `in_progress` lo acepta el BACKEND (la replanificación en caliente ya
    // existe por esa vía, la gobierna el ADR 0132), pero ofrecer el editor ahí
    // insinuaría que replanificar es un gesto resuelto.
    for (const status of [
      "in_progress",
      "pending_second_approval",
      "approved",
      "blocked",
      "completed",
      "cancelled",
    ]) {
      expect(specEditable(status)).toBe(false);
    }
    expect(specEditable(undefined)).toBe(false);
  });
});

describe("round-trip del spec", () => {
  it("keeps the fields the editor does not touch", () => {
    // ADR 0107 marca las tareas nacidas de un rechazo con `origin`. Perderlo al
    // editar el título borraría de dónde vino la tarea.
    const [row] = toDrafts([{ id: "t1", title: "T", origin: "correction" }]);
    expect(toTaskSpecs([{ ...row, title: "T2" }])[0]).toMatchObject({
      id: "t1",
      title: "T2",
      origin: "correction",
    });
  });

  it("omits empty fields instead of persisting blanks", () => {
    const [row] = toDrafts([{ id: "t1", title: "T" }]);
    const spec = toTaskSpecs([row])[0];
    // Un `role: ""` guardado se pinta como un rol asignado en blanco.
    expect("role" in spec).toBe(false);
    expect("estimated_hours" in spec).toBe(false);
    expect("depends_on" in spec).toBe(false);
  });

  it("splits the acceptance criteria by line and drops the empty ones", () => {
    const [row] = toDrafts([{ id: "t1", title: "T" }]);
    const spec = toTaskSpecs([
      { ...row, criteria: "  Devuelve 200 \n\n  Registra el intento " },
    ])[0];
    expect(spec.acceptance_criteria).toEqual(["Devuelve 200", "Registra el intento"]);
  });
});

describe("removeTask", () => {
  it("also prunes every dependency on the removed task", () => {
    // Sin la poda el backend contesta «depends on unknown task», que es cierto
    // pero incomprensible: el operador borró una tarea, no una dependencia.
    const rows = drafts();
    const left = removeTask(rows, rows[1].key);
    expect(left.map((r) => r.id)).toEqual(["t1", "t3"]);
    expect(left[1].dependsOn).toEqual([]);
  });
});

describe("nextTaskId", () => {
  it("does not reuse an id that is already taken", () => {
    const rows = toDrafts([
      { id: "t1", title: "A" },
      { id: "t4", title: "B" },
    ]);
    expect(nextTaskId(rows)).toBe("t3");
    expect(nextTaskId([...rows, { ...rows[0], key: 9, id: "t3" }])).toBe("t5");
  });
});

describe("localSpecProblems", () => {
  it("catches what does not need a round-trip", () => {
    const rows = drafts();
    expect(localSpecProblems(rows, "es")).toEqual([]);
    expect(localSpecProblems([{ ...rows[0], title: "  " }], "es")).toContain(
      "La tarea «t1» no tiene título.",
    );
    expect(localSpecProblems([rows[0], { ...rows[1], id: "t1" }], "es")).toContain(
      "El identificador «t1» está repetido.",
    );
    expect(localSpecProblems([{ ...rows[0], id: "" }], "es")).toContain(
      "Toda tarea necesita un identificador.",
    );
  });

  /*
   * Estos mensajes se PINTAN en el editor del spec, y este módulo es puro: no
   * lo ve el guard de atributos ni el de ternarios, así que llevaban en
   * castellano fijo mientras la pantalla que los muestra ya estaba migrada
   * (prod-16 `task_prod16_03`). `lang` es obligatorio y sin default por lo
   * mismo que en `conversationLabel()`.
   */
  it("los da en el idioma activo, que es donde se leen", () => {
    const rows = drafts();
    expect(localSpecProblems([{ ...rows[0], title: "  " }], "en")).toContain(
      "Task “t1” has no title.",
    );
    expect(localSpecProblems([{ ...rows[0], id: "" }], "en")).toContain(
      "Every task needs an identifier.",
    );
  });
});

describe("describeSaveError", () => {
  it("turns a DAG cycle into the chain of titles the operator can act on", () => {
    // Lo que se pintaba antes: {"detail":{"error":"dag_cycle","cycle":["t1",...]}}.
    // `t1 → t2 → t1` tampoco dice nada a quien acaba de escribir los títulos.
    const error = new ApiError(
      422,
      JSON.stringify({ detail: { error: "dag_cycle", cycle: ["t1", "t2", "t1"] } }),
    );
    const message = describeSaveError(error, drafts(), "es");
    expect(message).toContain("t1 («Migrar el esquema»)");
    expect(message).toContain("t2 («Cargar los datos»)");
    expect(message).toContain("romper el ciclo");
  });

  it("relays the backend message when the plan no longer admits edits", () => {
    const error = new ApiError(
      409,
      JSON.stringify({
        detail: { error: "spec_not_editable", status: "approved", message: "Ya está aprobado." },
      }),
    );
    expect(describeSaveError(error, drafts(), "es")).toBe("Ya está aprobado.");
  });

  it("unwraps a pydantic 422 to its readable messages", () => {
    const error = new ApiError(
      422,
      JSON.stringify({
        detail: [{ msg: "tasks[t2] depends on unknown task 't9'", loc: ["body"] }],
      }),
    );
    expect(describeSaveError(error, drafts(), "es")).toBe("tasks[t2] depends on unknown task 't9'");
  });

  it("falls back to the raw body rather than swallowing an unknown failure", () => {
    expect(describeSaveError(new ApiError(500, "boom"), drafts(), "es")).toBe("boom");
  });

  it("traduce el ciclo, y sigue nombrando los títulos que el operador escribió", () => {
    const error = new ApiError(
      422,
      JSON.stringify({ detail: { error: "dag_cycle", cycle: ["t1", "t2", "t1"] } }),
    );
    const message = describeSaveError(error, drafts(), "en");
    expect(message).toContain("Circular dependency");
    expect(message).toContain("break the cycle");
    expect(message).toContain("t1 («Migrar el esquema»)");
  });
});

/**
 * IMPORTANTE 4 de la ola 2 del ADR 0162 — el editor del spec y el criterio
 * ESTRUCTURADO.
 *
 * Desde que `_clean_acceptance_criteria` dejó de aplanar (ola 1), un criterio
 * puede bajar del backend como diccionario con `runtime` + `command`. Este
 * editor lo trataba como `string[]`: `join("\n")` lo pintaba
 * `[object Object]` y —lo caro— al guardar volvía al spec COMO ESA CADENA,
 * borrando para siempre la única declaración ejecutable que el ADR 0162 vino a
 * fabricar. Un fallo de tipo que no se ve en pantalla como tal: se ve como que
 * el test-runtime dejó de dispararse.
 */
describe("un criterio ESTRUCTURADO sobrevive al editor del spec (ADR 0162)", () => {
  const STRUCTURED = {
    description: "La portada responde 200 y sus tests pasan",
    check_type: "automated",
    runtime: "php-phpunit",
    command: "vendor/bin/phpunit --testsuite Feature",
    expected_signal: "exit_code == 0 and tests > 0",
  };

  function rowWithStructured(): TaskDraft {
    return toDrafts([{ id: "t1", title: "T", acceptance_criteria: [STRUCTURED, "Y en prosa"] }])[0];
  }

  it("el textarea enseña el TEXTO del criterio, no su [object Object]", () => {
    expect(rowWithStructured().criteria).toBe(
      "La portada responde 200 y sus tests pasan\nY en prosa",
    );
  });

  it("y al guardar vuelve al spec con su declaración INTACTA", () => {
    // Sin esto, abrir el editor y pulsar «Guardar» sin tocar nada convertía el
    // criterio ejecutable en prosa: el worker exige `runtime` Y `command`
    // (`execution.py::_run_task_tests`), así que la tarea dejaba de ejecutar sus
    // tests sin que nada lo dijera.
    expect(toTaskSpecs([rowWithStructured()])[0].acceptance_criteria).toEqual([
      STRUCTURED,
      "Y en prosa",
    ]);
  });

  it("un criterio BORRADO del textarea no vuelve por la puerta de atrás", () => {
    const row = rowWithStructured();
    expect(toTaskSpecs([{ ...row, criteria: "Y en prosa" }])[0].acceptance_criteria).toEqual([
      "Y en prosa",
    ]);
  });

  it("una línea NUEVA sigue siendo prosa: no hereda la declaración de otra", () => {
    // Heredar por posición ataría un `command` a un criterio que nadie declaró
    // así — el «criterio fantasma» que el propio ADR prohíbe en su ola 2.
    const row = rowWithStructured();
    const spec = toTaskSpecs([
      { ...row, criteria: "Otra cosa\nLa portada responde 200 y sus tests pasan" },
    ])[0];
    expect(spec.acceptance_criteria).toEqual(["Otra cosa", STRUCTURED]);
  });
});
