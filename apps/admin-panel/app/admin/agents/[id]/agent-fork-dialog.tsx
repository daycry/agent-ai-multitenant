"use client";

/**
 * "Personalizar (crear copia)" — fork del agente (Plan 06.17 task_06_17_12).
 *
 * Extraído de `page.tsx` en prod-16 `task_prod16_08`. Refactor mecánico: mismos
 * `data-testid`, mismo filtro de proyectos y mismo cuerpo del POST.
 *
 * Clona el agente en un proyecto del tenant como copia editable. El backend
 * (POST /agents/{id}/fork) hereda automáticamente KBs/tools/skills del origen.
 * El fork siempre aterriza en un proyecto concreto, por eso el selector de
 * proyecto destino es obligatorio — y las plantillas se filtran fuera.
 */

import { useEffect, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";

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
import { Select } from "@/components/ui/select";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";

import type { Agent } from "./agent-detail-types";

interface ForkProject {
  id: string;
  name: string;
  is_template: boolean;
}

export function AgentForkDialog({
  agent,
  open,
  onOpenChange,
  onForked,
}: {
  agent: Agent;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onForked: (newId: string) => void;
}) {
  const t = useT("agents");
  const [projectId, setProjectId] = useState("");
  const [name, setName] = useState("");

  useEffect(() => {
    if (open) {
      setProjectId("");
      setName(t("forkCopySuffix", { name: agent.name }));
    }
  }, [open, agent.name, t]);

  const projectsQuery = useQuery<ForkProject[], ApiError>({
    queryKey: ["projects", "list"],
    queryFn: () => apiFetch<ForkProject[]>("/projects"),
    enabled: open,
    refetchOnWindowFocus: false,
  });
  const projects = (projectsQuery.data ?? []).filter((p) => !p.is_template);

  const mutation = useMutation<Agent, ApiError, void>({
    mutationFn: () =>
      apiFetch<Agent>(`/agents/${agent.id}/fork`, {
        method: "POST",
        body: { project_id: projectId, name: name.trim() || undefined },
      }),
    onSuccess: (fork) => onForked(fork.id),
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) mutation.reset();
        onOpenChange(v);
      }}
    >
      <DialogContent data-testid="agent-fork-dialog">
        <DialogHeader>
          <DialogTitle>{t("fork")}</DialogTitle>
          <DialogDescription>
            {t("forkDescriptionLead")} <strong>{agent.name}</strong> {t("forkDescriptionMid")}{" "}
            <strong>{t("forkDescriptionStrong")}</strong> {t("forkDescriptionTail")}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="fork-name">{t("forkNameLabel")}</Label>
            <Input
              id="fork-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="fork-agent-name"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="fork-project">{t("forkProjectLabel")}</Label>
            <Select
              id="fork-project"
              value={projectId}
              onChange={(e) => setProjectId(e.target.value)}
              data-testid="fork-agent-project"
            >
              <option value="">{t("forkPickProject")}</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </Select>
            {projectsQuery.isSuccess && projects.length === 0 && (
              <p className="text-muted-foreground text-xs" data-testid="fork-agent-no-projects">
                {t("forkNoProjects")}
              </p>
            )}
          </div>
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="fork-agent-error"
            >
              {mutation.error?.message ?? t("forkError")}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("cancel")}
          </Button>
          <Button
            disabled={!projectId || mutation.isPending}
            onClick={() => mutation.mutate()}
            data-testid="fork-agent-submit"
          >
            {mutation.isPending ? t("creating") : t("forkSubmit")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
