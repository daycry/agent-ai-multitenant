"use client";

/**
 * Alta de un miembro del equipo, en modo `linked` (referencia al agente del
 * catálogo) o `forked` (copia editable en un proyecto).
 *
 * El modo `forked` son DOS llamadas y la segunda usa el id del FORK: añadir el
 * original dejaría el equipo apuntando a la plantilla y el fork huérfano. Lo fija
 * `page.test.tsx`.
 *
 * ## Por qué hay un campo de nombre
 *
 * Este diálogo mandaba sólo `{ project_id }`, y eso significa "hereda el nombre
 * del origen". Con el índice único parcial `(tenant_id, project_id, name)` de la
 * migración 0126, añadir al equipo un agente que ya se había forkeado a ese
 * proyecto chocaba con el índice. La API contesta **409** y no renombra sola: el
 * nombre es identidad, y renombrar en silencio decide por el usuario algo que
 * después tiene que deshacer.
 *
 * Mandar un nombre sugerido *sin enseñarlo* sería exactamente eso mismo con otro
 * culpable: la copia acabaría llamándose algo que el usuario no eligió ni vio.
 * Por eso la sugerencia va en un campo visible y editable, igual que en
 * `agents/[id]/agent-fork-dialog.tsx`, y las dos comparten el cálculo
 * (`lib/agents/fork-name.ts`) y el vocabulario del diccionario (namespace
 * `agents`) para que no se separen con el tiempo.
 */

import { useEffect, useMemo, useState } from "react";
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
import { Spinner } from "@/components/ui/spinner";
import { namesTakenInProject, suggestForkName } from "@/lib/agents/fork-name";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import type { Agent, Mode, Project, Team } from "./team-types";

