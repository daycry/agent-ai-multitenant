"use client";

/**
 * ADR 0135 (N3) — «re-aparcar, pero enseñando el diff».
 *
 * El operador decidió que aprobar autoriza esa acción exacta, en esa task, una
 * vez. La consecuencia asumida es que un «casi igual» —un salto de línea de
 * más, el `path` con otra forma— vuelve a preguntar. Lo que hace esa
 * consecuencia soportable es esto: la segunda solicitud llega con la acción que
 * ya se aprobó y el delta, para confirmar en dos segundos en vez de releerlo
 * todo.
 *
 * Y la otra mitad de la recomendación del ADR: cuando la MISMA acción ya se
 * aprobó N veces en esta tarea, se dice. Quien ve «aprobada 3 veces» deja de
 * aprobar y llama a alguien — que es la respuesta correcta, no seguir dándole
 * al botón.
 *
 * El api-server lo persiste en `ApprovalRequest.action.prior_approvals`, una
 * clave HERMANA de `tool`/`args`: la anotación no toca lo que se hashea.
 */

import { AlertTriangle, History } from "lucide-react";

interface ChangedValue {
  before: unknown;
  after: unknown;
}

interface ClosestPrior {
  request_id?: string | null;
  resolved_at?: string | null;
  args?: unknown;
  changed_args?: Record<string, ChangedValue>;
}

interface PriorApprovals {
  same_action_approved_times: number;
  closest_prior: ClosestPrior | null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Lee el contexto con desconfianza: es JSONB, y una fila vieja no lo lleva. */
export function readPriorApprovals(action: unknown): PriorApprovals | null {
  if (!isRecord(action)) return null;
  const raw = action.prior_approvals;
  if (!isRecord(raw)) return null;
  const times = Number(raw.same_action_approved_times ?? 0);
  const closest = isRecord(raw.closest_prior) ? (raw.closest_prior as ClosestPrior) : null;
  return { same_action_approved_times: Number.isFinite(times) ? times : 0, closest_prior: closest };
}

function render(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "string" ? value : JSON.stringify(value);
}

export function PriorApprovalsNotice({ action }: { action: unknown }) {
  const prior = readPriorApprovals(action);
  if (!prior) return null;

  const changed = isRecord(prior.closest_prior?.changed_args)
    ? (prior.closest_prior.changed_args as Record<string, ChangedValue>)
    : {};
  const keys = Object.keys(changed);
  const repeats = prior.same_action_approved_times;
  if (repeats <= 0 && keys.length === 0) return null;

  return (
    <div className="space-y-2">
      {repeats > 0 && (
        <div
          data-testid="approval-repeat-warning"
          className="border-warning/40 bg-warning-soft text-warning-soft-foreground flex items-start gap-2 rounded-md border p-2 text-xs"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            Esta misma acción ya se aprobó <strong>{repeats}</strong>{" "}
            {repeats === 1 ? "vez" : "veces"} en esta tarea. Si vuelve a pedirla, el agente no está
            avanzando: conviene mirar la tarea en vez de seguir aprobando.
          </span>
        </div>
      )}

      {keys.length > 0 && (
        <div data-testid="approval-delta" className="bg-muted/40 rounded-md p-2 text-xs">
          <p className="mb-1.5 flex items-center gap-1.5 font-medium">
            <History className="h-3.5 w-3.5" />
            Ya aprobaste una acción casi igual — esto es lo que cambió
            {prior.closest_prior?.resolved_at
              ? ` (aprobada ${new Date(prior.closest_prior.resolved_at).toLocaleString()})`
              : ""}
          </p>
          <ul className="space-y-1">
            {keys.map((key) => (
              <li key={key} className="flex flex-col gap-0.5">
                <span data-testid={`approval-delta-key-${key}`} className="font-mono font-medium">
                  {key}
                </span>
                <span
                  data-testid={`approval-delta-before-${key}`}
                  className="text-muted-foreground font-mono break-all line-through"
                >
                  {render(changed[key]?.before)}
                </span>
                <span data-testid={`approval-delta-after-${key}`} className="font-mono break-all">
                  {render(changed[key]?.after)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
