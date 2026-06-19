"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, Plus, Users } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { StateBlock } from "@/components/shared/state-block";
import { AdoptTeamDialog } from "@/components/teams/adopt-team-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { apiFetch } from "@/lib/api";

interface TeamMember {
  agent_id: string;
  role_in_team: string | null;
  is_team_leader: boolean;
  assignment_priority: number;
}

interface Team {
  id: string;
  tenant_id: string;
  name: string;
  description: string | null;
  members: TeamMember[];
  is_builtin?: boolean;
  forked_from_team_id?: string | null;
}

interface AgentScopeRow {
  id: string;
  scope: string;
}

// El "scope" del equipo se deriva del de sus agentes miembros (igual eje que la
// pantalla de Agentes): built-in (catálogo), plantilla del tenant (agentes
// global_tenant_template) o local del proyecto (algún miembro project_local,
// p.ej. un equipo forkeado a un proyecto vía adopción/ADR 0068).
type TeamScope = "built_in" | "tenant_template" | "project_local";

export default function TeamsListPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [adopting, setAdopting] = useState<Team | null>(null);

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["teams", "list"],
    queryFn: () => apiFetch<Team[]>("/teams"),
    refetchOnWindowFocus: false,
  });

  // Scopes de agentes para clasificar cada equipo por el de sus miembros.
  const agentsQuery = useQuery({
    queryKey: ["agents", "list"],
    queryFn: () => apiFetch<AgentScopeRow[]>("/agents"),
    refetchOnWindowFocus: false,
  });

  const scopeByAgent = useMemo(() => {
    const m = new Map<string, string>();
    for (const a of agentsQuery.data ?? []) m.set(a.id, a.scope);
    return m;
  }, [agentsQuery.data]);

  const teamScope = (team: Team): TeamScope => {
    if (team.is_builtin) return "built_in";
    const hasProjectLocal = team.members.some(
      (mem) => scopeByAgent.get(mem.agent_id) === "project_local",
    );
    return hasProjectLocal ? "project_local" : "tenant_template";
  };

  const teams = data ?? [];
  const builtins = teams.filter((t) => teamScope(t) === "built_in");
  const tenantTemplates = teams.filter((t) => teamScope(t) === "tenant_template");
  const projectLocal = teams.filter((t) => teamScope(t) === "project_local");

  function TeamGrid({ items, emptyText }: { items: Team[]; emptyText: string }) {
    if (items.length === 0) {
      return <p className="text-muted-foreground py-6 text-sm">{emptyText}</p>;
    }
    return (
      <div
        className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
        data-testid="teams-grid"
      >
        {items.map((team) => (
          <Card key={team.id} data-testid={`team-${team.id}`}>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-base">{team.name}</CardTitle>
              {team.is_builtin ? (
                <Badge variant="muted" data-testid="team-builtin-badge">
                  Built-in
                </Badge>
              ) : team.forked_from_team_id ? (
                <Badge variant="info">Adoptado</Badge>
              ) : null}
            </CardHeader>
            <CardContent className="flex flex-col gap-3">
              {team.description && (
                <p className="text-muted-foreground text-sm">{team.description}</p>
              )}
              <p className="text-muted-foreground text-xs">
                {team.members.length} miembro
                {team.members.length === 1 ? "" : "s"}
              </p>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" size="sm" asChild>
                  <Link href={`/admin/teams/${team.id}`}>
                    Ver detalle <ArrowRight className="ml-1 h-3.5 w-3.5" />
                  </Link>
                </Button>
                {team.is_builtin && (
                  <Button
                    size="sm"
                    onClick={() => setAdopting(team)}
                    data-testid={`team-adopt-${team.id}`}
                  >
                    <Plus className="mr-1 h-3.5 w-3.5" /> Adoptar
                  </Button>
                )}
              </div>
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Users className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Equipos"
        description="Built-ins de la plataforma, plantillas de tu tenant y equipos locales de proyecto."
      />

      <StateBlock
        isLoading={isLoading}
        isError={isError}
        error={error}
        loadingLabel="Cargando equipos…"
        loadingTestId="teams-loading"
        errorTitle="Could not load teams"
        errorTestId="teams-error"
      >
        {data && (
          <Tabs defaultValue="builtin" data-testid="teams-tabs">
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
              <TeamGrid
                items={builtins}
                emptyText="No hay equipos built-in seedeados. Corre python -m api_server.seeds."
              />
            </TabsContent>
            <TabsContent value="template">
              <TeamGrid
                items={tenantTemplates}
                emptyText="Tu tenant aún no tiene equipos propios. Adopta un built-in para empezar."
              />
            </TabsContent>
            <TabsContent value="local">
              <TeamGrid
                items={projectLocal}
                emptyText="No hay equipos locales de proyecto. Adopta un equipo a un proyecto o créalo desde el wizard de proyecto."
              />
            </TabsContent>
          </Tabs>
        )}
      </StateBlock>

      {adopting && (
        <AdoptTeamDialog
          team={{ id: adopting.id, name: adopting.name }}
          open={adopting !== null}
          onOpenChange={(v) => !v && setAdopting(null)}
          onAdopted={(newId) => {
            setAdopting(null);
            void queryClient.invalidateQueries({ queryKey: ["teams", "list"] });
            router.push(`/admin/teams/${newId}`);
          }}
        />
      )}
    </div>
  );
}
