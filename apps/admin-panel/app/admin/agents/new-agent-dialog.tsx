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
import { Label } from "@/components/ui/label";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { ProjectCombobox } from "@/components/ui/project-combobox";
import { Select } from "@/components/ui/select";
import { PersonaModelFields } from "@/components/capability/persona-section";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useLang } from "@/lib/lang-context";
import { useErrorText } from "@/lib/use-error-text";
import {
  buildModelConfig,
  DEFAULT_MODEL_CONFIG,
  validateDraft,
  type ModelConfig,
  type ModelConfigDraft,
} from "@/lib/persona/persona";

import type { Agent } from "./agent-types";

// ---------------------------------------------------------------------------
// New Agent dialog (Plan 06.6 task_06_6_06)
// ---------------------------------------------------------------------------

const ROLE_OPTIONS = [
  "project_manager",
  "architect",
  "backend_dev",
  "frontend_dev",
  "qa",
  "reviewer",
  "leader",
  "worker",
  "specialist",
  "researcher",
  "devops",
  "security",
  "technical_writer",
];

interface NewAgentRequest {
  name: string;
  description: string | null;
  role: string;
  system_prompt: string;
  // Persona enviada bajo la clave JSON `model_config` (alias de llm_config):
  // proveedor/modelo/temperatura del catálogo cerrado + system_prompts.{es,en}.
  model_config: ModelConfig;
  scope: "global_tenant_template" | "project_local";
  project_id: string | null;
}

export function NewAgentDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: () => void;
}) {
  const errorText = useErrorText();
  const t = useT("agents");
  const { lang } = useLang();
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [role, setRole] = useState("backend_dev");
  const [systemPrompt, setSystemPrompt] = useState("");
  // Persona (SER): proveedor/modelo/temperatura del catálogo cerrado (ADR 0021)
  // + el prompt EN opcional. El prompt ES sale del campo `systemPrompt` de arriba
  // (fuente del bilingüe `system_prompts.es`).
  const [draft, setDraft] = useState<ModelConfigDraft>({ ...DEFAULT_MODEL_CONFIG });
  const [promptEn, setPromptEn] = useState("");
  const [scope, setScope] = useState<"global_tenant_template" | "project_local">(
    "global_tenant_template",
  );
  const [projectId, setProjectId] = useState("");

  const mutation = useMutation<Agent, ApiError, NewAgentRequest>({
    mutationFn: (payload) =>
      apiFetch<Agent>("/agents", {
        method: "POST",
        body: payload,
      }),
    onSuccess: () => {
      // Reset for next opening.
      setName("");
      setDescription("");
      setSystemPrompt("");
      setPromptEn("");
      setDraft({ ...DEFAULT_MODEL_CONFIG });
      setProjectId("");
      onCreated();
    },
  });

  const personaValid = validateDraft(draft, lang).length === 0;
  const submitDisabled =
    !name.trim() ||
    !systemPrompt.trim() ||
    !personaValid ||
    (scope === "project_local" && !projectId.trim()) ||
    mutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("newAgent")}</DialogTitle>
          <DialogDescription>{t("newDescription")}</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="na-name">{t("fieldName")}</Label>
              <Input
                id="na-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                data-testid="new-agent-name"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="na-role">{t("fieldRole")}</Label>
              <Select
                id="na-role"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                data-testid="new-agent-role"
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
              rows={3}
              data-testid="new-agent-description"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>{t("promptEsLabel")}</Label>
            <MarkdownTextarea
              value={systemPrompt}
              onChange={setSystemPrompt}
              rows={6}
              data-testid="new-agent-system-prompt"
            />
          </div>

          {/* Persona (SER): proveedor/modelo/temperatura del catálogo cerrado
              (ADR 0021) + prompt EN opcional (el ES sale del campo de arriba). */}
          <fieldset className="border-border space-y-3 rounded-md border p-3">
            <legend className="px-1 text-sm font-medium">{t("personaModelLegend")}</legend>
            <PersonaModelFields draft={draft} onChange={setDraft} idPrefix="new-agent" />
            <div className="flex flex-col gap-1.5">
              <Label>{t("promptEnLabel")}</Label>
              <MarkdownTextarea
                value={promptEn}
                onChange={setPromptEn}
                rows={4}
                data-testid="new-agent-prompt-en"
              />
            </div>
          </fieldset>

          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-medium">{t("scopeLegend")}</legend>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="scope"
                checked={scope === "global_tenant_template"}
                onChange={() => setScope("global_tenant_template")}
                data-testid="new-agent-scope-template"
              />
              {t("scopeTemplateOption")}
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="scope"
                checked={scope === "project_local"}
                onChange={() => setScope("project_local")}
                data-testid="new-agent-scope-local"
              />
              {t("scopeLocalOption")}
            </label>
          </fieldset>

          {scope === "project_local" && (
            <div className="flex flex-col gap-1.5">
              <Label>{t("fieldProject")}</Label>
              <ProjectCombobox
                value={projectId || null}
                onChange={(id) => setProjectId(id ?? "")}
                data-testid="new-agent-project-id"
              />
              <p className="text-muted-foreground text-xs">{t("projectHint")}</p>
            </div>
          )}

          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="new-agent-error"
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
            disabled={submitDisabled}
            onClick={() =>
              mutation.mutate({
                name: name.trim(),
                description: description.trim() || null,
                role,
                system_prompt: systemPrompt,
                // El ES viene del campo de arriba; el EN del textarea opcional.
                // model_config nace SIEMPRE poblado (ningún agente nuevo {}).
                model_config: buildModelConfig({
                  current: null,
                  draft,
                  prompts: { es: systemPrompt, en: promptEn },
                }),
                scope,
                project_id: scope === "project_local" ? projectId.trim() : null,
              })
            }
            data-testid="new-agent-submit"
          >
            {mutation.isPending ? t("creating") : t("create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