export function AddMemberDialog({
  teamId,
  agents,
  projects,
  memberIds,
  open,
  onOpenChange,
  onAdded,
}: {
  teamId: string;
  agents: Agent[];
  projects: Project[];
  memberIds: string[];
  open: boolean;
  onOpenChange: (next: boolean) => void;
  onAdded: () => void;
}) {
  const t = useT("teams");
  // El vocabulario del fork vive en el namespace `agents` y se comparte con el
  // diálogo de «Personalizar» del agente: duplicarlo aquí los dejaría contando
  // dos historias distintas de la misma regla en cuanto alguien tocara una.
  const tAgents = useT("agents");
  const errorText = useErrorText();
  const [selectedAgentId, setSelectedAgentId] = useState<string>("");
  const [mode, setMode] = useState<Mode>("linked");
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [forkName, setForkName] = useState<string>("");
  // Mientras esté en `false`, el campo sigue a la sugerencia; en cuanto el
  // usuario escribe, manda lo suyo y cambiar de destino no le pisa el texto.
  const [forkNameEdited, setForkNameEdited] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  function resetDialog() {
    setSelectedAgentId("");
    setMode("linked");
    setSelectedProjectId("");
    setForkName("");
    setForkNameEdited(false);
    setSubmitError(null);
  }

  const sourceAgent = agents.find((a) => a.id === selectedAgentId) ?? null;

  const suggestedForkName = useMemo(
    () =>
      sourceAgent
        ? suggestForkName(sourceAgent.name, namesTakenInProject(agents, selectedProjectId), tAgents)
        : "",
    [sourceAgent, agents, selectedProjectId, tAgents],
  );

  useEffect(() => {
    if (!forkNameEdited) setForkName(suggestedForkName);
  }, [forkNameEdited, suggestedForkName]);

  const addMember = useMutation({
    mutationFn: async () => {
      if (!selectedAgentId) {
        throw new Error(t("errSelectAgent"));
      }
      let agentId = selectedAgentId;
      if (mode === "forked") {
        if (!selectedProjectId) {
          throw new Error(t("errSelectProject"));
        }
        const name = forkName.trim() || undefined;
        let fork: Agent;
        try {
          fork = await apiFetch<Agent>(`/agents/${selectedAgentId}/fork`, {
            method: "POST",
            body: { project_id: selectedProjectId, name },
          });
        } catch (err) {
          // El 409 se traduce AQUÍ y no en `onError` porque son dos llamadas: un
          // 409 del alta de miembro es otra cosa (el agente ya está en el
          // equipo) y contarlo como choque de nombre mandaría al usuario a
          // cambiar un nombre que no tiene ningún problema.
          if (err instanceof ApiError && err.status === 409) {
            throw new Error(tAgents("forkConflictName", { name: name ?? sourceAgent?.name ?? "" }));
          }
          throw err;
        }
        agentId = fork.id;
      }
      await apiFetch<Team>(`/teams/${teamId}/members`, {
        method: "POST",
        body: { agent_id: agentId },
      });
    },
    onSuccess: () => {
      onAdded();
      resetDialog();
    },
    // `errorText` (prod-16 `task_prod16_05`): esto pintaba `err.body` CRUDO.
    onError: (err: unknown) => {
      setSubmitError(errorText(err));
    },
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) resetDialog();
      }}
    >
      <DialogContent data-testid="add-member-dialog">
        <DialogHeader>
          <DialogTitle>{t("addMemberTitle")}</DialogTitle>
          <DialogDescription>{t("addMemberDescription")}</DialogDescription>
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="agent">{t("agentLabel")}</Label>
            <select
              id="agent"
              className="border-input bg-background ring-offset-background focus-visible:ring-ring h-10 rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
              value={selectedAgentId}
              onChange={(e) => setSelectedAgentId(e.target.value)}
              data-testid="agent-select"
            >
              <option value="">{t("selectPlaceholder")}</option>
              {agents
                .filter((a) => !memberIds.includes(a.id))
                .map((a) => (
                  <option key={a.id} value={a.id}>
                    {a.name} ({a.scope})
                  </option>
                ))}
            </select>
          </div>

          <fieldset className="flex flex-col gap-1.5">
            <legend className="text-sm font-medium">{t("modeLegend")}</legend>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="mode"
                value="linked"
                checked={mode === "linked"}
                onChange={() => setMode("linked")}
                data-testid="mode-linked"
              />
              <span>
                <strong>Linked</strong>
                {t("modeLinkedHelp")}
              </span>
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="mode"
                value="forked"
                checked={mode === "forked"}
                onChange={() => setMode("forked")}
                data-testid="mode-forked"
              />
              <span>
                <strong>Forked</strong>
                {t("modeForkedHelp")}
              </span>
            </label>
          </fieldset>

          {mode === "forked" && (
            <>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="project">{t("projectLabel")}</Label>
                <select
                  id="project"
                  className="border-input bg-background ring-offset-background focus-visible:ring-ring h-10 rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                  value={selectedProjectId}
                  onChange={(e) => setSelectedProjectId(e.target.value)}
                  data-testid="project-select"
                >
                  <option value="">{t("selectPlaceholder")}</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
                {projects.length === 0 && (
                  <p className="text-muted-foreground text-xs">{t("addMemberNoProjects")}</p>
                )}
              </div>

              {/* El nombre va DESPUÉS del destino a propósito: la sugerencia
                  depende de qué nombres estén cogidos EN ESE proyecto, así que
                  primero se elige dónde aterriza la copia y luego se ve —y se
                  corrige, si se quiere— cómo se va a llamar. */}
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="fork-name">{tAgents("forkNameLabel")}</Label>
                <Input
                  id="fork-name"
                  value={forkName}
                  onChange={(e) => {
                    setForkNameEdited(true);
                    setForkName(e.target.value);
                  }}
                  data-testid="add-member-fork-name"
                />
                <p className="text-muted-foreground text-xs">{tAgents("forkNameHelp")}</p>
              </div>
            </>
          )}

          {submitError && (
            <p
              className="text-danger-soft-foreground bg-danger-soft rounded p-2 text-xs"
              data-testid="add-member-error"
            >
              {submitError}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            data-testid="add-member-cancel"
          >
            {t("cancel")}
          </Button>
          <Button
            onClick={() => addMember.mutate()}
            disabled={
              addMember.isPending || !selectedAgentId || (mode === "forked" && !selectedProjectId)
            }
            data-testid="add-member-submit"
          >
            {addMember.isPending && <Spinner className="mr-2 h-4 w-4" />}
            {addMember.isPending ? t("adding") : t("add")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
