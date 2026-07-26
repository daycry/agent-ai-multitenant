"use client";

/**
 * Lanzar una corrida de evals (`task_wf_52b`).
 *
 * El subsistema de evals estaba construido entero —módulos, tablas, endpoints,
 * dashboard— y **sus tablas vacías**: no había ninguna vía de producir una
 * corrida. El dashboard de calidad llevaba desde el Plan 14 pintando un vacío
 * permanente porque nadie podía llenarlo.
 *
 * Esto es el botón que faltaba. Elige el dataset dorado, el modelo SUJETO (el
 * que se mide) y el modelo JUEZ (el que puntúa), y lanza.
 *
 * Dos cosas que el formulario impide a propósito:
 *   - juez == sujeto: un modelo juzgándose a sí mismo se aprueba, así que el
 *     backend devuelve 409 y aquí ni se deja pulsar;
 *   - dataset sin items: daría un pass_rate del 100 % sin haber juzgado nada,
 *     que es el peor dato posible porque parece perfecto.
 *
 * Backend:
 *   - GET  /eval-datasets   — elegir el dataset (trae `item_count`)
 *   - POST /eval-runs       — lanzar (201; 409 mismo modelo; 422 dataset vacío)
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Play } from "lucide-react";

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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { ApiError, apiFetch } from "@/lib/api";

interface EvalDataset {
  id: string;
  name: string;
  kind: string;
  item_count: number;
}

interface EvalRun {
  id: string;
  status: string;
  total_items: number;
  passed_items: number;
  pass_rate: string | null;
}

/**
 * Traduce el fallo del backend a algo que diga QUÉ HACER.
 *
 * `ApiError.body` es el texto crudo de la respuesta, no un objeto: hay que
 * parsearlo. Un cuerpo que no sea JSON (un 502 del gateway, por ejemplo) no
 * puede reventar el diálogo — se enseña tal cual.
 */
export function describeLaunchError(error: unknown): string {
  if (!(error instanceof ApiError)) return String(error);
  let detail: unknown;
  try {
    detail = (JSON.parse(error.body) as { detail?: unknown }).detail;
  } catch {
    return error.body || error.message;
  }
  const obj =
    detail && typeof detail === "object" && !Array.isArray(detail)
      ? (detail as Record<string, unknown>)
      : null;
  const code = obj?.["error"];

  if (code === "same_model_judge") {
    return "El juez tiene que ser un modelo distinto del sujeto: un modelo juzgándose a sí mismo se aprueba.";
  }
  if (code === "empty_dataset") {
    return "Ese dataset no tiene items. Promociona antes alguna tarea aprobada con «Promover a dataset».";
  }
  if (code === "dataset_too_large") {
    // El backend manda las cifras; repetirlas aquí a mano se desincroniza.
    return typeof obj?.["message"] === "string"
      ? (obj["message"] as string)
      : "El dataset es demasiado grande para una corrida síncrona. Párte lo o reduce criterios.";
  }
  if (code === "no_llm_provider") {
    return "No hay proveedor LLM activo para actuar de juez. Configura uno en Proveedores LLM.";
  }
  if (typeof obj?.["message"] === "string") return obj["message"] as string;
  return typeof detail === "string" ? detail : error.body;
}

