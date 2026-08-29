/**
 * La mitad PURA del formulario de edición de tarea: qué se considera inválido y
 * qué viaja en el PUT.
 *
 * Vive separada del diálogo por la misma razón que `lib/acceptance-criteria.ts`
 * lo está de `task-detail-sheet.tsx`: las dos reglas que aquí se afirman son
 * espejo del servidor (`api_server.schemas.tasks.TaskUpdateRequest`), y un
 * espejo hay que poder compararlo sin montar un árbol de React.
 */

import { describe, expect, it } from "vitest";

import {
  buildTaskPatch,
  formFromTask,
  RETRIES_MAX,
  RETRIES_MIN,
  TITLE_MAX,
  validateTaskForm,
  type EditableTask,
} from "@/components/tasks/task-edit-form";

const TASK: EditableTask = {
  title: "Migrar el esquema",
  description: "Con Alembic",
  priority: "medium",
  plan_id: "plan-1",
  estimated_complexity: "m",
  max_retries: 3,
  assigned_agent_id: "ag-1",
  reviewer_agent_id: null,
};

describe("formFromTask", () => {
  it("lleva los ocho campos editables al formulario, con «vacío» para los nulos", () => {
    expect(formFromTask(TASK)).toEqual({
      title: "Migrar el esquema",
      description: "Con Alembic",
      priority: "medium",
      planId: "plan-1",
      complexity: "m",
      maxRetries: "3",
      assignedAgentId: "ag-1",
      reviewerAgentId: "",
    });
  });
});

describe("validateTaskForm — espejo de TaskUpdateRequest", () => {
  const valid = formFromTask(TASK);

  it("no se queja de un formulario que el servidor aceptaría", () => {
    expect(validateTaskForm(valid)).toEqual([]);
  });

  it("rechaza el título vacío, que es lo que el servidor rechaza tras recortar", () => {
    // `_BASE_CONFIG` lleva `str_strip_whitespace=True`, así que para Pydantic
    // "   " ES la cadena vacía y choca contra `min_length=1`. Mandarlo sería un
    // 422 garantizado.
    expect(validateTaskForm({ ...valid, title: "   " }).map((e) => e.key)).toEqual([
      "errorTitleEmpty",
    ]);
  });

  it("rechaza el título que pasa del máximo del servidor", () => {
    const errors = validateTaskForm({ ...valid, title: "x".repeat(TITLE_MAX + 1) });
    expect(errors.map((e) => e.key)).toEqual(["errorTitleTooLong"]);
    expect(errors[0].vars).toEqual({ max: TITLE_MAX });
  });

  it("acepta el título que mide justo el máximo", () => {
    expect(validateTaskForm({ ...valid, title: "x".repeat(TITLE_MAX) })).toEqual([]);
  });

  it("rechaza unos reintentos fuera del rango [0, 20] del servidor", () => {
    for (const value of ["-1", String(RETRIES_MAX + 1)]) {
      const errors = validateTaskForm({ ...valid, maxRetries: value });
      expect(errors.map((e) => e.key)).toEqual(["errorRetriesRange"]);
      expect(errors[0].vars).toEqual({ min: RETRIES_MIN, max: RETRIES_MAX });
    }
  });

  it("acepta los dos extremos del rango", () => {
    expect(validateTaskForm({ ...valid, maxRetries: "0" })).toEqual([]);
    expect(validateTaskForm({ ...valid, maxRetries: "20" })).toEqual([]);
  });

  it("rechaza unos reintentos que no son un entero", () => {
    // `max_retries: int` en Pydantic v2 estricto-por-JSON: 3.5 es un 422, y el
    // hueco vacío ni siquiera es un número.
    for (const value of ["3.5", "", "  "]) {
      expect(validateTaskForm({ ...valid, maxRetries: value }).map((e) => e.key)).toEqual([
        "errorRetriesNotInteger",
      ]);
    }
  });

  it("acumula los dos motivos cuando los dos campos están mal", () => {
    const errors = validateTaskForm({ ...valid, title: "", maxRetries: "99" });
    expect(errors.map((e) => e.key)).toEqual(["errorTitleEmpty", "errorRetriesRange"]);
  });
});

describe("buildTaskPatch — sólo viaja lo que el operador cambió", () => {
  const form = formFromTask(TASK);

  it("no manda nada cuando no se ha tocado nada", () => {
    // El endpoint aplica `model_fields_set`: mandar los ocho campos siempre
    // pisaría lo que otro actor hubiese cambiado mientras el diálogo estaba
    // abierto. Un PUT vacío no tiene sentido, así que el diálogo ni lo ofrece.
    expect(buildTaskPatch(TASK, form)).toEqual({});
  });

  it("manda EXACTAMENTE los campos cambiados, con el título recortado", () => {
    expect(buildTaskPatch(TASK, { ...form, title: "  Otro título  ", maxRetries: "5" })).toEqual({
      title: "Otro título",
      max_retries: 5,
    });
  });

  it("manda null explícito al desasignar, que es como se vacía una columna", () => {
    // `apply_partial_update` distingue ausente (deja igual) de null (limpia).
    // Sin el null explícito, quitar el agente en la UI no quitaría nada.
    expect(
      buildTaskPatch(TASK, { ...form, assignedAgentId: "", planId: "", complexity: "" }),
    ).toEqual({
      assigned_agent_id: null,
      plan_id: null,
      estimated_complexity: null,
    });
  });

  it("una descripción vaciada viaja como null, no como cadena vacía", () => {
    expect(buildTaskPatch(TASK, { ...form, description: "   " })).toEqual({ description: null });
  });

  it("no reenvía la descripción cuando sigue estando vacía en los dos lados", () => {
    const empty: EditableTask = { ...TASK, description: null };
    expect(buildTaskPatch(empty, formFromTask(empty))).toEqual({});
  });

  it("asignar un revisor donde no había manda el id, no un hueco", () => {
    expect(buildTaskPatch(TASK, { ...form, reviewerAgentId: "ag-2" })).toEqual({
      reviewer_agent_id: "ag-2",
    });
  });
});
