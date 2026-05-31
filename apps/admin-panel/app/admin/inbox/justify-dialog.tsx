"use client";

/**
 * Modal de acción contextual de la bandeja personal (Plan 16 task_16_08).
 *
 * Cubre las tres acciones que llevan texto:
 *   - reject    — justificación OBLIGATORIA; tarea -> blocked.
 *   - escalate  — motivo opcional; tarea -> blocked + notifica al admin.
 *   - complete  — comentarios opcionales; tarea -> in_review.
 *
 * El modal POSTea al endpoint correspondiente con el cuerpo apropiado
 * ({ justification } / { comments }) y llama a onDone al terminar para que la
 * página refresque la lista. La acción "accept" (sin cuerpo) se resuelve en la
 * propia página, no aquí.
 */

import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { ApiError, apiFetch } from "@/lib/api";

import type { InboxAssignment } from "./page";

type DialogMode = "reject" | "escalate" | "complete";

interface ActionResult {
  assignment_id: string;
  task_id: string;
  action: string;
  assignment_status: string;
  task_status: string;
}

const COPY: Record<
  DialogMode,
  {
    title: string;
    description: string;
    label: string;
    placeholder: string;
    required: boolean;
    field: "justification" | "comments";
    endpoint: "reject" | "escalate" | "complete";
    confirmLabel: string;
    confirmVariant: "default" | "destructive";
  }
> = {
  reject: {
    title: "Rechazar tarea",
    description:
      "Indica por qué rechazas esta tarea. La justificación queda registrada en el historial y la tarea pasa a bloqueada para que un administrador la reasigne.",
    label: "Justificación",
    placeholder: "Ej.: fuera de mi área de competencia, falta contexto…",
    required: true,
    field: "justification",
    endpoint: "reject",
    confirmLabel: "Rechazar",
    confirmVariant: "destructive",
  },
  escalate: {
    title: "Escalar al administrador",
    description:
      "La tarea pasa a bloqueada y se notifica a los administradores del tenant. Puedes añadir un motivo (opcional).",
    label: "Motivo (opcional)",
    placeholder: "Ej.: necesita una decisión con más autoridad…",
    required: false,
    field: "justification",
    endpoint: "escalate",
    confirmLabel: "Escalar",
    confirmVariant: "default",
  },
  complete: {
    title: "Marcar como completada",
    description:
      "La tarea pasa a revisión. Puedes dejar una nota sobre el trabajo realizado (opcional). El formulario completo de entrega con adjuntos y horas llega más adelante.",
    label: "Comentarios (opcional)",
    placeholder: "Ej.: resuelto sin observaciones…",
    required: false,
    field: "comments",
    endpoint: "complete",
    confirmLabel: "Completar",
    confirmVariant: "default",
  },
};

export function InboxJustifyDialog({
  request,
  onOpenChange,
  onDone,
}: {
  request: { mode: DialogMode; item: InboxAssignment } | null;
  onOpenChange: (open: boolean) => void;
  onDone: () => void;
}) {
  const [text, setText] = useState("");

  // Reset the textarea whenever a new request opens the dialog.
  useEffect(() => {
    if (request) setText("");
  }, [request]);

  const mutation = useMutation<ActionResult, ApiError, void>({
    mutationFn: () => {
      if (!request) throw new Error("no request");
      const copy = COPY[request.mode];
      const body: Record<string, string> = {};
      const trimmed = text.trim();
      if (trimmed) body[copy.field] = trimmed;
      return apiFetch<ActionResult>(
        `/inbox/assignments/${request.item.assignment_id}/${copy.endpoint}`,
        { method: "POST", body },
      );
    },
    onSuccess: onDone,
  });

  const open = request !== null;
  const copy = request ? COPY[request.mode] : null;
  const confirmDisabled =
    mutation.isPending || (copy?.required === true && text.trim().length === 0);

  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="md">
      <DialogContent>
        {request && copy && (
          <>
            <DialogHeader>
              <DialogTitle>{copy.title}</DialogTitle>
              <DialogDescription>{copy.description}</DialogDescription>
            </DialogHeader>
            <DialogBody>
              <p className="text-muted-foreground text-xs">
                Tarea:{" "}
                <span className="text-foreground font-medium">{request.item.task_title}</span>
              </p>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="inbox-action-text">{copy.label}</Label>
                <textarea
                  id="inbox-action-text"
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  rows={4}
                  placeholder={copy.placeholder}
                  className="border-input bg-background min-h-[6rem] rounded-md border px-3 py-2 text-sm"
                  data-testid="inbox-action-text"
                />
              </div>
              {mutation.isError && (
                <p
                  className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
                  data-testid="inbox-dialog-error"
                >
                  {mutation.error?.message ?? "Error al aplicar la acción"}
                </p>
              )}
            </DialogBody>
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Cancelar
              </Button>
              <Button
                variant={copy.confirmVariant}
                disabled={confirmDisabled}
                onClick={() => mutation.mutate()}
                data-testid="inbox-action-confirm"
              >
                {mutation.isPending ? "Aplicando…" : copy.confirmLabel}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
