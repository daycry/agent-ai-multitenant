"use client";

/**
 * Formulario de entrega de una tarea humana (Plan 16 task_16_09).
 *
 * Modal que el asignado abre al marcar una tarea como completada. Recoge:
 *   - output       — textarea con lo que se hizo / el resultado (recomendado).
 *   - attachments  — adjuntos: archivos (ref), URLs o capturas (ref). Cada uno
 *                    con un tipo, una etiqueta y su enlace o referencia.
 *   - hours_worked — horas trabajadas (OPCIONAL; alimenta el coste humano).
 *
 * POSTea a `POST /inbox/assignments/{id}/complete` con el cuerpo estructurado:
 *   { output, attachments: [{ kind, label, url?, ref? }], hours_worked? }
 * El backend crea una HumanWorkSession y transiciona la tarea a `in_review`.
 *
 * El botón de envío se habilita cuando hay output O al menos un adjunto con
 * destino (url/ref) — entregar "nada" no aporta trazabilidad. Las horas, si se
 * indican, deben ser un número no negativo.
 */

import { useEffect, useMemo, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Link2, Paperclip, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError, apiFetch } from "@/lib/api";

import type { InboxAssignment } from "./page";

// Mirrors api_server.schemas.human_inbox.AttachmentKind.
type AttachmentKind = "file" | "url" | "screenshot";

interface AttachmentDraft {
  id: string;
  kind: AttachmentKind;
  label: string;
  // For `url` kind we ask for `url`; for `file`/`screenshot` we ask for `ref`
  // (an object-store key / path the human pastes). One single text field whose
  // meaning depends on the kind keeps the form simple.
  target: string;
}

interface SubmitResult {
  assignment_id: string;
  task_id: string;
  action: string;
  assignment_status: string;
  task_status: string;
  work_session_id: string;
  attachments_count: number;
}

const KIND_LABEL: Record<AttachmentKind, string> = {
  url: "URL",
  file: "Archivo",
  screenshot: "Captura",
};

const KIND_PLACEHOLDER: Record<AttachmentKind, string> = {
  url: "https://…",
  file: "p. ej. minio://deliverables/informe.pdf",
  screenshot: "p. ej. minio://shots/captura.png",
};

let _seq = 0;
function newAttachment(kind: AttachmentKind = "url"): AttachmentDraft {
  _seq += 1;
  return { id: `att-${_seq}`, kind, label: "", target: "" };
}

/** An attachment is "usable" once it has a label AND a target (url/ref). */
function isUsable(a: AttachmentDraft): boolean {
  return a.label.trim().length > 0 && a.target.trim().length > 0;
}

