"use client";

/**
 * Diálogo de borrado del agente, con confirmación por nombre.
 *
 * Extraído de `page.tsx` en prod-16 `task_prod16_08`. Refactor mecánico: mismos
 * `data-testid` y misma condición de habilitado (`typed === agent.name`, exacta
 * y sensible a mayúsculas — teclear el nombre es la confirmación).
 */

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
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

import type { Agent } from "./agent-detail-types";

export function AgentDeleteDialog({
  agent,
  open,
  onOpenChange,
  onDeleted,
}: {
  agent: Agent;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onDeleted: () => void;
}) {
  const t = useT("agents");
  const [typed, setTyped] = useState("");
  const matches = typed === agent.name;

  const mutation = useMutation<void, ApiError, void>({
    mutationFn: async () => {
      await apiFetch(`/agents/${agent.id}`, { method: "DELETE" });
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
          <DialogTitle>{t("deleteTitle")}</DialogTitle>
          <DialogDescription>
            {t("deleteWarningLead")} <strong>{t("deleteWarningStrong")}</strong>
            {t("deleteWarningTail")}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            {t("deleteConfirmPrompt")}
            <br />
            <code className="bg-muted rounded px-1 py-0.5 text-xs">{agent.name}</code>
          </p>
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={agent.name}
            data-testid="delete-agent-confirm-input"
          />
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="delete-agent-error"
            >
              {mutation.error?.message ?? t("deleteError")}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("cancel")}
          </Button>
          <Button
            variant="destructive"
            disabled={!matches || mutation.isPending}
            onClick={() => mutation.mutate()}
            data-testid="delete-agent-confirm"
          >
            {mutation.isPending ? t("deleting") : t("deleteConfirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
