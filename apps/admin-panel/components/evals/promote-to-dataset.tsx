"use client";

/**
 * task_14_02 — "Promote to dataset" action (Plan 14 Fase A).
 *
 * Botón + diálogo para promover una tarea REAL APROBADA (su ejecución
 * aprobada) a un golden dataset del tenant como item del dataset. Copia el
 * input de la tarea + la salida aprobada como referencia. Va envuelto en
 * <RoleGuard min="tenant_admin"> — el dataset golden es POR TENANT (Plan 14
 * Decisiones Clave) y el backend valida igualmente (RBAC tenant_admin + RLS).
 *
 * Backend:
 *   - GET  /eval-datasets                          — elegir dataset destino
 *   - POST /eval-datasets                          — crear dataset inline
 *   - POST /tasks/{taskId}/promote-to-dataset      — promover (idempotente)
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookMarked } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { ApiError, apiFetch } from "@/lib/api";

// --------------------------------------------------------------------------
// Types (mirror api_server.schemas.evals)
// --------------------------------------------------------------------------
interface EvalDataset {
  id: string;
  name: string;
  description: string | null;
  kind: string;
  target_agent_id: string | null;
  target_role: string | null;
  item_count: number;
  created_at: string;
  updated_at: string;
}

interface PromoteResult {
  id: string;
  dataset_id: string;
  created: boolean;
  expected_output: string | null;
  source_task_id: string | null;
  source_execution_id: string | null;
  created_at: string;
}

interface PromoteToDatasetProps {
  /** The real task being promoted. */
  taskId: string;
  /** Optional: pin a specific approved execution as the reference. */
  executionId?: string;
}

const NEW_DATASET = "__new__";

export function PromoteToDataset({ taskId, executionId }: PromoteToDatasetProps) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<string>("");
  const [newName, setNewName] = useState("");
  const [allowUnapproved, setAllowUnapproved] = useState(false);
  const [result, setResult] = useState<PromoteResult | null>(null);

  const datasetsQuery = useQuery({
    queryKey: ["eval-datasets"],
    queryFn: () => apiFetch<EvalDataset[]>("/eval-datasets"),
    refetchOnWindowFocus: false,
    enabled: open,
  });

  const datasets = datasetsQuery.data ?? [];

  function invalidate() {
    void queryClient.invalidateQueries({ queryKey: ["eval-datasets"] });
  }

  const promoteMutation = useMutation({
    mutationFn: async () => {
      let datasetId = selected;
      if (selected === NEW_DATASET) {
        const created = await apiFetch<EvalDataset>("/eval-datasets", {
          method: "POST",
          body: { name: newName.trim() },
        });
        datasetId = created.id;
        invalidate();
      }
      return apiFetch<PromoteResult>(`/tasks/${taskId}/promote-to-dataset`, {
        method: "POST",
        body: {
          dataset_id: datasetId,
          execution_id: executionId ?? null,
          allow_unapproved: allowUnapproved,
        },
      });
    },
    onSuccess: (res) => {
      setResult(res);
      invalidate();
    },
  });

  function reset() {
    setSelected("");
    setNewName("");
    setAllowUnapproved(false);
    setResult(null);
    promoteMutation.reset();
  }

  function handleClose() {
    setOpen(false);
    reset();
  }

  const canSubmit =
    selected !== "" &&
    (selected !== NEW_DATASET || newName.trim().length > 0) &&
    !promoteMutation.isPending;

  const errorMessage =
    promoteMutation.error instanceof ApiError
      ? promoteMutation.error.body
      : promoteMutation.error
        ? String(promoteMutation.error)
        : null;

  return (
    <RoleGuard min="tenant_admin">
      <Button
        variant="outline"
        size="sm"
        onClick={() => setOpen(true)}
        data-testid="promote-to-dataset-button"
      >
        <BookMarked className="mr-1 h-3.5 w-3.5" />
        Promover a dataset
      </Button>

      <Dialog open={open} onOpenChange={(next) => (next ? setOpen(true) : handleClose())} size="md">
        <DialogContent data-testid="promote-dialog">
          <DialogHeader>
            <DialogTitle>Promover a golden dataset</DialogTitle>
          </DialogHeader>
          <DialogBody>
            {result ? (
              <div
                className="bg-success-soft text-success-soft-foreground rounded-md border border-success/30 p-4 text-sm"
                data-testid="promote-result"
              >
                {result.created
                  ? "Tarea promovida al dataset como nuevo item golden."
                  : "Esta tarea ya estaba en el dataset — no se ha duplicado."}
              </div>
            ) : (
              <div className="space-y-4">
                <div>
                  <Label htmlFor="promote-dataset-select">Dataset destino</Label>
                  <select
                    id="promote-dataset-select"
                    data-testid="promote-dataset-select"
                    value={selected}
                    onChange={(e) => setSelected(e.target.value)}
                    className="border-input bg-background mt-1 h-10 w-full rounded-md border px-3 text-sm disabled:opacity-60"
                    disabled={datasetsQuery.isLoading}
                  >
                    <option value="" disabled>
                      Elige un dataset…
                    </option>
                    {datasets.map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name} ({d.item_count})
                      </option>
                    ))}
                    <option value={NEW_DATASET}>+ Crear dataset nuevo…</option>
                  </select>
                </div>

                {selected === NEW_DATASET && (
                  <div>
                    <Label htmlFor="promote-new-name">Nombre del dataset</Label>
                    <Input
                      id="promote-new-name"
                      data-testid="promote-new-name"
                      value={newName}
                      onChange={(e) => setNewName(e.target.value)}
                      placeholder="Golden login"
                      className="mt-1"
                    />
                  </div>
                )}

                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    data-testid="promote-allow-unapproved"
                    checked={allowUnapproved}
                    onChange={(e) => setAllowUnapproved(e.target.checked)}
                  />
                  Promover aunque la tarea no esté aprobada (done)
                </label>

                {errorMessage && (
                  <p className="text-destructive text-sm" data-testid="promote-error">
                    {errorMessage}
                  </p>
                )}
              </div>
            )}
          </DialogBody>
          <DialogFooter>
            {result ? (
              <Button onClick={handleClose} data-testid="promote-done">
                Hecho
              </Button>
            ) : (
              <>
                <Button variant="ghost" onClick={handleClose} data-testid="promote-cancel">
                  Cancelar
                </Button>
                <Button
                  onClick={() => promoteMutation.mutate()}
                  disabled={!canSubmit}
                  data-testid="promote-submit"
                >
                  Promover
                </Button>
              </>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </RoleGuard>
  );
}
