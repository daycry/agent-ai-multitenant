"use client";

import { useQuery } from "@tanstack/react-query";
import { Bot } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
          <Card key={agent.id} data-testid={`agent-${agent.id}`}>
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
        );
      })}
    </div>
  );
}

export default function AgentsCatalogPage() {
  const { lang } = useLang();
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
      <PageHeader
        icon={<Bot className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Catálogo de agentes"
        description="Built-ins de la plataforma, plantillas de tu tenant y agentes locales de proyecto."
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
