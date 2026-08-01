"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Home, Plus } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
import { RoleGuard } from "@/components/ui/role-guard";
import { Select } from "@/components/ui/select";
import { EmptyState } from "@/components/ui/empty-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StateBlock } from "@/components/shared/state-block";
import { PersonaModelFields } from "@/components/capability/persona-section";
import { ApiError, apiFetch } from "@/lib/api";
import { pickLang, useT, type MessageKey } from "@/lib/i18n";
import { useLang, type Lang } from "@/lib/lang-context";
import {
  buildModelConfig,
  DEFAULT_MODEL_CONFIG,
  validateDraft,
  type ModelConfig,
  type ModelConfigDraft,
} from "@/lib/persona/persona";

interface Agent {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  role: string;
  agent_type: string;
  scope: "global_builtin" | "global_tenant_template" | "project_local" | string;
  project_id: string | null;
  forked_from_agent_id: string | null;
  is_template: boolean;
  // ADR 0071: equipos a los que pertenece el agente (badge + filtros).
  teams: { id: string; name: string }[];
  // Bilingual: the seed stores prompts under model_config.system_prompts.{es,en}.
  // The API exposes it as `model_config` (Pydantic alias of `llm_config`).
  model_config?: {
    system_prompts?: { es?: string; en?: string };
  } | null;
}

const PROMPT_SNIPPET = 180;

/**
 * Recorte del system prompt del agente en el idioma activo.
 *
 * El prompt llega en DATOS bilingües (`system_prompts.{es,en}`), no del
 * diccionario: no hay clave posible porque el contenido lo escribe el usuario.
 * Por eso usa `pickLang`, que es la otra mitad del i18n (prod-16) — antes esto
 * era un ternario a mano.
 *
 * `pickLang` NO es equivalente al `??` que había: cae al otro idioma también
 * cuando el pedido viene **vacío o en blanco**, no sólo cuando falta. Es un
 * cambio deliberado: un agente con `en: ""` pintaba una tarjeta sin prompt y el
 * operador lo leía como "este agente no tiene prompt", cuando sí lo tiene en
 * castellano.
 */
function promptIn(agent: Agent, lang: Lang): string | null {
  const prompts = agent.model_config?.system_prompts;
  if (!prompts) return null;
  const value = pickLang(lang, { es: prompts.es ?? "", en: prompts.en ?? "" });
  if (!value) return null;
  return value.length > PROMPT_SNIPPET ? value.slice(0, PROMPT_SNIPPET).trim() + "…" : value;
}

/**
 * Scope crudo del backend → clave del diccionario que lo etiqueta.
 *
 * El valor (`global_builtin`…) NO se traduce: es lo que viaja por la API. Un
 * scope desconocido cae a pintar el identificador, que es mejor pista de un
 * backend nuevo que una etiqueta genérica que lo esconda.
 */
const SCOPE_LABEL_KEY: Record<string, MessageKey<"agents">> = {
  global_builtin: "scopeBuiltin",
  global_tenant_template: "scopeTenantTemplate",
  project_local: "scopeProjectLocal",
};

const SCOPE_BADGE: Record<string, BadgeVariant> = {
  global_builtin: "muted",
  global_tenant_template: "info",
  project_local: "primary",
};

