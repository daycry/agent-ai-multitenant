/**
 * Piezas puras de la sección de gobierno del proyecto (`task_wf_35`).
 *
 * Los cuatro ajustes que el backend acepta y ninguna pantalla ofrecía:
 * `execution_budgets`, `guardrails_config`, el presupuesto de gasto
 * (`budget_*`) y `human_task_review_mode`. La conversión formulario↔API tiene
 * dos reglas que no se pueden equivocar y que por eso viven aquí con test:
 *
 *   1. **Vacío significa «heredar», no «cero».** Un campo en blanco tiene que
 *      viajar como `null`/ausente; mandar `0` fijaría un presupuesto de cero y
 *      ningún run arrancaría.
 *   2. **Los invariantes del periodo de gasto** se comprueban antes de enviar,
 *      porque el 422 del backend llega como una lista de pydantic y es peor
 *      leerlo que evitarlo.
 */

/** Techos de plataforma (`budgets/envelope.py: EXECUTION_BUDGET_CEILING`). Un
 * valor por encima NO es un error: el resolver lo recorta, y eso está
 * documentado. Se muestran para que el operador sepa contra qué compite. */
export const EXECUTION_BUDGET_CEILING = {
  max_iterations: 50,
  max_tokens: 500_000,
  max_cost_usd: 5,
  max_wall_clock_s: 7200,
  max_tool_calls: 50,
} as const;

export type ExecutionBudgetKey = keyof typeof EXECUTION_BUDGET_CEILING;

export const EXECUTION_BUDGET_LABEL: Record<ExecutionBudgetKey, string> = {
  max_iterations: "Iteraciones por run",
  max_tokens: "Tokens por run",
  max_cost_usd: "Coste por run (USD)",
  max_wall_clock_s: "Tiempo de reloj por run (s)",
  max_tool_calls: "Llamadas a tools por run",
};

export const EXECUTION_BUDGET_KEYS = Object.keys(EXECUTION_BUDGET_CEILING) as ExecutionBudgetKey[];

export const HUMAN_TASK_REVIEW_MODES = [
  {
    value: "auto_approve",
    label: "Auto-aprobar al entregar",
    hint: "Entregar la tarea la da por hecha. Adecuado para tareas de «firma».",
  },
  {
    value: "peer_human_reviewer",
    label: "Revisión de otra persona",
    hint: "La tarea queda en revisión y se asigna a un segundo humano, que aprueba o rechaza.",
  },
] as const;

export const BUDGET_PERIODS = [
  { value: "", label: "Sin límite de gasto" },
  { value: "daily", label: "Diario" },
  { value: "weekly", label: "Semanal" },
  { value: "monthly", label: "Mensual" },
  { value: "custom", label: "Personalizado" },
] as const;

/** El estado del formulario: todo texto, que es lo que un `<input>` sostiene
 * mientras se escribe (incluido el estado intermedio inválido). */
export interface GovernanceForm {
  budgets: Record<ExecutionBudgetKey, string>;
  guardrailsJson: string;
  humanTaskReviewMode: string;
  budgetAmount: string;
  budgetCurrency: string;
  budgetPeriod: string;
  budgetPeriodStartDay: string;
  budgetPeriodLengthDays: string;
}

export interface GovernanceValue {
  execution_budgets?: Record<string, unknown> | null;
  guardrails_config?: Record<string, unknown> | null;
  human_task_review_mode?: string | null;
  budget_amount?: string | number | null;
  budget_currency?: string | null;
  budget_period?: string | null;
  budget_period_start_day?: number | null;
  budget_period_length_days?: number | null;
}

function text(value: unknown): string {
  return value == null ? "" : String(value);
}

export function toForm(value: GovernanceValue | null | undefined): GovernanceForm {
  const budgets = {} as Record<ExecutionBudgetKey, string>;
  const stored = (value?.execution_budgets ?? {}) as Record<string, unknown>;
  for (const key of EXECUTION_BUDGET_KEYS) budgets[key] = text(stored[key]);
  const guardrails = value?.guardrails_config;
  return {
    budgets,
    guardrailsJson:
      guardrails && Object.keys(guardrails).length > 0 ? JSON.stringify(guardrails, null, 2) : "",
    humanTaskReviewMode: text(value?.human_task_review_mode) || "auto_approve",
    budgetAmount: text(value?.budget_amount),
    budgetCurrency: text(value?.budget_currency),
    budgetPeriod: text(value?.budget_period),
    budgetPeriodStartDay: text(value?.budget_period_start_day),
    budgetPeriodLengthDays: text(value?.budget_period_length_days),
  };
}

