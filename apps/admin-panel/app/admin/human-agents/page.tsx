"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clock, Coins, GitFork, Home, Mail, UserRound, UsersRound } from "lucide-react";

import {
  ResourceCard,
  ResourceDetailPanel,
  ResourceHeader,
  ResourceLayout,
  ResourceToolbar,
} from "@/components/resource";
import { Breadcrumb } from "@/components/layout/breadcrumb";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { RoleGuard } from "@/components/ui/role-guard";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ApiError, apiFetch } from "@/lib/api";
import { useT, type MessageKey } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

import { HumanAgentFormDialog } from "./human-agent-form-dialog";

export interface HumanAgentConfig {
  id: string;
  agent_id: string;
  assignment_mode: string;
  assigned_user_id: string | null;
  hourly_rate: string | null;
  hourly_rate_currency: string | null;
  notification_channels: string[];
  acceptance_timeout_hours: number;
  escalation_target_user_id: string | null;
  expected_response_time_hours: number | null;
  expected_execution_time_hours: number | null;
}

export interface HumanAgent {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  avatar_url: string | null;
  agent_type: string;
  role: string;
  scope: string;
  is_template: boolean;
  forked_from_agent_id: string | null;
  config: HumanAgentConfig | null;
}

export interface AssignableUser {
  user_id: string;
  email: string;
  full_name: string | null;
  role: string;
}

// ---------------------------------------------------------------------------
// Tenant Human-Agent card
// ---------------------------------------------------------------------------
const SCOPE_LABEL_KEY: Record<string, MessageKey<"agents">> = {
  global_builtin: "scopeBuiltin",
  global_tenant_template: "scopeTenantTemplate",
  project_local: "scopeProjectLocal",
};

function HumanAgentCard({
  agent,
  users,
  onEdit,
  selected,
  onSelect,
}: {
  agent: HumanAgent;
  users: AssignableUser[];
  onEdit: (agent: HumanAgent) => void;
  selected: boolean;
  onSelect: () => void;
}) {
  const tr = useT("resource");
  const ta = useT("agents");
  const cfg = agent.config;
  const assigned = users.find((u) => u.user_id === cfg?.assigned_user_id);
  const escalation = users.find((u) => u.user_id === cfg?.escalation_target_user_id);

  return (
    <ResourceCard
      testId={`human-agent-${agent.id}`}
      name={agent.name}
      selected={selected}
      onSelect={onSelect}
      badge={<Badge variant="info">humano</Badge>}
      provenance={agent.forked_from_agent_id ? tr("provForked") : tr("provOwn")}
      scope={SCOPE_LABEL_KEY[agent.scope] ? ta(SCOPE_LABEL_KEY[agent.scope]) : agent.scope}
      description={
        agent.description ? <p className="line-clamp-2 text-xs">{agent.description}</p> : undefined
      }
      extra={
        <>
          <span className="text-muted-foreground text-xs">{agent.role}</span>
          <dl className="flex flex-col gap-1.5 text-xs">
            <div className="flex items-center gap-2">
              <UserRound className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
              <dt className="sr-only">Usuario asignado</dt>
              <dd data-testid={`ha-assigned-${agent.id}`}>
                {assigned ? (
                  <span className="font-medium">{assigned.full_name ?? assigned.email}</span>
                ) : (
                  <span className="text-warning-soft-foreground italic">Sin asignar</span>
                )}
              </dd>
            </div>
            {cfg?.hourly_rate && (
              <div className="flex items-center gap-2">
                <Coins className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
                <dd>
                  {cfg.hourly_rate} {cfg.hourly_rate_currency ?? ""} / h
                </dd>
              </div>
            )}
            <div className="flex items-center gap-2">
              <Clock className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
              <dd>Aceptación: {cfg?.acceptance_timeout_hours ?? 24} h</dd>
            </div>
            {escalation && (
              <div className="flex items-center gap-2">
                <UsersRound className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
                <dd>Escala a: {escalation.full_name ?? escalation.email}</dd>
              </div>
            )}
            {cfg && cfg.notification_channels.length > 0 && (
              <div className="flex items-center gap-2">
                <Mail className="text-muted-foreground h-3.5 w-3.5 shrink-0" />
                <dd className="flex flex-wrap gap-1">
                  {cfg.notification_channels.map((ch) => (
                    <Badge key={ch} variant="muted">
                      {ch}
                    </Badge>
                  ))}
                </dd>
              </div>
            )}
          </dl>
        </>
      }
      actions={
        <RoleGuard min="tenant_admin">
          <Button
            size="sm"
            variant="outline"
            onClick={() => onEdit(agent)}
            data-testid={`ha-edit-${agent.id}`}
          >
            Editar
          </Button>
        </RoleGuard>
      }
    />
  );
}