export function LaunchEvalRun() {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [datasetId, setDatasetId] = useState("");
  const [subjectModel, setSubjectModel] = useState("");
  const [judgeModel, setJudgeModel] = useState("");
  const [promptVersion, setPromptVersion] = useState("");
  const [launched, setLaunched] = useState<EvalRun | null>(null);

  const datasetsQuery = useQuery({
    queryKey: ["eval-datasets"],
    queryFn: () => apiFetch<EvalDataset[]>("/eval-datasets"),
    refetchOnWindowFocus: false,
    enabled: open,
  });
  const datasets = datasetsQuery.data ?? [];
  const chosen = datasets.find((d) => d.id === datasetId) ?? null;

  const mutation = useMutation({
    mutationFn: () =>
      apiFetch<EvalRun>("/eval-runs", {
        method: "POST",
        body: {
          dataset_id: datasetId,
          subject_model: subjectModel.trim(),
          judge_model: judgeModel.trim(),
          ...(promptVersion.trim() ? { subject_prompt_version: promptVersion.trim() } : {}),
        },
      }),
    onSuccess: (run) => {
      setLaunched(run);
      void queryClient.invalidateQueries({ queryKey: ["eval-quality-runs"] });
      void queryClient.invalidateQueries({ queryKey: ["eval-quality-dashboard"] });
    },
  });

  const sameModel = subjectModel.trim().length > 0 && subjectModel.trim() === judgeModel.trim();
  const emptyDataset = chosen !== null && chosen.item_count === 0;
  const canLaunch =
    datasetId !== "" &&
    subjectModel.trim() !== "" &&
    judgeModel.trim() !== "" &&
    !sameModel &&
    !emptyDataset &&
    !mutation.isPending;

  return (
    <RoleGuard min="tenant_admin">
      <Button
        size="sm"
        onClick={() => {
          mutation.reset();
          setLaunched(null);
          setOpen(true);
        }}
        data-testid="launch-eval-run-open"
      >
        <Play className="mr-1 h-4 w-4" />
        Lanzar corrida
      </Button>

      <Dialog open={open} onOpenChange={(next) => !next && setOpen(false)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Lanzar una corrida de evals</DialogTitle>
            <DialogDescription>
              El modelo SUJETO produce una salida para cada item del dataset y el modelo JUEZ la
              compara contra la salida de referencia. Cuesta llamadas a los dos modelos.
            </DialogDescription>
          </DialogHeader>

          <DialogBody className="space-y-4">
            {launched ? (
              <div
                className="bg-success-soft text-success-soft-foreground rounded p-3 text-sm"
                data-testid="launch-eval-run-done"
              >
                Corrida <span className="font-mono">{launched.id.slice(0, 8)}</span>{" "}
                {launched.status}: {launched.passed_items}/{launched.total_items} items pasaron.
              </div>
            ) : null}

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="eval-dataset">Dataset dorado</Label>
              <select
                id="eval-dataset"
                className="border-input bg-background h-9 rounded-md border px-3 text-sm"
                value={datasetId}
                onChange={(e) => setDatasetId(e.target.value)}
                data-testid="launch-eval-run-dataset"
              >
                <option value="">Elige un dataset…</option>
                {datasets.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.name} ({d.item_count} items)
                  </option>
                ))}
              </select>
              {emptyDataset ? (
                <p className="text-destructive text-xs" data-testid="launch-eval-run-empty">
                  Ese dataset no tiene items: la corrida daría un 100 % sin haber juzgado nada.
                  Promociona antes alguna tarea aprobada.
                </p>
              ) : null}
            </div>

            <div className="grid gap-3 sm:grid-cols-2">
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="eval-subject">Modelo sujeto (el que se mide)</Label>
                <Input
                  id="eval-subject"
                  value={subjectModel}
                  onChange={(e) => setSubjectModel(e.target.value)}
                  placeholder="p. ej. qwen2.5-coder:14b"
                  data-testid="launch-eval-run-subject"
                />
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="eval-judge">Modelo juez (el que puntúa)</Label>
                <Input
                  id="eval-judge"
                  value={judgeModel}
                  onChange={(e) => setJudgeModel(e.target.value)}
                  placeholder="p. ej. claude-sonnet-5"
                  data-testid="launch-eval-run-judge"
                />
              </div>
            </div>
            {sameModel ? (
              <p className="text-destructive text-xs" data-testid="launch-eval-run-same-model">
                El juez tiene que ser distinto del sujeto: un modelo juzgándose a sí mismo se
                aprueba.
              </p>
            ) : null}

            <div className="flex flex-col gap-1.5">
              <Label htmlFor="eval-prompt-version">Versión de prompt (opcional)</Label>
              <Input
                id="eval-prompt-version"
                value={promptVersion}
                onChange={(e) => setPromptVersion(e.target.value)}
                placeholder="la etiqueta que aparece en el run que quieres atribuir"
                data-testid="launch-eval-run-prompt-version"
              />
              <p className="text-muted-foreground text-xs">
                Sirve para atribuir el resultado a un cambio de prompt concreto en el desglose por
                release.
              </p>
            </div>

            {mutation.isError ? (
              <p
                className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
                data-testid="launch-eval-run-error"
              >
                {describeLaunchError(mutation.error)}
              </p>
            ) : null}
          </DialogBody>

          <DialogFooter>
            <Button variant="outline" onClick={() => setOpen(false)}>
              Cerrar
            </Button>
            <Button
              disabled={!canLaunch}
              onClick={() => mutation.mutate()}
              data-testid="launch-eval-run-submit"
            >
              {mutation.isPending ? "Juzgando…" : "Lanzar"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </RoleGuard>
  );
}
