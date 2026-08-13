"use client";

/**
 * `TaskHumanActions` — las cinco acciones humanas sobre UNA tarea, en un solo
 * sitio (`task_wf_40`).
 *
 * Vivían empotradas en el panel de tareas escaladas, así que solo se llegaba a
 * ellas por la ruta del plan escalado. Una tarea `blocked` por un run que falló
 * de forma ordinaria NO escala, y desde su ficha no había ninguna acción: el
 * humano veía el problema y no podía hacer nada. Extraer en vez de duplicar es
 * deliberado — dos copias de estos botones acabarían divergiendo justo en el
 * gate de estados, que es lo único que impide llamar a un endpoint que
 * responde 409.
 *
 *   POST /tasks/{id}/human-action   { action, reason?, guidance? }
 *
 * El backend (`task_lifecycle.py`) exige `tenant_admin` y solo acepta la
 * llamada desde `awaiting_human_approval` / `blocked`. `acceptsHumanAction`
 * es el espejo EXACTO de ese gate: ofrecer un botón que el backend va a
 * rechazar es peor que no ofrecerlo.
 *
 * Los dos diálogos (reasignar / bloquear) son propios de cada instancia. El
 * `Dialog` de la casa no renderiza nada cerrado y anida bien dentro de otro
 * (la pila de Escape lo contempla), así que montar el componente dentro de la
 * ficha modal de la tarea funciona igual que en una fila de lista.
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Ban, Check, RotateCcw, Workflow } from "lucide-react";

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
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { ApiError, apiFetch } from "@/lib/api";
import { cn } from "@/lib/utils";

export type HumanAction =
  "approve_manual" | "reassign_with_guidance" | "block_with_reason" | "cancel" | "retry";

export interface HumanActionPayload {
  action: HumanAction;
  reason?: string;
  guidance?: string;
}

/** Los únicos dos estados desde los que `POST /tasks/{id}/human-action` no
 * devuelve 409 (`task_lifecycle.py`). Espejo del gate del backend: si esta
 * lista y la suya divergen, la UI ofrece un botón que siempre falla. */
const ACTIONABLE_STATUSES = new Set(["awaiting_human_approval", "blocked"]);

export function acceptsHumanAction(status: string | null | undefined): boolean {
  return status != null && ACTIONABLE_STATUSES.has(status);
}

export function TaskHumanActions({
  taskId,
  disabled = false,
  onApplied,
  className,
}: {
  taskId: string;
  disabled?: boolean;
  /** Se llama tras un POST correcto. Cada pantalla invalida SUS queries: el
   * componente no puede saber en qué lista está montado. */
  onApplied?: () => void;
  className?: string;
}) {
  const [dialog, setDialog] = useState<"reassign" | "block" | null>(null);

  const mutation = useMutation({
    mutationFn: (payload: HumanActionPayload) =>
      apiFetch(`/tasks/${taskId}/human-action`, { method: "POST", body: payload }),
    onSuccess: () => onApplied?.(),
  });

  const busy = disabled || mutation.isPending;

  function run(payload: HumanActionPayload) {
    mutation.mutate(payload);
  }

  return (
    <div className={cn("space-y-2", className)} data-testid={`task-human-actions-${taskId}`}>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant="default"
          onClick={() => run({ action: "approve_manual" })}
          disabled={busy}
          data-testid={`approve-${taskId}`}
        >
          <Check className="mr-1 h-3.5 w-3.5" />
          Aprobar manualmente
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => run({ action: "retry" })}
          disabled={busy}
          data-testid={`retry-${taskId}`}
        >
          <RotateCcw className="mr-1 h-3.5 w-3.5" />
          Reintentar
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setDialog("reassign")}
          disabled={busy}
          data-testid={`reassign-${taskId}`}
        >
          <Workflow className="mr-1 h-3.5 w-3.5" />
          Reasignar con guía
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={() => setDialog("block")}
          disabled={busy}
          data-testid={`block-${taskId}`}
        >
          <Ban className="mr-1 h-3.5 w-3.5" />
          Bloquear con motivo
        </Button>
        <Button
          size="sm"
          variant="destructive"
          onClick={() => run({ action: "cancel" })}
          disabled={busy}
          data-testid={`cancel-${taskId}`}
        >
          Cancelar
        </Button>
      </div>

      {mutation.isError && (
        <p
          className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
          data-testid="action-error"
        >
          {mutation.error instanceof ApiError ? mutation.error.body : String(mutation.error)}
        </p>
      )}

      <ReasonDialog
        open={dialog === "reassign"}
        title="Reasignar con guía"
        description="Devuelve la tarea al backlog con instrucciones específicas para el siguiente intento. La guía queda en el historial de la tarea."
        label="Guía para el agente"
        placeholder="Por ejemplo: 'Intenta otro enfoque usando la librería X en vez de Y.'"
        submitLabel="Reasignar"
        rows={5}
        inputTestId="reassign-guidance"
        submitTestId="reassign-submit"
        onClose={() => setDialog(null)}
        onSubmit={(guidance) => {
          setDialog(null);
          run({ action: "reassign_with_guidance", guidance });
        }}
      />

      <ReasonDialog
        open={dialog === "block"}
        title="Bloquear con motivo"
        description="Marca la tarea como bloqueada por una causa externa (falta de acceso, dependencia pendiente, decisión de producto…). El motivo queda visible en el historial."
        label="Motivo del bloqueo"
        placeholder="Por ejemplo: 'Esperando credencial de la API del cliente.'"
        submitLabel="Bloquear"
        destructive
        rows={4}
        inputTestId="block-reason"
        submitTestId="block-submit"
        onClose={() => setDialog(null)}
        onSubmit={(reason) => {
          setDialog(null);
          run({ action: "block_with_reason", reason });
        }}
      />
    </div>
  );
}

/** Reasignar y bloquear son el mismo diálogo con otras etiquetas: un texto
 * obligatorio que viaja al historial. Tenerlos separados solo garantizaba que
 * uno de los dos se quedara sin los arreglos del otro. */
function ReasonDialog({
  open,
  title,
  description,
  label,
  placeholder,
  submitLabel,
  destructive = false,
  rows,
  inputTestId,
  submitTestId,
  onClose,
  onSubmit,
}: {
  open: boolean;
  title: string;
  description: string;
  label: string;
  placeholder: string;
  submitLabel: string;
  destructive?: boolean;
  rows: number;
  inputTestId: string;
  submitTestId: string;
  onClose: () => void;
  onSubmit: (text: string) => void;
}) {
  const [text, setText] = useState("");

  function close() {
    setText("");
    onClose();
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-1.5">
            <Label>{label}</Label>
            <MarkdownTextarea
              value={text}
              onChange={setText}
              rows={rows}
              placeholder={placeholder}
              data-testid={inputTestId}
            />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={close}>
            Cancelar
          </Button>
          <Button
            variant={destructive ? "destructive" : "default"}
            disabled={!text.trim()}
            onClick={() => {
              const value = text.trim();
              setText("");
              onSubmit(value);
            }}
            data-testid={submitTestId}
          >
            {submitLabel}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
