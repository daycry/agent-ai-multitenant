"use client";

/**
 * Diálogo de edición del agente.
 *
 * Extraído de `page.tsx` en prod-16 `task_prod16_08`. Refactor mecánico: mismos
 * `data-testid`, mismas reglas de validación y mismo cuerpo del PUT.
 *
 * El campo plano `system_prompt` (NOT NULL en el backend) se sigue derivando de
 * la fuente única bilingüe: ES si lo hay, si no EN. Eso no es cosmética — es lo
 * que evita que un agente sin prompt ES quede con el campo vacío en base.
 */

import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";

import { PersonaModelFields, PersonaPromptFields } from "@/components/capability/persona-section";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { Select } from "@/components/ui/select";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import { MEMORY_SCOPE_OPTIONS } from "@/lib/memory/constants";
import { privateScopeMemoryWarning } from "@/lib/memory/honesty";
import {
  buildModelConfig,
  draftFromConfig,
  validateDraft,
  type ModelConfigDraft,
  type SystemPrompts,
} from "@/lib/persona/persona";

import { initialPrompts, ROLE_OPTIONS, type Agent, type AgentUpdate } from "./agent-detail-types";

export function AgentEditDialog({
  agent,
  open,
  onOpenChange,
  onSaved,
}: {
  agent: Agent;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const t = useT("agents");
  // Namespace aparte porque el catálogo de scopes lo comparten esta ficha y la
  // del equipo (`lib/memory/constants.ts`): la clave viene de la constante.
  const tScope = useT("memoryScope");
  const { lang } = useLang();
  const [name, setName] = useState(agent.name);
  const [description, setDescription] = useState(agent.description ?? "");
  const [role, setRole] = useState(agent.role);
  const [memoryScope, setMemoryScope] = useState(agent.memory_scope);
  const [reviewCap, setReviewCap] = useState(agent.review_capability);
  const [maxTasks, setMaxTasks] = useState(agent.max_concurrent_tasks);
  // Persona (SER): borrador de modelo + prompts bilingües sobre la fuente única.
  const [draft, setDraft] = useState<ModelConfigDraft>(() => draftFromConfig(agent.model_config));
  const [prompts, setPrompts] = useState<SystemPrompts>(() => initialPrompts(agent));
  // Persona válida = modelo del catálogo (sin errores) + al menos un prompt.
  const hasPrompt = Boolean((prompts.es ?? "").trim() || (prompts.en ?? "").trim());
  const personaValid = validateDraft(draft, lang).length === 0 && hasPrompt;
  const privateWarning =
    agent.agent_type === "ai" ? privateScopeMemoryWarning(memoryScope, lang) : null;
  // ADR 0071: si el agente pertenece a equipo(s), la memoria la gobierna el
  // equipo — el control por-agente se deshabilita (nota con el/los nombre(s)).
  const teamNames = (agent.teams ?? []).map((team) => team.name);
  const governedByTeam = teamNames.length > 0;

  useEffect(() => {
    if (open) {
      setName(agent.name);
      setDescription(agent.description ?? "");
      setRole(agent.role);
      setMemoryScope(agent.memory_scope);
      setReviewCap(agent.review_capability);
      setMaxTasks(agent.max_concurrent_tasks);
      setDraft(draftFromConfig(agent.model_config));
      setPrompts(initialPrompts(agent));
    }
  }, [open, agent]);

  const mutation = useMutation<Agent, ApiError, AgentUpdate>({
    mutationFn: (payload) =>
      apiFetch<Agent>(`/agents/${agent.id}`, {
        method: "PUT",
        body: payload,
      }),
    onSuccess: onSaved,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("editTitle")}</DialogTitle>
          <DialogDescription>{t("editDescription")}</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ae-name">{t("fieldName")}</Label>
              <Input
                id="ae-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                data-testid="edit-agent-name"
                required
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ae-role">{t("fieldRole")}</Label>
              <Select
                id="ae-role"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                data-testid="edit-agent-role"
              >
                {ROLE_OPTIONS.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </Select>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>{t("fieldDescription")}</Label>
            <MarkdownTextarea
              value={description}
              onChange={setDescription}
              rows={2}
              data-testid="edit-agent-description"
            />
          </div>

          {/* Persona (SER): proveedor/modelo/temperatura del catálogo cerrado
              (ADR 0021) + system prompt bilingüe es/en sobre la fuente única. */}
          <fieldset className="border-border space-y-3 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">{t("personaFullLegend")}</legend>
            <PersonaModelFields draft={draft} onChange={setDraft} idPrefix="edit-agent" />
            <PersonaPromptFields prompts={prompts} onChange={setPrompts} idPrefix="edit-agent" />
          </fieldset>

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ae-scope">{t("memoryScope")}</Label>
              <Select
                id="ae-scope"
                value={memoryScope}
                onChange={(e) => setMemoryScope(e.target.value)}
                disabled={governedByTeam}
                data-testid="edit-agent-memory-scope"
              >
                {MEMORY_SCOPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {tScope(o.key)}
                  </option>
                ))}
              </Select>
              {governedByTeam ? (
                <p
                  className="text-muted-foreground text-xs"
                  data-testid="edit-agent-memory-team-governed"
                  role="status"
                >
                  {teamNames.length === 1
                    ? t("governedByOneTeam", { team: teamNames[0] })
                    : t("governedByTeams", { teams: teamNames.join(", ") })}
                </p>
              ) : privateWarning ? (
                <p
                  className="text-warning-soft-foreground text-xs"
                  data-testid="edit-agent-private-memory-warning"
                  role="status"
                >
                  {privateWarning}
                </p>
              ) : null}
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="ae-tasks">{t("maxConcurrent")}</Label>
              <Input
                id="ae-tasks"
                type="number"
                min={1}
                max={64}
                value={maxTasks}
                onChange={(e) => setMaxTasks(Number(e.target.value) || 1)}
                data-testid="edit-agent-max-tasks"
              />
            </div>
            <div className="flex items-end gap-2">
              <Checkbox
                id="ae-review"
                checked={reviewCap}
                onChange={(e) => setReviewCap(e.target.checked)}
                data-testid="edit-agent-review-cap"
              />
              <Label htmlFor="ae-review">{t("canReviewTasks")}</Label>
            </div>
          </div>

          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="edit-agent-error"
            >
              {mutation.error?.message ?? t("saveError")}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("cancel")}
          </Button>
          <Button
            disabled={!name.trim() || !personaValid || mutation.isPending}
            onClick={() => {
              // El campo plano `system_prompt` (NOT NULL en backend) se deriva
              // de la fuente única bilingüe: prioriza ES, cae a EN. El
              // `model_config` lleva la persona completa (modelo + prompts).
              const flat = (prompts.es ?? "").trim() || (prompts.en ?? "").trim();
              mutation.mutate({
                name: name.trim(),
                description: description.trim() || null,
                role,
                system_prompt: flat,
                model_config: buildModelConfig({
                  current: agent.model_config,
                  draft,
                  prompts,
                }),
                memory_scope: memoryScope,
                review_capability: reviewCap,
                max_concurrent_tasks: maxTasks,
              });
            }}
            data-testid="edit-agent-save"
          >
            {mutation.isPending ? t("saving") : t("save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