function AgentList({
  agents,
  emptyText,
  lang,
}: {
  agents: Agent[];
  emptyText: string;
  lang: Lang;
}) {
  const t = useT("agents");
  if (agents.length === 0) {
    return (
      <EmptyState className="mt-2" icon={Bot} title={t("emptyTitle")} description={emptyText} />
    );
  }
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3" data-testid="agents-grid">
      {agents.map((agent) => {
        const snippet = promptIn(agent, lang);
        return (
          <Link
            key={agent.id}
            href={`/admin/agents/${agent.id}`}
            data-testid={`agent-link-${agent.id}`}
            className="block"
          >
            <Card
              data-testid={`agent-${agent.id}`}
              className="hover:border-primary/40 h-full cursor-pointer transition-colors"
            >
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-base">{agent.name}</CardTitle>
                <Badge variant={SCOPE_BADGE[agent.scope] ?? "muted"}>
                  {SCOPE_LABEL_KEY[agent.scope] ? t(SCOPE_LABEL_KEY[agent.scope]) : agent.scope}
                </Badge>
              </CardHeader>
              <CardContent className="flex flex-col gap-2">
                <p className="text-muted-foreground text-xs">
                  <span className="font-medium">{t("cardRole")}</span> {agent.role}
                  {agent.agent_type !== "ai" && (
                    <span className="ml-2 italic">({agent.agent_type})</span>
                  )}
                </p>
                {agent.description && <p className="text-sm">{agent.description}</p>}
                {snippet && (
                  <div
                    className="bg-muted/40 rounded-md border p-2 text-xs"
                    data-testid={`prompt-${agent.id}`}
                    data-lang={lang}
                  >
                    <p className="text-muted-foreground mb-1 text-[10px] font-semibold uppercase tracking-wide">
                      {t("systemPrompt")} · {lang}
                    </p>
                    <p className="text-foreground/90 leading-snug">{snippet}</p>
                  </div>
                )}
                {agent.forked_from_agent_id && (
                  <p className="text-muted-foreground text-xs italic">{t("forkedFrom")}</p>
                )}
                {/* ADR 0071: equipos del agente (su memoria la gobierna el equipo). */}
                <div
                  className="flex flex-wrap items-center gap-1"
                  data-testid={`teams-${agent.id}`}
                >
                  {agent.teams.length > 0 ? (
                    agent.teams.map((t) => (
                      <Badge key={t.id} variant="muted" className="text-[10px]">
                        {t.name}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-muted-foreground text-[10px] italic">
                      {t("filterNoTeam")}
                    </span>
                  )}
                </div>
              </CardContent>
            </Card>
          </Link>
        );
      })}
    </div>
  );
}

export default function AgentsCatalogPage() {
  const t = useT("agents");
  const { lang } = useLang();
  const queryClient = useQueryClient();
  const [newOpen, setNewOpen] = useState(false);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["agents", "list"],
    queryFn: () => apiFetch<Agent[]>("/agents"),
    refetchOnWindowFocus: false,
  });

  // ADR 0071: filtros por pertenencia a equipo + por equipo concreto del tenant.
  const teamsQuery = useQuery({
    queryKey: ["teams", "list"],
    queryFn: () => apiFetch<{ id: string; name: string }[]>("/teams"),
    refetchOnWindowFocus: false,
  });
  const [membership, setMembership] = useState<"all" | "in_team" | "no_team">("all");
  const [teamFilter, setTeamFilter] = useState<string>("");

  const filtered = (data ?? []).filter((a) => {
    if (membership === "in_team" && a.teams.length === 0) return false;
    if (membership === "no_team" && a.teams.length > 0) return false;
    if (teamFilter && !a.teams.some((t) => t.id === teamFilter)) return false;
    return true;
  });
  const builtins = filtered.filter((a) => a.scope === "global_builtin");
  const tenantTemplates = filtered.filter((a) => a.scope === "global_tenant_template");
  const projectLocal = filtered.filter((a) => a.scope === "project_local");

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <Breadcrumb
        items={[
          { label: t("home"), href: "/admin", icon: <Home className="h-3.5 w-3.5" /> },
          { label: t("agents") },
        ]}
      />
      <PageHeader
        icon={<Bot className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("catalogTitle")}
        description={t("catalogDescription")}
        actions={
          <RoleGuard min="tenant_admin">
            <Button onClick={() => setNewOpen(true)} data-testid="new-agent-button">
              <Plus className="mr-1 h-4 w-4" />
              {t("newAgent")}
            </Button>
          </RoleGuard>
        }
      />
      <NewAgentDialog
        open={newOpen}
        onOpenChange={setNewOpen}
        onCreated={() => {
          void queryClient.invalidateQueries({ queryKey: ["agents", "list"] });
          setNewOpen(false);
        }}
      />

      <StateBlock
        isLoading={isLoading}
        isError={isError}
        error={error}
        loadingLabel={t("loading")}
        loadingTestId="agents-loading"
        errorTitle={t("loadError")}
        errorTestId="agents-error"
      >
        {data && (
          <div className="mb-4 flex flex-wrap items-center gap-4" data-testid="agents-filters">
            <div className="flex items-center gap-2">
              <Label htmlFor="agents-membership" className="text-xs">
                {t("filterMembership")}
              </Label>
              {/* Ancho fijo en el contenedor: el wrapper del Select es w-full, así
                  que el <select> no se queda estrecho y no recorta el valor. */}
              <div className="w-44">
                <Select
                  id="agents-membership"
                  value={membership}
                  onChange={(e) => setMembership(e.target.value as "all" | "in_team" | "no_team")}
                  data-testid="agents-membership-filter"
                >
                  <option value="all">{t("filterAll")}</option>
                  <option value="in_team">{t("filterInTeam")}</option>
                  <option value="no_team">{t("filterNoTeam")}</option>
                </Select>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Label htmlFor="agents-team" className="text-xs">
                {t("filterTeam")}
              </Label>
              <div className="w-56">
                <Select
                  id="agents-team"
                  value={teamFilter}
                  onChange={(e) => setTeamFilter(e.target.value)}
                  data-testid="agents-team-filter"
                >
                  <option value="">{t("filterAllTeams")}</option>
                  {(teamsQuery.data ?? []).map((t) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </Select>
              </div>
            </div>
          </div>
        )}
        {data && (
          <Tabs defaultValue="builtin" data-testid="agents-tabs">
            <TabsList>
              <TabsTrigger value="builtin" data-testid="tab-builtin">
                {t("scopeBuiltin")} ({builtins.length})
              </TabsTrigger>
              <TabsTrigger value="template" data-testid="tab-template">
                {t("tabTemplates")} ({tenantTemplates.length})
              </TabsTrigger>
              <TabsTrigger value="local" data-testid="tab-local">
                {t("tabLocal")} ({projectLocal.length})
              </TabsTrigger>
            </TabsList>

            <TabsContent value="builtin">
              <AgentList agents={builtins} lang={lang} emptyText={t("emptyBuiltins")} />
            </TabsContent>
            <TabsContent value="template">
              <AgentList agents={tenantTemplates} lang={lang} emptyText={t("emptyTemplates")} />
            </TabsContent>
            <TabsContent value="local">
              <AgentList agents={projectLocal} lang={lang} emptyText={t("emptyLocal")} />
            </TabsContent>
          </Tabs>
        )}
      </StateBlock>
    </div>
  );
}

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

function NewAgentDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onCreated: () => void;
}) {
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
              {mutation.error?.message ?? t("createError")}
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
