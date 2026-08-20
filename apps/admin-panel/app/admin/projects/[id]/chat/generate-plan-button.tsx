"use client";

/**
 * "Generar Plan" (task_03_13), troceado en prod-16 `task_prod16_08`.
 *
 * Regla de visibilidad: sólo aparece cuando el ÚLTIMO mensaje de agente del
 * feed trae un adjunto con la forma
 *   {"kind": "planning_directive", "intent": "finish_planning"}.
 * Ese adjunto lo añade el endpoint de chat (cableado de la Fase G) cuando el
 * sub-grafo de planning devuelve `PMIntent.FINISH_PLANNING`.
 *
 * Al pulsarlo hace `POST /projects/{id}/plans` con el id de conversación — el
 * backend (task_03_14) materializa el Plan canónico desde el historial. El botón
 * se deshabilita mientras el POST está en vuelo y el error se pinta debajo.
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import type { Message } from "./chat-types";

interface GeneratePlanButtonProps {
  messages: Message[];
  projectId: string;
  conversationId: string;
}

export function GeneratePlanButton({
  messages,
  projectId,
  conversationId,
}: GeneratePlanButtonProps) {
  const t = useT("projectChat");
  const queryClient = useQueryClient();
  const router = useRouter();
  const errorText = useErrorText();

  const ready = isFinishPlanningReady(messages);

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<{ id: string }>(`/projects/${projectId}/plans`, {
        method: "POST",
        body: { conversation_id: conversationId },
      }),
    onSuccess: (created) => {
      queryClient.invalidateQueries({ queryKey: ["plans", projectId] });
      router.push(`/admin/projects/${projectId}/plans/${created.id}`);
    },
  });

  if (!ready) {
    return null;
  }

  return (
    <div className="mt-4" data-testid="generate-plan-cta">
      <Button
        data-testid="generate-plan-button"
        disabled={mutation.isPending}
        onClick={() => mutation.mutate()}
      >
        {t("generatePlan")}
      </Button>
      {mutation.isError ? (
        <p className="text-destructive mt-2 text-xs" data-testid="generate-plan-error">
          {/* prod-16 `task_prod16_05`: aquí se pintaba `mutation.error.body`
              CRUDO — el body JSON del backend en la cara del usuario. Es la
              misma fuga que el enunciado contaba 13 veces buscando por el
              nombre `errorText`; escrita en línea no salía en esa búsqueda. */}
          {errorText(mutation.error)}
        </p>
      ) : null}
    </div>
  );
}

/**
 * Returns true when the most recent `agent` message in the feed has
 * an attachment that signals the planning sub-graph wants to finish.
 *
 * The structure is intentionally permissive — Fase G may add more
 * keys (rationale, estimated_phase_count, ...) but only `kind` and
 * `intent` drive visibility.
 */
export function isFinishPlanningReady(messages: Message[]): boolean {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg.author_kind !== "agent") continue;
    for (const att of msg.attachments) {
      if (
        att &&
        typeof att === "object" &&
        (att as Record<string, unknown>).kind === "planning_directive" &&
        (att as Record<string, unknown>).intent === "finish_planning"
      ) {
        return true;
      }
    }
    return false; // most recent agent message had no FINISH directive
  }
  return false;
}
