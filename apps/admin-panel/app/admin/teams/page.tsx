"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Users } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { StateBlock } from "@/components/shared/state-block";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
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
}

export default function TeamsListPage() {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["teams", "list"],
    queryFn: () => apiFetch<Team[]>("/teams"),
    refetchOnWindowFocus: false,
  });

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Users className="h-6 w-6 sm:h-7 sm:w-7" />}
        title="Equipos"
        description="Plantillas built-in y equipos propios del tenant."
      />

      <StateBlock
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={Boolean(data && data.length === 0)}
        loadingLabel="Cargando equipos…"
        loadingTestId="teams-loading"
        errorTitle="Could not load teams"
        errorTestId="teams-error"
        emptyIcon={Users}
        emptyTitle="No hay equipos visibles"
        emptyDescription={
          <>
            Si esperabas built-ins, corre{" "}
            <code className="bg-muted rounded px-1 py-0.5 text-xs">python -m api_server.seeds</code>
            .
          </>
        }
      >
        <div
          className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
          data-testid="teams-grid"
        >
          {(data ?? []).map((team) => (
            <Card key={team.id} data-testid={`team-${team.id}`}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-base">{team.name}</CardTitle>
                {team.is_builtin && (
                  <Badge variant="muted" data-testid="team-builtin-badge">
                    Built-in
                  </Badge>
                )}
              </CardHeader>
              <CardContent className="flex flex-col gap-3">
                {team.description && (
                  <p className="text-muted-foreground text-sm">{team.description}</p>
                )}
                <p className="text-muted-foreground text-xs">
                  {team.members.length} miembro
                  {team.members.length === 1 ? "" : "s"}
                </p>
                <Button variant="outline" size="sm" asChild>
                  <Link href={`/admin/teams/${team.id}`}>
                    Ver detalle <ArrowRight className="ml-1 h-3.5 w-3.5" />
                  </Link>
                </Button>
              </CardContent>
            </Card>
          ))}
        </div>
      </StateBlock>
    </div>
  );
}
