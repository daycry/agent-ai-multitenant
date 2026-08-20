"use client";

// Sincronizar el plan al Kanban (asignación por rol, ADR 0091) + resumen del resultado.
// Extraída verbatim de plan-interactive-sections.tsx (tramo #9, partición del
// hotspot residual de 1248 líneas — auditoría 2026-07-10). No es una ruta
// (nombre ≠ page.tsx dentro de app/**); testids intactos.

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLangOptional } from "@/lib/lang-context";
import { type PlanPhaseSpec, phaseLabel } from "./plan-spec-types";

// --------------------------------------------------------------------------
// Sync to Kanban (task_03_27)
// --------------------------------------------------------------------------
type SyncScope = "total" | "phase" | "selection";

interface SyncResponse {
  created_task_ids: Record<string, string>;
  skipped_task_ids: Record<string, string>;
  dependencies_created: number;
}

export function SyncToKanbanSection({
  planId,
  status,
  phases,
  taskIds,
}: {
  status: string;
  planId: string;
  phases: PlanPhaseSpec[];
  taskIds: string[];
}) {
  const t = useT("planDetail");
  const lang = useLangOptional();
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<SyncScope>("total");
  const [phaseIndex, setPhaseIndex] = useState<number>(0);
  const [selection, setSelection] = useState<Set<string>>(new Set());
  const [lastResult, setLastResult] = useState<SyncResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const queryClient = useQueryClient();
  // Materialising tasks is only legal once the plan is signed off (mirrors the
  // backend guard). A draft must not seed the Kanban.
  const syncable = status === "approved" || status === "in_progress";

  const mutation = useMutation({
    mutationFn: () => {
      const body: { scope: SyncScope; phase_index?: number; task_ids?: string[] } = {
        scope,
      };
      if (scope === "phase") body.phase_index = phaseIndex;
      if (scope === "selection") body.task_ids = Array.from(selection);
      return apiFetch<SyncResponse>(`/plans/${planId}/sync-to-kanban`, {
        method: "POST",
        body,
      });
    },
    onSuccess: (data) => {
      setLastResult(data);
      setErrorMsg(null);
      // The Kanban tab caches its tasks query — invalidate so the UI
      // reflects the freshly-materialised cards.
      queryClient.invalidateQueries({ queryKey: ["project-tasks"] });
    },
    onError: (err) => {
      setLastResult(null);
      setErrorMsg(err instanceof ApiError ? err.body : String(err));
    },
  });

  const canSubmit =
    !mutation.isPending &&
    (scope !== "selection" || selection.size > 0) &&
    (scope !== "phase" || (phases.length > 0 && phaseIndex >= 0 && phaseIndex < phases.length));

  return (
    <Card className="mt-6" data-testid="plan-sync-to-kanban">
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>{t("syncTitle")}</CardTitle>
        <Button
          onClick={() => {
            setLastResult(null);
            setErrorMsg(null);
            setOpen(true);
          }}
          disabled={taskIds.length === 0 || !syncable}
          data-testid="plan-sync-open"
        >
          {t("syncTitle")}
        </Button>
      </CardHeader>
      <CardContent>
        {!syncable ? (
          <p className="text-muted-foreground text-sm italic" data-testid="plan-sync-not-approved">
            {t("syncNotApprovedBefore")} <strong>{t("syncNotApprovedStrong")}</strong>{" "}
            {t("syncNotApprovedAfter")}
          </p>
        ) : taskIds.length === 0 ? (
          <p className="text-muted-foreground text-sm italic" data-testid="plan-sync-empty">
            {t("syncEmpty")}
          </p>
        ) : lastResult ? (
          <SyncResultLine result={lastResult} />
        ) : (
          <p className="text-muted-foreground text-sm">{t("syncHelp")}</p>
        )}
      </CardContent>

      <Dialog
        open={open}
        onOpenChange={(next) => {
          if (!mutation.isPending) setOpen(next);
        }}
      >
        <DialogContent data-testid="plan-sync-dialog">
          <DialogHeader>
            <DialogTitle>{t("syncTitle")}</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <fieldset className="flex flex-col gap-2 text-sm">
              <label className="flex items-center gap-2" data-testid="plan-sync-scope-total-row">
                <input
                  type="radio"
                  name="sync-scope"
                  value="total"
                  checked={scope === "total"}
                  onChange={() => setScope("total")}
                  data-testid="plan-sync-scope-total"
                />
                <span>{t("syncScopeTotal", { count: taskIds.length })}</span>
              </label>
              <label className="flex items-center gap-2" data-testid="plan-sync-scope-phase-row">
                <input
                  type="radio"
                  name="sync-scope"
                  value="phase"
                  checked={scope === "phase"}
                  onChange={() => setScope("phase")}
                  disabled={phases.length === 0}
                  data-testid="plan-sync-scope-phase"
                />
                <span>{t("syncScopePhase")}</span>
                {scope === "phase" ? (
                  <select
                    value={phaseIndex}
                    onChange={(e) => setPhaseIndex(Number(e.target.value))}
                    data-testid="plan-sync-phase-select"
                    className="bg-background border-muted rounded border px-2 py-1 text-xs"
                  >
                    {phases.map((p, i) => (
                      <option key={i} value={i}>
                        {phaseLabel(p, i, lang)}
                      </option>
                    ))}
                  </select>
                ) : null}
              </label>
              <label
                className="flex items-center gap-2"
                data-testid="plan-sync-scope-selection-row"
              >
                <input
                  type="radio"
                  name="sync-scope"
                  value="selection"
                  checked={scope === "selection"}
                  onChange={() => setScope("selection")}
                  data-testid="plan-sync-scope-selection"
                />
                <span>{t("syncScopeSelection")}</span>
              </label>

              {scope === "selection" ? (
                <ul
                  className="border-muted mt-1 max-h-48 overflow-y-auto rounded border px-2 py-1 text-xs"
                  data-testid="plan-sync-selection-list"
                >
                  {taskIds.map((tid) => (
                    <li key={tid} className="flex items-center gap-2 py-0.5">
                      <input
                        type="checkbox"
                        checked={selection.has(tid)}
                        onChange={(e) => {
                          const next = new Set(selection);
                          if (e.target.checked) next.add(tid);
                          else next.delete(tid);
                          setSelection(next);
                        }}
                        data-testid={`plan-sync-selection-${tid}`}
                      />
                      <span className="font-mono">{tid}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </fieldset>

            {errorMsg ? (
              <p className="text-destructive text-xs" data-testid="plan-sync-error">
                {errorMsg}
              </p>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setOpen(false)}
              disabled={mutation.isPending}
              data-testid="plan-sync-cancel"
            >
              {t("cancel")}
            </Button>
            <Button
              onClick={() => mutation.mutate()}
              disabled={!canSubmit}
              data-testid="plan-sync-confirm"
            >
              {mutation.isPending ? t("syncing") : t("syncConfirm")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

function SyncResultLine({ result }: { result: SyncResponse }) {
  const t = useT("planDetail");
  const created = Object.keys(result.created_task_ids).length;
  const skipped = Object.keys(result.skipped_task_ids).length;
  return (
    <p className="text-sm" data-testid="plan-sync-result">
      {t("syncResultBefore")} <span className="font-semibold">{created}</span>{" "}
      {t("syncResultMiddle")} <span className="font-semibold">{skipped}</span>{" "}
      {t("syncResultAfter")}{" "}
      <span className="text-muted-foreground">
        {t("syncResultDeps", { count: result.dependencies_created })}
      </span>
    </p>
  );
}
