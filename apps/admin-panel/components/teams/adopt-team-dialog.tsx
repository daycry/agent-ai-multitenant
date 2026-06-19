"use client";

/**
 * Diálogo "Adoptar / Personalizar equipo" (Ola C-UI / ADR 0066).
 *
 * Adopta un equipo built-in como copia editable del tenant vía
 * `POST /teams/{id}/adopt`. Elige el destino (proyecto → agentes `project_local`;
 * tenant → `global_tenant_template`), un nombre, y opcionalmente fija el modelo
 * por defecto del equipo nuevo (cadena de herencia, ADR 0065). El built-in
 * original no se toca. Reusable desde la lista y el detalle de equipos.
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
import { PersonaModelFields } from "@/components/capability/persona-section";
import { ApiError, apiFetch } from "@/lib/api";
import { useLang } from "@/lib/lang-context";
import {
  buildModelConfig,
  validateDraft,
  DEFAULT_MODEL_CONFIG,
  type ModelConfigDraft,
} from "@/lib/persona/persona";

interface AdoptProject {
  id: string;
  name: string;
  is_template: boolean;
}

interface AdoptedTeam {
  id: string;
  name: string;
}

type AdoptTarget = "tenant" | "project";

export function AdoptTeamDialog({
  team,
  open,
  onOpenChange,
  onAdopted,
}: {
  team: { id: string; name: string };
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onAdopted: (newId: string) => void;
}) {
  const { lang } = useLang();
  const t = (es: string, en: string) => (lang === "es" ? es : en);

  const [target, setTarget] = useState<AdoptTarget>("tenant");
  const [projectId, setProjectId] = useState("");
  const [name, setName] = useState("");
  const [pinModel, setPinModel] = useState(false);
  const [draft, setDraft] = useState<ModelConfigDraft>(DEFAULT_MODEL_CONFIG);

  useEffect(() => {
    if (open) {
      setTarget("tenant");
      setProjectId("");
      setName(t(`${team.name} (copia)`, `${team.name} (copy)`));
      setPinModel(false);
      setDraft(DEFAULT_MODEL_CONFIG);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, team.name]);

  const projectsQuery = useQuery<AdoptProject[], ApiError>({
    queryKey: ["projects", "list"],
    queryFn: () => apiFetch<AdoptProject[]>("/projects"),
    enabled: open && target === "project",
    refetchOnWindowFocus: false,
  });
  const projects = (projectsQuery.data ?? []).filter((p) => !p.is_template);

  const modelErrors = pinModel ? validateDraft(draft, lang) : [];

  const mutation = useMutation<AdoptedTeam, ApiError, void>({
    mutationFn: () =>
      apiFetch<AdoptedTeam>(`/teams/${team.id}/adopt`, {
        method: "POST",
        body: {
          target,
          project_id: target === "project" ? projectId : undefined,
          name: name.trim() || undefined,
          model_config: pinModel
            ? buildModelConfig({ current: null, draft, prompts: {} })
            : undefined,
        },
      }),
    onSuccess: (adopted) => onAdopted(adopted.id),
  });

  const canSubmit =
    !mutation.isPending &&
    name.trim().length > 0 &&
    (target === "tenant" || projectId.length > 0) &&
    modelErrors.length === 0;

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) mutation.reset();
        onOpenChange(v);
      }}
    >
      <DialogContent data-testid="adopt-team-dialog">
        <DialogHeader>
          <DialogTitle>{t("Adoptar / Personalizar equipo", "Adopt / Customize team")}</DialogTitle>
          <DialogDescription>
            {t(
              `Crea una copia editable de "${team.name}". Sus agentes se forkean (persona + tools + skills) y el equipo original built-in no se toca.`,
              `Creates an editable copy of "${team.name}". Its agents are forked (persona + tools + skills) and the original built-in team is untouched.`,
            )}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="adopt-name">{t("Nombre del equipo", "Team name")}</Label>
            <Input
              id="adopt-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="adopt-team-name"
            />
          </div>

          <fieldset className="border-border space-y-2 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">{t("Destino", "Target")}</legend>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="adopt-target"
                checked={target === "tenant"}
                onChange={() => setTarget("tenant")}
                data-testid="adopt-target-tenant"
              />
              <span>
                {t("Catálogo del tenant", "Tenant catalog")}
                <span className="text-muted-foreground block text-xs">
                  {t(
                    "El equipo y sus agentes viven a nivel de tenant (reutilizable en cualquier proyecto).",
                    "The team and its agents live at the tenant level (reusable across projects).",
                  )}
                </span>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="radio"
                name="adopt-target"
                checked={target === "project"}
                onChange={() => setTarget("project")}
                data-testid="adopt-target-project"
              />
              <span>
                {t("Un proyecto", "A project")}
                <span className="text-muted-foreground block text-xs">
                  {t(
                    "El equipo y sus agentes quedan atados a un proyecto concreto.",
                    "The team and its agents are tied to a specific project.",
                  )}
                </span>
              </span>
            </label>

            {target === "project" && (
              <div className="flex flex-col gap-1.5 pt-1">
                <Label htmlFor="adopt-project">{t("Proyecto destino", "Target project")}</Label>
                <Select
                  id="adopt-project"
                  value={projectId}
                  onChange={(e) => setProjectId(e.target.value)}
                  data-testid="adopt-team-project"
                >
                  <option value="">{t("— Selecciona —", "— Select —")}</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </Select>
                {projectsQuery.isSuccess && projects.length === 0 && (
                  <p className="text-muted-foreground text-xs" data-testid="adopt-team-no-projects">
                    {t(
                      "No tienes proyectos. Crea uno primero o adopta al catálogo del tenant.",
                      "You have no projects. Create one first or adopt to the tenant catalog.",
                    )}
                  </p>
                )}
              </div>
            )}
          </fieldset>

          <fieldset className="border-border space-y-2 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">
              {t("Modelo del equipo (opcional)", "Team model (optional)")}
            </legend>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={pinModel}
                onChange={(e) => setPinModel(e.target.checked)}
                data-testid="adopt-pin-model"
              />
              {t(
                "Fijar un modelo por defecto (si no, hereda de proyecto/plataforma)",
                "Pin a default model (otherwise inherits from project/platform)",
              )}
            </label>
            {pinModel && (
              <PersonaModelFields draft={draft} onChange={setDraft} idPrefix="adopt-team" />
            )}
          </fieldset>

          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="adopt-team-error"
            >
              {mutation.error?.message ??
                t("Error al adoptar el equipo", "Failed to adopt the team")}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("Cancelar", "Cancel")}
          </Button>
          <Button
            disabled={!canSubmit}
            onClick={() => mutation.mutate()}
            data-testid="adopt-team-submit"
          >
            {mutation.isPending ? t("Adoptando…", "Adopting…") : t("Adoptar", "Adopt")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
