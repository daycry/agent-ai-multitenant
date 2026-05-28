"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Plus } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { Home } from "lucide-react";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, apiFetch } from "@/lib/api";
import { useLang, type Lang } from "@/lib/lang-context";

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
  // Bilingual: the seed stores prompts under model_config.system_prompts.{es,en}.
  // The API exposes it as `model_config` (Pydantic alias of `llm_config`).
  model_config?: {
    system_prompts?: { es?: string; en?: string };
  } | null;
}

const PROMPT_SNIPPET = 180;

function promptIn(agent: Agent, lang: Lang): string | null {
  const prompts = agent.model_config?.system_prompts;
  if (!prompts) return null;
  const value = prompts[lang] ?? prompts[lang === "es" ? "en" : "es"];
  if (!value) return null;
  return value.length > PROMPT_SNIPPET ? value.slice(0, PROMPT_SNIPPET).trim() + "…" : value;
}

const SCOPE_LABEL: Record<string, string> = {
  global_builtin: "Built-in",
  global_tenant_template: "Plantilla del tenant",
  project_local: "Local del proyecto",
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
  if (agents.length === 0) {
    return <p className="text-muted-foreground py-8 text-center text-sm">{emptyText}</p>;
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
                  {SCOPE_LABEL[agent.scope] ?? agent.scope}
                </Badge>
              </CardHeader>
              <CardContent className="flex flex-col gap-2">
                <p className="text-muted-foreground text-xs">
                  <span className="font-medium">Role:</span> {agent.role}
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
                      System prompt · {lang}
                    </p>
                    <p className="text-foreground/90 leading-snug">{snippet}</p>
                  </div>
                )}
                {agent.forked_from_agent_id && (
                  <p className="text-muted-foreground text-xs italic">Forked from another agent</p>
                )}
              </CardContent>
            </Card>
          </Link>
        );
      })}
    </div>
  );
}

export default function AgentsCatalogPage() {
  const { lang } = useLang();
  const queryClient = useQueryClient();
  const [newOpen, setNewOpen] = useState(false);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["agents", "list"],
    queryFn: () => apiFetch<Agent[]>("/agents"),
    refetchOnWindowFocus: false,
  });

  const builtins = (data ?? []).filter((a) => a.scope === "global_builtin");
  const tenantTemplates = (data ?? []).filter((a) => a.scope === "global_tenant_template");
  const projectLocal = (data ?? []).filter((a) => a.scope === "project_local");

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <Breadcrumb
        items={[
          { label: "Inicio", href: "/admin", icon: <Home className="h-3.5 w-3.5" /> },
          { label: "Agentes" },
        ]}
      />
      <PageHeader
        icon={<Bot className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Catálogo de agentes"
        description="Built-ins de la plataforma, plantillas de tu tenant y agentes locales de proyecto."
        actions={
          <RoleGuard min="tenant_admin">
            <Button onClick={() => setNewOpen(true)} data-testid="new-agent-button">
              <Plus className="mr-1 h-4 w-4" />
              Nuevo agente
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

      {isLoading && (
        <p className="text-muted-foreground text-sm" data-testid="agents-loading">
          Cargando agentes…
        </p>
      )}

      {isError && (
        <Card className="border-destructive p-4" data-testid="agents-error">
          <p className="text-destructive text-sm">
            Could not load agents: {error instanceof ApiError ? error.body : String(error)}
          </p>
        </Card>
      )}

      {data && (
        <Tabs defaultValue="builtin" data-testid="agents-tabs">
          <TabsList>
            <TabsTrigger value="builtin" data-testid="tab-builtin">
              Built-in ({builtins.length})
            </TabsTrigger>
            <TabsTrigger value="template" data-testid="tab-template">
              Plantillas del Tenant ({tenantTemplates.length})
            </TabsTrigger>
            <TabsTrigger value="local" data-testid="tab-local">
              Locales del Proyecto ({projectLocal.length})
            </TabsTrigger>
          </TabsList>

          <TabsContent value="builtin">
            <AgentList
              agents={builtins}
              lang={lang}
              emptyText="No hay built-ins seedeados. Corre python -m api_server.seeds."
            />
          </TabsContent>
          <TabsContent value="template">
            <AgentList
              agents={tenantTemplates}
              lang={lang}
              emptyText="Tu tenant aún no tiene plantillas de agente propias."
            />
          </TabsContent>
          <TabsContent value="local">
            <AgentList
              agents={projectLocal}
              lang={lang}
              emptyText="No hay agentes locales de proyecto. Forkea uno desde un built-in o plantilla."
            />
          </TabsContent>
        </Tabs>
      )}
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
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [role, setRole] = useState("backend_dev");
  const [systemPrompt, setSystemPrompt] = useState("");
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
      setProjectId("");
      onCreated();
    },
  });

  const submitDisabled =
    !name.trim() ||
    !systemPrompt.trim() ||
    (scope === "project_local" && !projectId.trim()) ||
    mutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Nuevo agente</DialogTitle>
          <DialogDescription>
            Crea una plantilla del tenant (reutilizable en todos los proyectos) o un agente local de
            un proyecto específico.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="na-name">Nombre</Label>
              <Input
                id="na-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                data-testid="new-agent-name"
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="na-role">Role</Label>
              <select
                id="na-role"
                value={role}
                onChange={(e) => setRole(e.target.value)}
                className="border-input bg-background rounded-md border px-3 py-2 text-sm"
                data-testid="new-agent-role"
              >
                {ROLE_OPTIONS.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>Descripción</Label>
            <MarkdownTextarea
              value={description}
              onChange={setDescription}
              rows={3}
              data-testid="new-agent-description"
            />
          </div>

          <div className="flex flex-col gap-1.5">
            <Label>System prompt</Label>
            <MarkdownTextarea
              value={systemPrompt}
              onChange={setSystemPrompt}
              rows={6}
              data-testid="new-agent-system-prompt"
            />
          </div>

          <fieldset className="flex flex-col gap-2">
            <legend className="text-sm font-medium">Scope</legend>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="scope"
                checked={scope === "global_tenant_template"}
                onChange={() => setScope("global_tenant_template")}
                data-testid="new-agent-scope-template"
              />
              Plantilla del tenant (reutilizable)
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="radio"
                name="scope"
                checked={scope === "project_local"}
                onChange={() => setScope("project_local")}
                data-testid="new-agent-scope-local"
              />
              Local de un proyecto (requiere project_id)
            </label>
          </fieldset>

          {scope === "project_local" && (
            <div className="flex flex-col gap-1.5">
              <Label>Proyecto</Label>
              <ProjectCombobox
                value={projectId || null}
                onChange={(id) => setProjectId(id ?? "")}
                data-testid="new-agent-project-id"
              />
              <p className="text-muted-foreground text-xs">
                Sólo tus proyectos del tenant — escribe para buscar entre ellos.
              </p>
            </div>
          )}

          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="new-agent-error"
            >
              {mutation.error?.message ?? "Error al crear"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            disabled={submitDisabled}
            onClick={() =>
              mutation.mutate({
                name: name.trim(),
                description: description.trim() || null,
                role,
                system_prompt: systemPrompt,
                scope,
                project_id: scope === "project_local" ? projectId.trim() : null,
              })
            }
            data-testid="new-agent-submit"
          >
            {mutation.isPending ? "Creando…" : "Crear"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