export function InboxSubmitDialog({
  request,
  onOpenChange,
  onDone,
}: {
  request: { item: InboxAssignment } | null;
  onOpenChange: (open: boolean) => void;
  onDone: () => void;
}) {
  const [output, setOutput] = useState("");
  const [hours, setHours] = useState("");
  const [attachments, setAttachments] = useState<AttachmentDraft[]>([]);

  // Reset the form whenever a new request opens the dialog.
  useEffect(() => {
    if (request) {
      setOutput("");
      setHours("");
      setAttachments([]);
    }
  }, [request]);

  const usableAttachments = useMemo(() => attachments.filter(isUsable), [attachments]);

  const hoursValue = hours.trim();
  const hoursNumber = hoursValue === "" ? null : Number(hoursValue);
  const hoursInvalid = hoursValue !== "" && (Number.isNaN(hoursNumber) || (hoursNumber ?? 0) < 0);

  const hasDeliverable = output.trim().length > 0 || usableAttachments.length > 0;

  const mutation = useMutation<SubmitResult, ApiError, void>({
    mutationFn: () => {
      if (!request) throw new Error("no request");
      const body: {
        output?: string;
        attachments: { kind: AttachmentKind; label: string; url?: string; ref?: string }[];
        hours_worked?: number;
      } = {
        attachments: usableAttachments.map((a) =>
          a.kind === "url"
            ? { kind: a.kind, label: a.label.trim(), url: a.target.trim() }
            : { kind: a.kind, label: a.label.trim(), ref: a.target.trim() },
        ),
      };
      const trimmedOutput = output.trim();
      if (trimmedOutput) body.output = trimmedOutput;
      if (hoursValue !== "" && hoursNumber !== null) body.hours_worked = hoursNumber;
      return apiFetch<SubmitResult>(`/inbox/assignments/${request.item.assignment_id}/complete`, {
        method: "POST",
        body,
      });
    },
    onSuccess: onDone,
  });

  const open = request !== null;
  const confirmDisabled = mutation.isPending || !hasDeliverable || hoursInvalid;

  function updateAttachment(id: string, patch: Partial<AttachmentDraft>) {
    setAttachments((prev) => prev.map((a) => (a.id === id ? { ...a, ...patch } : a)));
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="lg">
      <DialogContent>
        {request && (
          <>
            <DialogHeader>
              <DialogTitle>Entregar tarea</DialogTitle>
              <DialogDescription>
                Describe el trabajo realizado y adjunta los entregables. Al enviar, la tarea pasa a
                revisión y se registra una sesión de trabajo con tus horas (opcional).
              </DialogDescription>
            </DialogHeader>
            <DialogBody>
              <p className="text-muted-foreground text-xs">
                Tarea:{" "}
                <span className="text-foreground font-medium">{request.item.task_title}</span>
              </p>

              {/* Output text */}
              <div className="flex flex-col gap-1.5">
                <Label>Resultado / output</Label>
                <MarkdownTextarea
                  value={output}
                  onChange={setOutput}
                  rows={5}
                  placeholder="Describe qué hiciste y el resultado…"
                  data-testid="submit-output"
                />
              </div>

              {/* Attachments */}
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <Label>Adjuntos</Label>
                  <span
                    className="text-muted-foreground text-xs"
                    data-testid="submit-attachment-count"
                  >
                    {usableAttachments.length} válido(s)
                  </span>
                </div>
                {attachments.length === 0 && (
                  <p className="text-muted-foreground text-xs">
                    Añade enlaces, archivos o capturas como evidencia del trabajo.
                  </p>
                )}
                <ul className="flex flex-col gap-2">
                  {attachments.map((a) => (
                    <li
                      key={a.id}
                      className="bg-muted/40 flex flex-col gap-2 rounded-md border p-2 sm:flex-row sm:items-center"
                      data-testid={`submit-attachment-${a.id}`}
                    >
                      <select
                        value={a.kind}
                        onChange={(e) =>
                          updateAttachment(a.id, { kind: e.target.value as AttachmentKind })
                        }
                        className="border-input bg-background h-9 rounded-md border px-2 text-sm sm:w-28"
                        aria-label="Tipo de adjunto"
                        data-testid={`submit-attachment-kind-${a.id}`}
                      >
                        {(["url", "file", "screenshot"] as AttachmentKind[]).map((k) => (
                          <option key={k} value={k}>
                            {KIND_LABEL[k]}
                          </option>
                        ))}
                      </select>
                      <Input
                        value={a.label}
                        onChange={(e) => updateAttachment(a.id, { label: e.target.value })}
                        placeholder="Etiqueta"
                        className="h-9 sm:w-40"
                        aria-label="Etiqueta del adjunto"
                        data-testid={`submit-attachment-label-${a.id}`}
                      />
                      <Input
                        value={a.target}
                        onChange={(e) => updateAttachment(a.id, { target: e.target.value })}
                        placeholder={KIND_PLACEHOLDER[a.kind]}
                        className="h-9 flex-1"
                        aria-label={a.kind === "url" ? "URL del adjunto" : "Referencia del adjunto"}
                        data-testid={`submit-attachment-target-${a.id}`}
                      />
                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-muted-foreground hover:text-destructive shrink-0"
                        onClick={() => setAttachments((prev) => prev.filter((x) => x.id !== a.id))}
                        aria-label="Quitar adjunto"
                        data-testid={`submit-attachment-remove-${a.id}`}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </li>
                  ))}
                </ul>
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setAttachments((prev) => [...prev, newAttachment("url")])}
                    data-testid="submit-add-url"
                  >
                    <Link2 className="mr-1 h-4 w-4" />
                    Añadir URL
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setAttachments((prev) => [...prev, newAttachment("file")])}
                    data-testid="submit-add-file"
                  >
                    <Paperclip className="mr-1 h-4 w-4" />
                    Añadir archivo
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => setAttachments((prev) => [...prev, newAttachment("screenshot")])}
                    data-testid="submit-add-screenshot"
                  >
                    <Plus className="mr-1 h-4 w-4" />
                    Añadir captura
                  </Button>
                </div>
              </div>

              {/* Optional hours */}
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="submit-hours">Horas trabajadas (opcional)</Label>
                <Input
                  id="submit-hours"
                  type="number"
                  min={0}
                  step="0.25"
                  inputMode="decimal"
                  value={hours}
                  onChange={(e) => setHours(e.target.value)}
                  placeholder="p. ej. 3.5"
                  className="sm:w-40"
                  data-testid="submit-hours"
                  aria-invalid={hoursInvalid}
                />
                {hoursInvalid && (
                  <p
                    className="text-danger-soft-foreground text-xs"
                    data-testid="submit-hours-error"
                  >
                    Introduce un número de horas no negativo.
                  </p>
                )}
              </div>

              {!hasDeliverable && (
                <p className="text-muted-foreground text-xs">
                  Añade un resultado o al menos un adjunto para poder entregar.
                </p>
              )}

              {mutation.isError && (
                <p
                  className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
                  data-testid="submit-error"
                >
                  {mutation.error?.body ?? mutation.error?.message ?? "Error al entregar la tarea"}
                </p>
              )}
            </DialogBody>
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Cancelar
              </Button>
              <Button
                disabled={confirmDisabled}
                onClick={() => mutation.mutate()}
                data-testid="submit-confirm"
              >
                {mutation.isPending ? "Entregando…" : "Entregar y enviar a revisión"}
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
