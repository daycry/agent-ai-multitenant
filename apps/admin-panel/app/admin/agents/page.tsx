"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, Home, Plus } from "lucide-react";

import { Breadcrumb } from "@/components/layout/breadcrumb";
import {
  ResourceCard,
  ResourceDetailPanel,
  ResourceHeader,
  ResourceLayout,
  ResourceToolbar,
} from "@/components/resource";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { RoleGuard } from "@/components/ui/role-guard";
import { Select } from "@/components/ui/select";
import { EmptyState } from "@/components/ui/empty-state";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StateBlock } from "@/components/shared/state-block";
import { apiFetch } from "@/lib/api";
import { pickLang, useT, type MessageKey } from "@/lib/i18n";
import { useLang, type Lang } from "@/lib/lang-context";

import type { Agent } from "./agent-types";
import { NewAgentDialog } from "./new-agent-dialog";

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

function AgentList({
  agents,
  emptyText,
  lang,
  selectedId,
  onSelect,
}: {
  agents: Agent[];
  emptyText: string;
  lang: Lang;
  selectedId: string | null;
  onSelect: (id: string) => void;
}) {
  const t = useT("agents");
  const tr = useT("resource");
  if (agents.length === 0) {
    return (
      <EmptyState className="mt-2" icon={Bot} title={t("emptyTitle")} description={emptyText} />
    );
  }
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3" data-testid="agents-grid">
      {agents.map((agent) => {
        const snippet = promptIn(agent, lang);
        const provenance = agent.forked_from_agent_id
          ? tr("provForked")
          : agent.scope === "global_builtin"
            ? tr("provBuiltin")
            : tr("provOwn");
        const n = agent.teams.length;
        return (
          <ResourceCard
            key={agent.id}
            testId={`agent-${agent.id}`}
            name={agent.name}
            selected={selectedId === agent.id}
            onSelect={() => onSelect(agent.id)}
            provenance={provenance}
            scope={SCOPE_LABEL_KEY[agent.scope] ? t(SCOPE_LABEL_KEY[agent.scope]) : agent.scope}
            usage={
              n === 0
                ? tr("noTeam")
                : tr(n === 1 ? "usageTeamsOne" : "usageTeamsMany", { count: n })
            }
            lastActivity={
              agent.last_activity_at
                ? new Date(agent.last_activity_at).toLocaleDateString(lang)
                : undefined
            }
            description={
              agent.description ? <p className="text-sm">{agent.description}</p> : undefined
            }
            extra={
              <>
                <p className="text-muted-foreground text-xs">
                  <span className="font-medium">{t("cardRole")}</span> {agent.role}
                  {agent.agent_type !== "ai" && (
                    <span className="ml-2 italic">({agent.agent_type})</span>
                  )}
                </p>
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
                  {n > 0 ? (
                    agent.teams.map((tm) => (
                      <Badge key={tm.id} variant="muted" className="text-[10px]">
                        {tm.name}
                      </Badge>
                    ))
                  ) : (
                    <span className="text-muted-foreground text-[10px] italic">
                      {t("filterNoTeam")}
                    </span>
                  )}
                </div>
              </>
            }
            actions={
              <Button variant="outline" size="sm" asChild>
                <Link href={`/admin/agents/${agent.id}`} data-testid={`agent-link-${agent.id}`}>
                  {tr("openFileShort")}
                </Link>
              </Button>
            }
          />
        );
      })}
    </div>
  );
}

export default function AgentsCatalogPage() {
  const t = useT("agents");
  const tr = useT("resource");
  const { lang } = useLang();
  const queryClient = useQueryClient();
  const [newOpen, setNewOpen] = useState(false);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");

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

  const q = search.trim().toLowerCase();
  const filtered = (data ?? []).filter((a) => {
    if (membership === "in_team" && a.teams.length === 0) return false;
    if (membership === "no_team" && a.teams.length > 0) return false;
    if (teamFilter && !a.teams.some((tm) => tm.id === teamFilter)) return false;
    if (q && !`${a.name} ${a.role} ${a.description ?? ""}`.toLowerCase().includes(q)) return false;
    return true;
  });
  const builtins = filtered.filter((a) => a.scope === "global_builtin");
  const tenantTemplates = filtered.filter((a) => a.scope === "global_tenant_template");
  const projectLocal = filtered.filter((a) => a.scope === "project_local");

  const selected = (data ?? []).find((a) => a.id === selectedId) ?? null;
  const total = (data ?? []).length;
  const list = (items: Agent[], emptyText: string) => (
    <AgentList
      agents={items}
      lang={lang}
      emptyText={q ? tr("noMatches") : emptyText}
      selectedId={selectedId}
      onSelect={setSelectedId}
    />
  );

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <Breadcrumb
        items={[
          { label: t("home"), href: "/admin", icon: <Home className="h-3.5 w-3.5" /> },
          { label: t("agents") },
        ]}
      />
      <ResourceHeader
        icon={<Bot className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={t("catalogTitle")}
        description={t("catalogDescription")}
        count={data ? total : undefined}
        countLabel={tr(total === 1 ? "countAgentsOne" : "countAgentsMany", { count: total })}
        action={
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
          <div data-testid="agents-filters">
            <ResourceToolbar
              search={search}
              onSearchChange={setSearch}
              searchPlaceholder={tr("searchAgents")}
              searchTestId="agents-search"
            >
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
                    {(teamsQuery.data ?? []).map((tm) => (
                      <option key={tm.id} value={tm.id}>
                        {tm.name}
                      </option>
                    ))}
                  </Select>
                </div>
              </div>
            </ResourceToolbar>
          </div>
        )}
        {data && (
          <ResourceLayout
            panel={
              selected ? (
                <ResourceDetailPanel
                  title={selected.name}
                  detailHref={`/admin/agents/${selected.id}`}
                  onClose={() => setSelectedId(null)}
                  fields={[
                    { key: "role", label: tr("fieldRole"), value: selected.role },
                    ...(selected.description
                      ? [
                          {
                            key: "description",
                            label: tr("fieldDescription"),
                            value: selected.description,
                          },
                        ]
                      : []),
                    {
                      key: "teams",
                      label: tr("fieldTeams"),
                      value:
                        selected.teams.length > 0
                          ? selected.teams.map((tm) => tm.name).join(", ")
                          : tr("noTeam"),
                    },
                  ]}
                />
              ) : undefined
            }
            list={
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

                <TabsContent value="builtin">{list(builtins, t("emptyBuiltins"))}</TabsContent>
                <TabsContent value="template">
                  {list(tenantTemplates, t("emptyTemplates"))}
                </TabsContent>
                <TabsContent value="local">{list(projectLocal, t("emptyLocal"))}</TabsContent>
              </Tabs>
            }
          />
        )}
      </StateBlock>
    </div>
  );
}
