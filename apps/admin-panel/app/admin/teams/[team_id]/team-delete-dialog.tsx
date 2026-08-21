"use client";

import { useState } from "react";
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
import { Input } from "@/components/ui/input";
import type { ApiError } from "@/lib/api";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import type { Team } from "./team-types";

// ---------------------------------------------------------------------------
// Team delete dialog with confirm-by-name (Plan 06.6 task_06_6_09)
// ---------------------------------------------------------------------------

export function TeamDeleteDialog({
  team,
  open,
  onOpenChange,
  onDeleted,
}: {
  team: Team;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onDeleted: () => void;
}) {
  const t = useT("teams");
  const errorText = useErrorText();
  const [typed, setTyped] = useState("");
  const matches = typed === team.name;

  /**
   * Cerrar SIEMPRE limpia la confirmación tecleada.
   *
   * El botón Cancelar llamaba a `onOpenChange(false)` directamente, saltándose
   * el envoltorio del `<Dialog>` que hacía el reset: al reabrir, el nombre
   * seguía escrito y el botón destructivo estaba HABILITADO de entrada. La
   * confirmación por nombre existe justo para que borrar sea un acto
   * deliberado; si sobrevive a un "Cancelar", el siguiente borrado es un click.
   * Detectado el 2026-08-19 por `project-delete.spec.ts` (el mismo defecto
   * estaba en las cuatro pantallas con confirmación por nombre).
   */
  const closeAndReset = () => {
    setTyped("");
    onOpenChange(false);
  };

  const mutation = useMutation<void, ApiError, void>({
    mutationFn: async () => {
      await apiFetch(`/teams/${team.id}`, { method: "DELETE" });
    },
    onSuccess: onDeleted,
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) closeAndReset();
        else onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("deleteTitle")}</DialogTitle>
          <DialogDescription>
            {t("deleteDescPrefix")}
            <strong>{t("deleteDescStrong")}</strong>
            {t("deleteDescSuffix")}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            {t("deleteConfirmPrompt")}
            <br />
            <code className="bg-muted rounded px-1 py-0.5 text-xs">{team.name}</code>
          </p>
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={team.name}
            data-testid="delete-team-confirm-input"
          />
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="delete-team-error"
            >
              {errorText(mutation.error)}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={closeAndReset}>
            {t("cancel")}
          </Button>
          <Button
            variant="destructive"
            disabled={!matches || mutation.isPending}
            onClick={() => mutation.mutate()}
            data-testid="delete-team-confirm"
          >
            {mutation.isPending ? t("deleting") : t("deleteConfirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
