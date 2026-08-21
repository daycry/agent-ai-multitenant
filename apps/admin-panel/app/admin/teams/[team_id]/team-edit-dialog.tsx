"use client";

import { useEffect, useState } from "react";
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
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import type { ApiError } from "@/lib/api";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import type { Team, TeamUpdate } from "./team-types";

// ---------------------------------------------------------------------------
// Team edit dialog (Plan 06.6 task_06_6_08)
// ---------------------------------------------------------------------------

export function TeamEditDialog({
  team,
  open,
  onOpenChange,
  onSaved,
}: {
  team: Team;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const t = useT("teams");
  const errorText = useErrorText();
  const [name, setName] = useState(team.name);
  const [description, setDescription] = useState(team.description ?? "");

  useEffect(() => {
    if (open) {
      setName(team.name);
      setDescription(team.description ?? "");
    }
  }, [open, team.name, team.description]);

  const mutation = useMutation<Team, ApiError, TeamUpdate>({
    mutationFn: (payload) =>
      apiFetch<Team>(`/teams/${team.id}`, {
        method: "PUT",
        body: payload,
      }),
    onSuccess: onSaved,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("editTeamTitle")}</DialogTitle>
          <DialogDescription>{t("editTeamDescription")}</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="te-name">{t("nameLabel")}</Label>
            <Input
              id="te-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="edit-team-name"
              required
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>{t("descriptionLabel")}</Label>
            <MarkdownTextarea
              value={description}
              onChange={setDescription}
              rows={3}
              data-testid="edit-team-description"
            />
          </div>
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="edit-team-error"
            >
              {errorText(mutation.error)}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("cancel")}
          </Button>
          <Button
            disabled={!name.trim() || mutation.isPending}
            onClick={() =>
              mutation.mutate({
                name: name.trim(),
                description: description.trim() || null,
              })
            }
            data-testid="edit-team-save"
          >
            {mutation.isPending ? t("saving") : t("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