function numberOrNull(raw: string): number | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) ? parsed : null;
}

/** Problemas que se ven sin preguntar al servidor. La lista vacía NO garantiza
 * un 200 — el backend valida igual, y es él quien manda. */
export function governanceProblems(form: GovernanceForm): string[] {
  const problems: string[] = [];

  for (const key of EXECUTION_BUDGET_KEYS) {
    const raw = form.budgets[key].trim();
    if (!raw) continue;
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) {
      problems.push(`«${EXECUTION_BUDGET_LABEL[key]}» tiene que ser un número.`);
    } else if (parsed <= 0) {
      // El resolver DESCARTA un presupuesto ≤ 0 sin decir nada: el operador
      // creería haber capado el gasto y no habría capado nada.
      problems.push(`«${EXECUTION_BUDGET_LABEL[key]}» tiene que ser mayor que cero.`);
    }
  }

  if (form.guardrailsJson.trim()) {
    try {
      const parsed: unknown = JSON.parse(form.guardrailsJson);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        problems.push("Los guardrails tienen que ser un objeto JSON.");
      }
    } catch {
      problems.push("Los guardrails no son JSON válido.");
    }
  }

  const amount = numberOrNull(form.budgetAmount);
  if (form.budgetAmount.trim() && amount === null) {
    problems.push("El importe del presupuesto tiene que ser un número.");
  } else if (amount !== null && amount < 0) {
    problems.push("El importe del presupuesto no puede ser negativo.");
  }
  if (amount !== null && !form.budgetCurrency.trim()) {
    problems.push("Un importe necesita moneda (código de 3 letras).");
  }
  if (form.budgetCurrency.trim() && form.budgetCurrency.trim().length !== 3) {
    problems.push("La moneda es un código de 3 letras (EUR, USD…).");
  }
  if (form.budgetPeriod === "custom") {
    if (!form.budgetPeriodStartDay.trim() || !form.budgetPeriodLengthDays.trim()) {
      problems.push("Un periodo personalizado necesita día de inicio y duración.");
    }
  } else if (form.budgetPeriodStartDay.trim() || form.budgetPeriodLengthDays.trim()) {
    problems.push("El día de inicio y la duración solo aplican a un periodo personalizado.");
  }

  return problems;
}

/** El cuerpo del `PUT /projects/{id}`. Solo las claves de esta sección: el PUT
 * es parcial y no debe pisar lo que editan las otras. */
export function toPayload(form: GovernanceForm): Record<string, unknown> {
  const budgets: Record<string, number> = {};
  for (const key of EXECUTION_BUDGET_KEYS) {
    const parsed = numberOrNull(form.budgets[key]);
    if (parsed !== null) budgets[key] = parsed;
  }
  const guardrails = form.guardrailsJson.trim()
    ? (JSON.parse(form.guardrailsJson) as Record<string, unknown>)
    : null;

  return {
    // `null` (no `{}`) cuando no hay nada: un dict vacío persistido se lee
    // como «configurado a nada», que no es lo mismo que «hereda».
    execution_budgets: Object.keys(budgets).length > 0 ? budgets : null,
    guardrails_config: guardrails,
    human_task_review_mode: form.humanTaskReviewMode,
    budget_amount: numberOrNull(form.budgetAmount),
    budget_currency: form.budgetCurrency.trim() ? form.budgetCurrency.trim().toUpperCase() : null,
    budget_period: form.budgetPeriod || null,
    budget_period_start_day:
      form.budgetPeriod === "custom" ? numberOrNull(form.budgetPeriodStartDay) : null,
    budget_period_length_days:
      form.budgetPeriod === "custom" ? numberOrNull(form.budgetPeriodLengthDays) : null,
  };
}
