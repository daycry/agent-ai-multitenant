"use client";

/**
 * Redirigir un run EN MARCHA en vez de solo poder matarlo (`task_wf_71`).
 *
 * Hasta ahora, ver que un agente iba por mal camino dejaba una sola opción al
 * lado del visor: «Cancelar ejecución». Eso tira todo el trabajo hecho y
 * relanza a ciegas con el mismo prompt que ya había fallado.
 *
 * La guía se entrega al bucle en la siguiente iteración, como instrucción de
 * máxima prioridad. Se consume UNA vez: es una intervención puntual, no una
 * instrucción permanente.
 */

import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { Compass } from "lucide-react";

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
import { RoleGuard } from "@/components/ui/role-guard";
import { ApiError, apiFetch } from "@/lib/api";

export function ExecutionGuidance({ executionId }: { executionId: string }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [sent, setSent] = useState(false);

  const mutation = useMutation({
    mutationFn: (guidance: string) =>
      apiFetch(`/executions/${executionId}/guidance`, {
        method: "POST",
        body: { guidance },
      }),
    onSuccess: () => {
      setSent(true);
      setText("");
      setOpen(false);
    },
  });

  return (
    <RoleGuard min="tenant_admin">
      <Button
        variant="outline"
        size="sm"
        onClick={() => {
          mutation.reset();
          setOpen(true);
        }}
        data-testid="execution-guidance-open"
      >
        <Compass className="mr-1 h-4 w-4" />
        {sent ? "Guía enviada" : "Redirigir"}
      </Button>

      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!next) setOpen(false);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Redirigir este run</DialogTitle>
            <DialogDescription>
              El agente leerá esto en su siguiente iteración, con prioridad sobre los empujones
              automáticos. Se entrega UNA vez: es una corrección puntual, no una instrucción que se
              repita cada turno. Si lo que hace falta es parar del todo, usa «Cancelar».
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div className="flex flex-col gap-1.5">
              <Label>Qué debe hacer a partir de ahora</Label>
              <MarkdownTextarea
                value={text}
                onChange={setText}
                rows={5}
                placeholder="Por ejemplo: 'No toques el esquema de la BD; usa el adaptador que ya existe en adapters/legacy.py.'"
                data-testid="execution-guidance-text"
              />
            </div>
            {mutation.isError ? (
              <p
                className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
                data-testid="execution-guidance-error"
              >
                {mutation.error instanceof ApiError ? mutation.error.body : String(mutation.error)}
              </p>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cancelar
            </Button>
            <Button
              disabled={!text.trim() || mutation.isPending}
              onClick={() => mutation.mutate(text.trim())}
              data-testid="execution-guidance-submit"
            >
              {mutation.isPending ? "Enviando…" : "Enviar al agente"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </RoleGuard>
  );
}
