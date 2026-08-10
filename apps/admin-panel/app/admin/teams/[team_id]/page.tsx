"use client";

import { useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Pencil, Plus, Trash2, Users } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { CapabilityHub } from "@/components/capability/capability-hub";
import { ChatModelSection, type ChatModelConfig } from "@/components/capability/chat-model-section";
import { AdoptTeamDialog } from "@/components/teams/adopt-team-dialog";
import { ApiError, apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { MEMORY_SCOPE_OPTIONS } from "@/lib/memory/constants";
import { useErrorText } from "@/lib/use-error-text";

import { AddMemberDialog } from "./add-member-dialog";
import { MemberEditDialog } from "./member-edit-dialog";
import { TeamDeleteDialog } from "./team-delete-dialog";
import { TeamEditDialog } from "./team-edit-dialog";
import type { Agent, Project, Team, TeamMember } from "./team-types";
export default function TeamDetailPage() {
  const params = useParams<{ team_id: string }>();
  const teamId = params.team_id;
  const queryClient = useQueryClient();
  const t = useT("teams");
  const errorText = useErrorText();

  const teamQuery = useQuery({
    queryKey: ["teams", teamId],
    queryFn: () => apiFetch<Team>(`/teams/${teamId}`),
    refetchOnWindowFocus: false,
  });

  const agentsQuery = useQuery({
    queryKey: ["agents", "list"],
    queryFn: () => apiFetch<Agent[]>("/agents"),
    refetchOnWindowFocus: false,
  });

  const projectsQuery = useQuery({
    queryKey: ["projects", "list"],
    queryFn: () => apiFetch<Project[]>("/projects"),
    refetchOnWindowFocus: false,
  });

  const [dialogOpen, setDialogOpen] = useState(false);

  // Plan 06.6: edit + delete dialogs.
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  // Ola C-UI: diálogo de adopción de un equipo built-in.
  const [adoptOpen, setAdoptOpen] = useState(false);
  // Plan 06.17 task_06_17_15: edición de metadata de miembro (líder/prioridad/rol).
  const [memberEditing, setMemberEditing] = useState<TeamMember | null>(null);
  const router = useRouter();

  // Ola A-UI: fija el modelo por defecto del equipo (PUT /teams/{id}).
  const saveModel = useMutation<Team, ApiError, ChatModelConfig>({
    mutationFn: (modelConfig) =>
      apiFetch<Team>(`/teams/${teamId}`, {
        method: "PUT",
        body: { model_config: modelConfig },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["teams", teamId] });
      void queryClient.invalidateQueries({ queryKey: ["teams", "list"] });
    },
  });

  // Modelo del CHAT del equipo (proveedor concreto; PUT /teams/{id}).
  const saveChatModel = useMutation<Team, ApiError, ChatModelConfig>({
    mutationFn: (chatModelConfig) =>
      apiFetch<Team>(`/teams/${teamId}`, {
        method: "PUT",
        body: { chat_model_config: chatModelConfig },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["teams", teamId] });
    },
  });

  // ADR 0071: fija/quita la política de memoria del equipo (PUT /teams/{id}).
  // `null` = sin política (los miembros caen al scope del agente / plataforma).
  const saveMemoryScope = useMutation<Team, ApiError, string | null>({
    mutationFn: (scope) =>
      apiFetch<Team>(`/teams/${teamId}`, {
        method: "PUT",
        body: { memory_scope: scope },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["teams", teamId] });
      void queryClient.invalidateQueries({ queryKey: ["teams", "list"] });
    },
  });

  const team = teamQuery.data;
  const agents = agentsQuery.data ?? [];
  const projects = (projectsQuery.data ?? []).filter((p) => !p.is_template);
  const agentsById = new Map(agents.map((a) => [a.id, a] as const));

  const isReadOnly = team?.is_builtin === true;

  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-8 sm:px-6 lg:px-8">
      <PageHeader
        icon={<Users className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={<span data-testid="team-name">{team?.name ?? t("fallbackName")}</span>}
        description={team?.description ?? t("fallbackDescription")}
        actions={
          <div className="flex gap-2">
            <Button variant="outline" asChild>
              <Link href="/admin/teams">
                <ArrowLeft className="mr-1 h-4 w-4" /> {t("back")}
              </Link>
            </Button>
            {team && !isReadOnly && (
              <>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setEditOpen(true)}
                  data-testid="team-edit-button"
                >
                  <Pencil className="mr-1 h-4 w-4" />
                  {t("edit")}
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setDeleteOpen(true)}
                  data-testid="team-delete-button"
                >
                  <Trash2 className="mr-1 h-4 w-4" />
                  {t("delete")}
                </Button>
              </>
            )}
            {team && isReadOnly && (
              <Button size="sm" onClick={() => setAdoptOpen(true)} data-testid="team-adopt-button">
                <Plus className="mr-1 h-4 w-4" />
                {t("adoptCustomize")}
              </Button>
            )}
          </div>
        }
      />
      {team && !isReadOnly && (
        <>
          <TeamEditDialog
            team={team}
            open={editOpen}
            onOpenChange={setEditOpen}
            onSaved={() => {
              void queryClient.invalidateQueries({ queryKey: ["teams", teamId] });
              void queryClient.invalidateQueries({ queryKey: ["teams", "list"] });
              setEditOpen(false);
            }}
          />
          <TeamDeleteDialog
            team={team}
            open={deleteOpen}
            onOpenChange={setDeleteOpen}
            onDeleted={() => {
              setDeleteOpen(false);
              router.push("/admin/teams");
            }}
          />
        </>
      )}
      {team && isReadOnly && (
        <AdoptTeamDialog
          team={{ id: team.id, name: team.name }}
          open={adoptOpen}
          onOpenChange={setAdoptOpen}
          onAdopted={(newId) => {
            setAdoptOpen(false);
            void queryClient.invalidateQueries({ queryKey: ["teams", "list"] });
            router.push(`/admin/teams/${newId}`);
          }}
        />
      )}

      {teamQuery.isLoading && <p className="text-muted-foreground text-sm">{t("loadingTeam")}</p>}

      {teamQuery.isError && (
        <Card className="border-destructive p-4">
          {/* `errorText` (prod-16 `task_prod16_05`): esto pintaba `error.body` CRUDO. */}
          <p className="text-destructive text-sm">
            {t("detailErrorTitle")} {errorText(teamQuery.error)}
          </p>
        </Card>
      )}

      {team && (
        <>
          {/* Plan 06.17 task_06_17_15 (ADR 0053): "qué sabe el equipo" — la
              capacidad de equipo es la UNIÓN AGREGADA read-only de la de sus
              miembros, no un subsistema TeamKB nuevo. Consume
              GET /teams/{id}/capabilities. */}
          <div className="mb-6">
            <CapabilityHub entityType="team" entityId={teamId} />
          </div>
          {/* Modelo de EJECUCIÓN del equipo (ADR 0065): proveedor concreto por nombre,
              uniforme con chat y asistente. Read-only en built-in (se personaliza adoptando). */}
          {isReadOnly ? (
            /* P1-9 (investigación 2026-07-11): la palanca EXISTE pero era
               indescubrible — nada decía CÓMO cambiar el modelo/effort de un
               equipo built-in. La vía es adoptarlo (fork del tenant, ADR 0066). */
            <p
              className="text-muted-foreground mb-3 rounded-md border border-dashed px-3 py-2 text-sm"
              data-testid="team-model-adopt-hint"
            >
              {t("modelAdoptHintPrefix")}
              <strong>{t("modelAdoptHintStrong")}</strong>
              {t("modelAdoptHintSuffix")}
            </p>
          ) : null}
          <div className="mb-6">
            <ChatModelSection
              value={team.model_config}
              isReadOnly={isReadOnly}
              pending={saveModel.isPending}
              idPrefix="team-exec"
              title={{ es: "Modelo del equipo", en: "Team model" }}
              description={{
                es:
                  "Proveedor + modelo por defecto del equipo, que heredan sus agentes sin " +
                  "modelo propio. Vacío = heredar del nivel superior (proyecto → plataforma).",
                en:
                  "The team's default provider + model, inherited by its agents without their " +
                  "own. Empty = inherit from the level above (project → platform).",
              }}
              onSave={(cfg) => saveModel.mutate(cfg)}
            />
          </div>
          {/* Modelo del CHAT del equipo (Feature B): proveedor concreto por nombre. */}
          <div className="mb-6">
            <ChatModelSection
              value={team.chat_model_config}
              isReadOnly={isReadOnly}
              pending={saveChatModel.isPending}
              idPrefix="team"
              onSave={(cfg) => saveChatModel.mutate(cfg)}
            />
          </div>
          {/* ADR 0071: política de memoria del equipo. Gobierna a sus miembros. */}
          <div className="mb-6">
            <Card className="p-4">
              <Label htmlFor="team-memory-scope" className="text-sm font-medium">
                {t("memoryPolicyLabel")}
              </Label>
              <p className="text-muted-foreground mt-1 text-xs">{t("memoryPolicyHelp")}</p>
              <Select
                id="team-memory-scope"
                data-testid="team-memory-scope"
                className="mt-2 max-w-xs"
                value={team.memory_scope ?? ""}
                disabled={isReadOnly || saveMemoryScope.isPending}
                onChange={(e) => saveMemoryScope.mutate(e.target.value || null)}
              >
                <option value="">{t("memoryPolicyNone")}</option>
                {MEMORY_SCOPE_OPTIONS.map((o) => (
                  <option key={o.value} value={o.value}>
                    {o.label}
                  </option>
                ))}
              </Select>
            </Card>
          </div>
        </>
      )}

      {team && (
        <section data-testid="team-detail">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="text-lg font-semibold">
              {t("membersHeading", { n: team.members.length })}
              {isReadOnly && (
                <Badge variant="muted" className="ml-2">
                  {t("builtinBadge")}
                </Badge>
              )}
            </h2>
            <Button
              data-testid="add-member-button"
              onClick={() => setDialogOpen(true)}
              disabled={isReadOnly}
              title={isReadOnly ? t("addMemberDisabled") : undefined}
            >
              <Plus className="mr-1 h-4 w-4" /> {t("addMember")}
            </Button>
          </div>

          {team.members.length === 0 ? (
            <p
              className="text-muted-foreground py-8 text-center text-sm"
              data-testid="members-empty"
            >
              {t("membersEmpty")}
            </p>
          ) : (
            <div className="flex flex-col gap-2" data-testid="members-list">
              {team.members.map((m) => {
                const agent = agentsById.get(m.agent_id);
                return (
                  <Card key={m.agent_id} data-testid={`member-${m.agent_id}`} className="p-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="font-medium">{agent?.name ?? t("unknownAgent")}</p>
                        <p className="text-muted-foreground text-xs">
                          {m.role_in_team ?? agent?.role ?? "—"}
                          {agent &&
                            (agent.forked_from_agent_id ? (
                              <Badge
                                variant="warning"
                                className="ml-2"
                                data-testid={`member-forked-${agent.id}`}
                              >
                                {t("forkedBadge")}
                              </Badge>
                            ) : (
                              <Badge
                                variant="muted"
                                className="ml-2"
                                data-testid={`member-linked-${agent.id}`}
                              >
                                {t("linkedBadge")}
                              </Badge>
                            ))}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        {m.is_team_leader && (
                          <Badge variant="warning" data-testid="leader-badge">
                            {t("leaderBadge")}
                          </Badge>
                        )}
                        <Badge variant="muted" data-testid={`member-priority-${m.agent_id}`}>
                          {t("priorityBadge", { n: m.assignment_priority })}
                        </Badge>
                        {!isReadOnly && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setMemberEditing(m)}
                            data-testid={`member-edit-${m.agent_id}`}
                          >
                            <Pencil className="mr-1 h-4 w-4" />
                            {t("edit")}
                          </Button>
                        )}
                      </div>
                    </div>
                  </Card>
                );
              })}
            </div>
          )}
        </section>
      )}
      <AddMemberDialog
        teamId={teamId}
        agents={agents}
        projects={projects}
        memberIds={team?.members.map((m) => m.agent_id) ?? []}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        onAdded={() => {
          queryClient.invalidateQueries({ queryKey: ["teams", teamId] });
          queryClient.invalidateQueries({ queryKey: ["agents", "list"] });
          setDialogOpen(false);
        }}
      />

      {memberEditing && (
        <MemberEditDialog
          teamId={teamId}
          member={memberEditing}
          agentName={agentsById.get(memberEditing.agent_id)?.name ?? t("unknownAgent")}
          open={memberEditing !== null}
          onOpenChange={(next) => {
            if (!next) setMemberEditing(null);
          }}
          onSaved={() => {
            void queryClient.invalidateQueries({ queryKey: ["teams", teamId] });
            setMemberEditing(null);
          }}
        />
      )}
    </div>
  );
}
