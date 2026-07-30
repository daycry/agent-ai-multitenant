// `task_wf_35`: la conversión formulario↔API de los cuatro ajustes de gobierno.

import { describe, expect, it } from "vitest";

import {
  governanceProblems,
  toForm,
  toPayload,
  type GovernanceForm,
} from "@/lib/project-governance";

const EMPTY = toForm(null);

function form(overrides: Partial<GovernanceForm> = {}): GovernanceForm {
  return { ...EMPTY, ...overrides };
}

describe("toForm", () => {
  it("starts empty for a project that never configured anything", () => {
    expect(EMPTY.budgets.max_tokens).toBe("");
    expect(EMPTY.guardrailsJson).toBe("");
    expect(EMPTY.budgetPeriod).toBe("");
    // El default del backend, no una casilla en blanco que al guardar lo cambie.
    expect(EMPTY.humanTaskReviewMode).toBe("auto_approve");
  });

  it("reads back what the project has stored", () => {
    const loaded = toForm({
      execution_budgets: { max_tokens: 120000, max_cost_usd: 1.5 },
      guardrails_config: { guardrails: { pre_tool: [] } },
      human_task_review_mode: "peer_human_reviewer",
      budget_amount: "250.00",
      budget_currency: "EUR",
      budget_period: "monthly",
    });
    expect(loaded.budgets.max_tokens).toBe("120000");
    expect(loaded.budgets.max_cost_usd).toBe("1.5");
    expect(loaded.budgets.max_iterations).toBe("");
    expect(loaded.guardrailsJson).toContain("pre_tool");
    expect(loaded.budgetAmount).toBe("250.00");
  });
});

describe("toPayload", () => {
  it("sends null, not zero, for what the operator left blank", () => {
    // Mandar `0` fijaría un presupuesto de cero y ningún run arrancaría; el
    // campo en blanco significa «hereda del nivel superior».
    const payload = toPayload(EMPTY);
    expect(payload.execution_budgets).toBeNull();
    expect(payload.guardrails_config).toBeNull();
    expect(payload.budget_amount).toBeNull();
    expect(payload.budget_period).toBeNull();
  });

  it("only sends the budgets that were filled in", () => {
    const payload = toPayload(form({ budgets: { ...EMPTY.budgets, max_tokens: "80000" } }));
    expect(payload.execution_budgets).toEqual({ max_tokens: 80000 });
  });

  it("normalises the currency and drops the custom-period fields when unused", () => {
    const payload = toPayload(
      form({
        budgetAmount: "100",
        budgetCurrency: " eur ",
        budgetPeriod: "monthly",
        budgetPeriodStartDay: "5",
        budgetPeriodLengthDays: "30",
      }),
    );
    expect(payload.budget_currency).toBe("EUR");
    // El backend rechaza start_day/length fuera de un periodo `custom`; mandarlos
    // sería un 422 por algo que el formulario ya sabe.
    expect(payload.budget_period_start_day).toBeNull();
    expect(payload.budget_period_length_days).toBeNull();
  });

  it("keeps the custom period fields when the period is custom", () => {
    const payload = toPayload(
      form({
        budgetPeriod: "custom",
        budgetPeriodStartDay: "5",
        budgetPeriodLengthDays: "14",
      }),
    );
    expect(payload.budget_period_start_day).toBe(5);
    expect(payload.budget_period_length_days).toBe(14);
  });
});

describe("governanceProblems", () => {
  it("is quiet on an untouched form", () => {
    expect(governanceProblems(EMPTY)).toEqual([]);
  });

  it("rejects a budget the resolver would silently discard", () => {
    // `resolve_execution_budgets` tira los valores ≤ 0 y los no numéricos sin
    // decir nada: el operador creería haber capado el gasto.
    expect(governanceProblems(form({ budgets: { ...EMPTY.budgets, max_tokens: "0" } }))).toContain(
      "«Tokens por run» tiene que ser mayor que cero.",
    );
    expect(
      governanceProblems(form({ budgets: { ...EMPTY.budgets, max_cost_usd: "mucho" } })),
    ).toContain("«Coste por run (USD)» tiene que ser un número.");
  });

  it("does not complain about a value above the platform ceiling", () => {
    // Recortar está documentado y es intencionado: no es un error.
    expect(
      governanceProblems(form({ budgets: { ...EMPTY.budgets, max_tokens: "999999" } })),
    ).toEqual([]);
  });

  it("catches malformed guardrails before the round-trip", () => {
    expect(governanceProblems(form({ guardrailsJson: "{no json" }))).toContain(
      "Los guardrails no son JSON válido.",
    );
    expect(governanceProblems(form({ guardrailsJson: "[1,2]" }))).toContain(
      "Los guardrails tienen que ser un objeto JSON.",
    );
    expect(governanceProblems(form({ guardrailsJson: '{"guardrails":{}}' }))).toEqual([]);
  });

  it("enforces the spend-budget invariants the backend also enforces", () => {
    expect(governanceProblems(form({ budgetAmount: "100" }))).toContain(
      "Un importe necesita moneda (código de 3 letras).",
    );
    expect(governanceProblems(form({ budgetPeriod: "custom" }))).toContain(
      "Un periodo personalizado necesita día de inicio y duración.",
    );
    expect(
      governanceProblems(form({ budgetPeriod: "monthly", budgetPeriodStartDay: "3" })),
    ).toContain("El día de inicio y la duración solo aplican a un periodo personalizado.");
  });
});
