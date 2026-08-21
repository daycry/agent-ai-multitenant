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
 *
 * ## El nombre de la copia
 *
 * Hay un índice único parcial `(tenant_id, project_id, name)` sobre los agentes
 * vivos (migración 0126), así que forkear dos veces el mismo origen al mismo
 * proyecto choca. La API contesta **409** y NO auto-renombra: el nombre es
 * identidad (con él se eligen agentes en los `role_map` y al montar equipos), y
 * renombrar en silencio decide por el usuario algo que luego tiene que deshacer.
 *
 * La mitad que le toca a la UI es *sugerir*: el campo arranca con un nombre
 * libre EN EL DESTINO elegido —de ahí la consulta de agentes, que sólo mira los
 * del proyecto seleccionado— y sigue siendo editable. Si el usuario lo toca, la
 * sugerencia se calla; y si aun así choca (una carrera, o un nombre escrito a
 * mano), el 409 se explica en vez de enseñar el error crudo.
 */

import { useEffect, useMemo, useState } from "react";
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
import { namesTakenInProject, suggestForkName } from "@/lib/agents/fork-name";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import type { Agent } from "./agent-detail-types";

interface ForkProject {
  id: string;
  name: string;
  is_template: boolean;
}

/** Lo único que hace falta del catálogo para saber qué nombres están cogidos. */
interface AgentNameRow {
  id: string;
  name: string;
  project_id: string | null;
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
  const errorText = useErrorText();
  const [projectId, setProjectId] = useState("");
  const [name, setName] = useState("");
  // Mientras esté en `false`, el campo sigue a la sugerencia; en cuanto el
  // usuario escribe, manda lo suyo y cambiar de destino ya no le pisa el texto.
  const [nameEdited, setNameEdited] = useState(false);

  useEffect(() => {
    if (open) {
      setProjectId("");
      setNameEdited(false);
    }
  }, [open]);

  const projectsQuery = useQuery<ForkProject[], ApiError>({
    queryKey: ["projects", "list"],
    queryFn: () => apiFetch<ForkProject[]>("/projects"),
    enabled: open,
    refetchOnWindowFocus: false,
  });
  const projects = (projectsQuery.data ?? []).filter((p) => !p.is_template);

  // Misma `queryKey` que el resto del panel: comparte caché con la lista de
  // agentes, así que abrir el diálogo no suele costar una petición extra.
  const agentsQuery = useQuery<AgentNameRow[], ApiError>({
    queryKey: ["agents", "list"],
    queryFn: () => apiFetch<AgentNameRow[]>("/agents"),
    enabled: open,
    refetchOnWindowFocus: false,
  });

  const suggestedName = useMemo(
    () => suggestForkName(agent.name, namesTakenInProject(agentsQuery.data ?? [], projectId), t),
    [agent.name, agentsQuery.data, projectId, t],
  );

  useEffect(() => {
    if (open && !nameEdited) setName(suggestedName);
  }, [open, nameEdited, suggestedName]);

  const mutation = useMutation<Agent, ApiError, void>({
    mutationFn: () =>
      apiFetch<Agent>(`/agents/${agent.id}/fork`, {
        method: "POST",
        body: { project_id: projectId, name: name.trim() || undefined },
      }),
    onSuccess: (fork) => onForked(fork.id),
  });

  // 409 = el nombre ya existe en el destino (la API no renombra sola). Se dice
  // QUÉ nombre choca, porque el campo para cambiarlo está justo encima. Para el
  // resto, `errorText`: esto pintaba `mutation.error.message`, que es el crudo
  // `api 409: {"detail":…}` — lo que prod-16 `task_prod16_05` vino a quitar.
  const errorMessage = !mutation.isError
    ? null
    : mutation.error?.status === 409
      ? t("forkConflictName", { name: name.trim() || agent.name })
      : errorText(mutation.error);

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
              onChange={(e) => {
                setNameEdited(true);
                setName(e.target.value);
              }}
              data-testid="fork-agent-name"
            />
            <p className="text-muted-foreground text-xs" data-testid="fork-agent-name-help">
              {t("forkNameHelp")}
            </p>
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
          {errorMessage && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="fork-agent-error"
            >
              {errorMessage}
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
