"use client";

/**
 * Los dos diálogos que cambian quién puede leer qué (prod-16 `task_prod16_08`).
 *
 * Borrar una KB y conceder acceso a un proyecto no se parecen en el formulario,
 * pero sí en lo que arriesgan: uno destruye el índice y el otro amplía quién lo
 * ve. Van juntos para que la guarda de cada uno —la confirmación por nombre
 * exacto, el acuse con el nombre del proyecto— se lea de una vez y nadie la
 * afloje sin verla.
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
import { Label } from "@/components/ui/label";
import { ProjectCombobox } from "@/components/ui/project-combobox";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { type KnowledgeBase } from "./kb-types";

export function KbDeleteDialog({
  kb,
  onOpenChange,
  onDeleted,
}: {
  kb: KnowledgeBase;
  onOpenChange: (v: boolean) => void;
  onDeleted: () => void;
}) {
  const errorText = useErrorText();
  const t = useT("knowledgeBases");
  const [typed, setTyped] = useState("");
  const matches = typed === kb.name;

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
      await apiFetch(`/knowledge-bases/${kb.id}`, { method: "DELETE" });
    },
    onSuccess: onDeleted,
  });

  return (
    <Dialog
      open={true}
      onOpenChange={(v) => {
        if (!v) closeAndReset();
        else onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("deleteTitle")}</DialogTitle>
          <DialogDescription>
            {t("deleteDescriptionPre")}
            <strong>{t("deleteDescriptionStrong")}</strong>
            {t("deleteDescriptionPost")}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            {t("deleteConfirmPrompt")}{" "}
            <code className="bg-muted rounded px-1 py-0.5 text-xs">{kb.name}</code>
          </p>
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={kb.name}
            data-testid="kb-delete-confirm-input"
          />
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="kb-delete-error"
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
            data-testid="kb-delete-confirm"
          >
            {mutation.isPending ? t("deleting") : t("deleteSubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export function KbGrantDialog({
  kb,
  onOpenChange,
  onGranted,
}: {
  kb: KnowledgeBase;
  onOpenChange: (v: boolean) => void;
  onGranted: () => void;
}) {
  const errorText = useErrorText();
  const t = useT("knowledgeBases");
  const [projectId, setProjectId] = useState<string | null>(null);
  const [projectName, setProjectName] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const mutation = useMutation<unknown, ApiError, { project_id: string }>({
    mutationFn: (payload) =>
      apiFetch(`/knowledge-bases/${kb.id}/projects`, { method: "POST", body: payload }),
    onSuccess: () => {
      setSuccessMsg(
        projectName ? t("grantSuccessNamed", { name: projectName }) : t("grantSuccess"),
      );
      setProjectId(null);
      setProjectName(null);
    },
  });

  return (
    <Dialog
      open={true}
      onOpenChange={(v) => {
        if (!v) {
          setProjectId(null);
          setProjectName(null);
          setSuccessMsg(null);
          onGranted();
        }
        onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("grantDialogTitle")}</DialogTitle>
          <DialogDescription>{t("grantDescription")}</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            {t("grantKbPrefix")} <strong>{kb.name}</strong>
          </p>

          <div className="flex flex-col gap-1.5">
            <Label>{t("grantProjectLabel")}</Label>
            <ProjectCombobox
              value={projectId}
              onChange={(id, name) => {
                setProjectId(id);
                setProjectName(name ?? null);
                setSuccessMsg(null);
              }}
              data-testid="kb-grant-project"
            />
          </div>

          {successMsg && (
            <p
              className="bg-success-soft text-success-soft-foreground rounded p-2 text-xs"
              data-testid="kb-grant-success"
            >
              {successMsg}
            </p>
          )}

          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="kb-grant-error"
            >
              {errorText(mutation.error)}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("close")}
          </Button>
          <Button
            disabled={!projectId || mutation.isPending}
            onClick={() => {
              if (projectId) mutation.mutate({ project_id: projectId });
            }}
            data-testid="kb-grant-submit"
          >
            {mutation.isPending ? t("granting") : t("grantSubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
