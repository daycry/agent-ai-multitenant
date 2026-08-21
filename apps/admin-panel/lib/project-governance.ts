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
 *
 * i18n (prod-16 `task_prod16_03`): los tres catálogos guardan la **clave** del
 * namespace `projectGovernance`, no su texto. Este módulo es PURO —no puede
 * llamar a un hook—, así que hasta ahora la sección pintaba «Tokens por run» y
 * «tiene que ser mayor que cero» con el toggle en EN aunque el resto de la
 * pantalla tradujese. Ninguna de las dos guardas de `check-i18n` lo veía: los
 * textos no estaban en un atributo ni detrás de un ternario, estaban en un
 * módulo de `lib/`. Quien necesita el texto lo resuelve con el idioma activo
 * (`useT("projectGovernance")` desde el componente, `translate` desde aquí).
 */

import { translate, type Lang, type MessageKey } from "@/lib/i18n";

/** Una clave del namespace de esta sección. */
type GovernanceKey = MessageKey<"projectGovernance">;

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

/** La clave de diccionario que nombra cada presupuesto por run. */
export const EXECUTION_BUDGET_LABEL_KEY = {
  max_iterations: "budgetMaxIterations",
  max_tokens: "budgetMaxTokens",
  max_cost_usd: "budgetMaxCostUsd",
  max_wall_clock_s: "budgetMaxWallClock",
  max_tool_calls: "budgetMaxToolCalls",
} as const satisfies Record<ExecutionBudgetKey, GovernanceKey>;

/**
 * La etiqueta de un presupuesto en el idioma pedido.
 *
 * `lang` es OBLIGATORIO y sin default, por la misma razón que en
 * `conversationLabel`: un default silencioso deja que el próximo llamante se
 * olvide y vuelva a pintar castellano en el panel inglés.
 */
export function executionBudgetLabel(key: ExecutionBudgetKey, lang: Lang): string {
  return translate(lang, "projectGovernance", EXECUTION_BUDGET_LABEL_KEY[key]);
}

export const EXECUTION_BUDGET_KEYS = Object.keys(EXECUTION_BUDGET_CEILING) as ExecutionBudgetKey[];

export const HUMAN_TASK_REVIEW_MODES = [
  { value: "auto_approve", labelKey: "reviewAutoApprove", hintKey: "reviewAutoApproveHint" },
  { value: "peer_human_reviewer", labelKey: "reviewPeer", hintKey: "reviewPeerHint" },
] as const satisfies readonly { value: string; labelKey: GovernanceKey; hintKey: GovernanceKey }[];

export const BUDGET_PERIODS = [
  { value: "", labelKey: "periodNone" },
  { value: "daily", labelKey: "periodDaily" },
  { value: "weekly", labelKey: "periodWeekly" },
  { value: "monthly", labelKey: "periodMonthly" },
  { value: "custom", labelKey: "periodCustom" },
] as const satisfies readonly { value: string; labelKey: GovernanceKey }[];

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

/** Problemas que se ven sin preguntar al servidor, redactados en `lang`. La
 * lista vacía NO garantiza un 200 — el backend valida igual, y es él quien
 * manda.
 *
 * `lang` es OBLIGATORIO: ver `executionBudgetLabel`. */
export function governanceProblems(form: GovernanceForm, lang: Lang): string[] {
  const problems: string[] = [];
  const say = (key: GovernanceKey, vars?: Record<string, string | number>) =>
    problems.push(translate(lang, "projectGovernance", key, vars));

  for (const key of EXECUTION_BUDGET_KEYS) {
    const raw = form.budgets[key].trim();
    if (!raw) continue;
    const parsed = Number(raw);
    const field = executionBudgetLabel(key, lang);
    if (!Number.isFinite(parsed)) {
      say("problemNotANumber", { field });
    } else if (parsed <= 0) {
      // El resolver DESCARTA un presupuesto ≤ 0 sin decir nada: el operador
      // creería haber capado el gasto y no habría capado nada.
      say("problemNotPositive", { field });
    }
  }

  if (form.guardrailsJson.trim()) {
    try {
      const parsed: unknown = JSON.parse(form.guardrailsJson);
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        say("problemGuardrailsNotObject");
      }
    } catch {
      say("problemGuardrailsNotJson");
    }
  }

  const amount = numberOrNull(form.budgetAmount);
  if (form.budgetAmount.trim() && amount === null) {
    say("problemAmountNotANumber");
  } else if (amount !== null && amount < 0) {
    say("problemAmountNegative");
  }
  if (amount !== null && !form.budgetCurrency.trim()) {
    say("problemAmountNeedsCurrency");
  }
  if (form.budgetCurrency.trim() && form.budgetCurrency.trim().length !== 3) {
    say("problemCurrencyLength");
  }
  if (form.budgetPeriod === "custom") {
    if (!form.budgetPeriodStartDay.trim() || !form.budgetPeriodLengthDays.trim()) {
      say("problemCustomNeedsBoth");
    }
  } else if (form.budgetPeriodStartDay.trim() || form.budgetPeriodLengthDays.trim()) {
    say("problemCustomOnly");
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
