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
  const [typed, setTyped] = useState("");
  const matches = typed === team.name;

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
        if (!v) setTyped("");
        onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Borrar equipo</DialogTitle>
          <DialogDescription>
            Esta acción es <strong>irreversible</strong>. Los agentes miembros NO se borran — solo
            desaparece su pertenencia a este equipo.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            Para confirmar, teclea el nombre del equipo:
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
              {mutation.error?.message ?? "Error al borrar"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            variant="destructive"
            disabled={!matches || mutation.isPending}
            onClick={() => mutation.mutate()}
            data-testid="delete-team-confirm"
          >
            {mutation.isPending ? "Borrando…" : "Borrar definitivamente"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