// ---------------------------------------------------------------------------
// Global template card (clone-and-fork)
// ---------------------------------------------------------------------------
function TemplateCard({ template, onForked }: { template: HumanAgent; onForked: () => void }) {
  const errorText = useErrorText();
  const mutation = useMutation<HumanAgent, ApiError, void>({
    mutationFn: () =>
      apiFetch<HumanAgent>(`/human-agents/templates/${template.id}/clone`, {
        method: "POST",
        body: {},
      }),
    onSuccess: onForked,
  });

  return (
    <Card data-testid={`ha-template-${template.id}`} className="flex h-full flex-col">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-base">{template.name}</CardTitle>
        <Badge variant="muted">plantilla global</Badge>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3 text-sm">
        <span className="text-muted-foreground text-xs">{template.role}</span>
        {template.description && (
          <p className="text-muted-foreground line-clamp-3 text-xs">{template.description}</p>
        )}
        <div className="mt-auto flex flex-col gap-1 pt-2">
          <RoleGuard
            min="tenant_admin"
            fallback={
              <p className="text-muted-foreground text-xs italic">
                Sólo un admin del tenant puede clonar.
              </p>
            }
          >
            <Button
              size="sm"
              onClick={() => mutation.mutate()}
              disabled={mutation.isPending}
              data-testid={`ha-clone-${template.id}`}
            >
              <GitFork className="mr-1 h-4 w-4" />
              {mutation.isPending ? "Clonando…" : "Clonar y forkar"}
            </Button>
          </RoleGuard>
          {mutation.isError && (
            <p className="text-destructive text-xs" data-testid={`ha-clone-error-${template.id}`}>
              {errorText(mutation.error)}
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------
export default function HumanAgentsPage() {
  const errorText = useErrorText();
  const tr = useT("resource");
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<HumanAgent | null>(null);

  const agentsQuery = useQuery({
    queryKey: ["human-agents", "list"],
    queryFn: () => apiFetch<HumanAgent[]>("/human-agents"),
    refetchOnWindowFocus: false,
  });

  const templatesQuery = useQuery({
    queryKey: ["human-agents", "templates"],
    queryFn: () => apiFetch<HumanAgent[]>("/human-agents/templates"),
    refetchOnWindowFocus: false,
  });

  const usersQuery = useQuery({
    queryKey: ["human-agents", "assignable-users"],
    queryFn: () => apiFetch<AssignableUser[]>("/human-agents/assignable-users"),
    refetchOnWindowFocus: false,
  });

  const allAgents = agentsQuery.data ?? [];
  const q = search.trim().toLowerCase();
  const agents = q
    ? allAgents.filter((a) =>
        `${a.name} ${a.role} ${a.description ?? ""}`.toLowerCase().includes(q),
      )
    : allAgents;
  const selected = allAgents.find((a) => a.id === selectedId) ?? null;
  const templates = templatesQuery.data ?? [];
  const users = usersQuery.data ?? [];
  const selectedUser = users.find((u) => u.user_id === selected?.config?.assigned_user_id);

  function refreshAgents() {
    void queryClient.invalidateQueries({ queryKey: ["human-agents", "list"] });
  }

  function openCreate() {
    setEditing(null);
    setFormOpen(true);
  }

  function openEdit(agent: HumanAgent) {
    setEditing(agent);
    setFormOpen(true);
  }

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <Breadcrumb
        items={[
          { label: "Inicio", href: "/admin", icon: <Home className="h-3.5 w-3.5" /> },
          { label: "Agentes humanos" },
        ]}
      />
      <ResourceHeader
        icon={<UserRound className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Agentes humanos"
        description="Personas (o roles) asignables a tareas del plan igual que un agente IA. Configura asignación, tarifa, canales de notificación y escalación."
        count={agentsQuery.data ? allAgents.length : undefined}
        countLabel={tr(allAgents.length === 1 ? "countHumansOne" : "countHumansMany", {
          count: allAgents.length,
        })}
        action={
          <RoleGuard min="tenant_admin">
            <Button onClick={openCreate} data-testid="new-human-agent-button">
              <UserRound className="mr-1 h-4 w-4" />
              Nuevo agente humano
            </Button>
          </RoleGuard>
        }
      />

      <HumanAgentFormDialog
        open={formOpen}
        onOpenChange={setFormOpen}
        editing={editing}
        users={users}
        onSaved={() => {
          refreshAgents();
          setFormOpen(false);
        }}
      />

      <Tabs defaultValue="mine" data-testid="human-agents-tabs">
        <TabsList>
          <TabsTrigger value="mine" data-testid="tab-mine">
            Mis agentes humanos ({allAgents.length})
          </TabsTrigger>
          <TabsTrigger value="templates" data-testid="tab-templates">
            Plantillas globales ({templates.length})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="mine">
          {agentsQuery.isLoading && (
            <p className="text-muted-foreground text-sm" data-testid="human-agents-loading">
              Cargando agentes humanos…
            </p>
          )}
          {agentsQuery.isError && (
            <Card className="border-destructive p-4" data-testid="human-agents-error">
              <p className="text-destructive text-sm">
                No se pudieron cargar los agentes: {errorText(agentsQuery.error)}
              </p>
            </Card>
          )}
          <ResourceToolbar
            search={search}
            onSearchChange={setSearch}
            searchPlaceholder={tr("searchHumans")}
            searchTestId="human-agents-search"
          />
          {!agentsQuery.isLoading && !agentsQuery.isError && allAgents.length === 0 && (
            <p
              className="text-muted-foreground py-8 text-center text-sm"
              data-testid="human-agents-empty"
            >
              Tu tenant aún no tiene agentes humanos. Crea uno o clona una plantilla global.
            </p>
          )}
          {allAgents.length > 0 && agents.length === 0 && (
            <p className="text-muted-foreground py-8 text-center text-sm">{tr("noMatches")}</p>
          )}
          {agents.length > 0 && (
            <ResourceLayout
              panel={
                selected ? (
                  <ResourceDetailPanel
                    title={selected.name}
                    onClose={() => setSelectedId(null)}
                    fields={[
                      { key: "role", label: tr("fieldRole"), value: selected.role },
                      {
                        key: "assigned",
                        label: tr("fieldAssignedTo"),
                        value: selectedUser
                          ? (selectedUser.full_name ?? selectedUser.email)
                          : tr("unassigned"),
                      },
                      ...(selected.description
                        ? [
                            {
                              key: "description",
                              label: tr("fieldDescription"),
                              value: selected.description,
                            },
                          ]
                        : []),
                    ]}
                  />
                ) : undefined
              }
              list={
                <div
                  className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3"
                  data-testid="human-agents-grid"
                >
                  {agents.map((agent) => (
                    <HumanAgentCard
                      key={agent.id}
                      agent={agent}
                      users={users}
                      onEdit={openEdit}
                      selected={selectedId === agent.id}
                      onSelect={() => setSelectedId(agent.id)}
                    />
                  ))}
                </div>
              }
            />
          )}
        </TabsContent>

        <TabsContent value="templates">
          {templatesQuery.isLoading && (
            <p className="text-muted-foreground text-sm" data-testid="templates-loading">
              Cargando plantillas…
            </p>
          )}
          {!templatesQuery.isLoading && templates.length === 0 && (
            <p
              className="text-muted-foreground py-8 text-center text-sm"
              data-testid="templates-empty"
            >
              No hay plantillas globales seedeadas. Corre python -m api_server.seeds.
            </p>
          )}
          {templates.length > 0 && (
            <div
              className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
              data-testid="templates-grid"
            >
              {templates.map((template) => (
                <TemplateCard key={template.id} template={template} onForked={refreshAgents} />
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
