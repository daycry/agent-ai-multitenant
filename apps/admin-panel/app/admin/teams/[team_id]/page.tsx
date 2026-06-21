"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Pencil, Plus, Trash2, Users } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
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
import { Select } from "@/components/ui/select";
import { Spinner } from "@/components/ui/spinner";
import { CapabilityHub } from "@/components/capability/capability-hub";
import { DefaultModelSection } from "@/components/capability/default-model-section";
import { AdoptTeamDialog } from "@/components/teams/adopt-team-dialog";
import { ApiError, apiFetch } from "@/lib/api";
import { MEMORY_SCOPE_OPTIONS } from "@/lib/memory/constants";
import { type ModelConfig } from "@/lib/persona/persona";

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
  is_builtin: boolean;
  // Ola C: enlace al built-in origen si el equipo fue adoptado (badge/origen).
  forked_from_team_id: string | null;
  // Ola A: modelo por defecto del equipo (alias JSON `model_config`). {} = hereda.
  model_config: ModelConfig;
  // Modelo del CHAT del equipo (separado del de ejecución). {} = hereda del de ejecución.
  chat_model_config: ModelConfig;
  // ADR 0071: política de memoria del equipo (null = sin política / heredar).
  memory_scope: string | null;
  members: TeamMember[];
}

interface Agent {
  id: string;
  name: string;
  role: string;
  scope: string;
  project_id: string | null;
  // Plan 06.17 task_06_17_12: el badge Linked/Forked se deriva de este campo,
  // no del scope (que mentía: un fork project_local se mostraba "Forked" aunque
  // no tuviera origen, y un template linked aparecía como "Linked (tenant)").
  forked_from_agent_id: string | null;
}

interface Project {
  id: string;
  name: string;
  is_template: boolean;
}

type Mode = "linked" | "forked";

export default function TeamDetailPage() {
  const params = useParams<{ team_id: string }>();
  const teamId = params.team_id;
  const queryClient = useQueryClient();

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
  const [selectedAgentId, setSelectedAgentId] = useState<string>("");
  const [mode, setMode] = useState<Mode>("linked");
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Plan 06.6: edit + delete dialogs.
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  // Ola C-UI: diálogo de adopción de un equipo built-in.
  const [adoptOpen, setAdoptOpen] = useState(false);
  // Plan 06.17 task_06_17_15: edición de metadata de miembro (líder/prioridad/rol).
  const [memberEditing, setMemberEditing] = useState<TeamMember | null>(null);
  const router = useRouter();

  function resetDialog() {
    setSelectedAgentId("");
    setMode("linked");
    setSelectedProjectId("");
    setSubmitError(null);
  }

  const addMember = useMutation({
    mutationFn: async () => {
      if (!selectedAgentId) {
        throw new Error("Selecciona un agente.");
      }
      let agentId = selectedAgentId;
      if (mode === "forked") {
        if (!selectedProjectId) {
          throw new Error("Selecciona un proyecto destino para el fork.");
        }
        const fork = await apiFetch<Agent>(`/agents/${selectedAgentId}/fork`, {
          method: "POST",
          body: { project_id: selectedProjectId },
        });
        agentId = fork.id;
      }
      await apiFetch<Team>(`/teams/${teamId}/members`, {
        method: "POST",
        body: { agent_id: agentId },
      });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["teams", teamId] });
      queryClient.invalidateQueries({ queryKey: ["agents", "list"] });
      setDialogOpen(false);
      resetDialog();
    },
    onError: (err: unknown) => {
      setSubmitError(err instanceof ApiError ? err.body : String(err));
    },
  });

  // Ola A-UI: fija el modelo por defecto del equipo (PUT /teams/{id}).
  const saveModel = useMutation<Team, ApiError, ModelConfig>({
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

  // Modelo del CHAT del equipo (separado del de ejecución; PUT /teams/{id}).
  const saveChatModel = useMutation<Team, ApiError, ModelConfig>({
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
        title={<span data-testid="team-name">{team?.name ?? "Equipo"}</span>}
        description={team?.description ?? "Detalle del equipo."}
        actions={
          <div className="flex gap-2">
            <Button variant="outline" asChild>
              <Link href="/admin/teams">
                <ArrowLeft className="mr-1 h-4 w-4" /> Volver
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
                  Editar
                </Button>
                <Button
                  variant="destructive"
                  size="sm"
                  onClick={() => setDeleteOpen(true)}
                  data-testid="team-delete-button"
                >
                  <Trash2 className="mr-1 h-4 w-4" />
                  Borrar
                </Button>
              </>
            )}
            {team && isReadOnly && (
              <Button size="sm" onClick={() => setAdoptOpen(true)} data-testid="team-adopt-button">
                <Plus className="mr-1 h-4 w-4" />
                Adoptar / Personalizar
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

      {teamQuery.isLoading && <p className="text-muted-foreground text-sm">Cargando equipo…</p>}

      {teamQuery.isError && (
        <Card className="border-destructive p-4">
          <p className="text-destructive text-sm">
            Could not load team:{" "}
            {teamQuery.error instanceof ApiError ? teamQuery.error.body : String(teamQuery.error)}
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
          {/* Ola A-UI: modelo por defecto del equipo (cadena de herencia, ADR 0065).
              Read-only en built-in (se personaliza adoptando). */}
          <div className="mb-6">
            <DefaultModelSection
              value={team.model_config}
              isReadOnly={isReadOnly}
              pending={saveModel.isPending}
              idPrefix="team"
              scopeLabel={{ es: "del equipo", en: "(team)" }}
              onSave={(modelConfig) => saveModel.mutate(modelConfig)}
            />
          </div>
          {/* Modelo del CHAT del equipo (separado del de ejecución): el equipo responde
              en el chat de planificación con este modelo. Conviene uno más rápido/ligero. */}
          <div className="mb-6">
            <DefaultModelSection
              value={team.chat_model_config}
              isReadOnly={isReadOnly}
              pending={saveChatModel.isPending}
              idPrefix="team-chat-model"
              scopeLabel={{ es: "del chat", en: "(chat)" }}
              title={{ es: "Modelo del chat", en: "Chat model" }}
              description={{
                es:
                  "El modelo con el que el equipo RESPONDE en el chat de planificación. " +
                  "Vacío = usa el modelo de ejecución del equipo. Conviene uno más rápido " +
                  "(un modelo agéntico/pesado hace el chat lento).",
                en:
                  "The model the team REPLIES with in the planning chat. Empty = use the " +
                  "team execution model. A faster model is recommended (a heavy/agentic " +
                  "model makes the chat slow).",
              }}
              onSave={(modelConfig) => saveChatModel.mutate(modelConfig)}
            />
          </div>
          {/* ADR 0071: política de memoria del equipo. Gobierna a sus miembros. */}
          <div className="mb-6">
            <Card className="p-4">
              <Label htmlFor="team-memory-scope" className="text-sm font-medium">
                Política de memoria del equipo
              </Label>
              <p className="text-muted-foreground mt-1 text-xs">
                Gobierna la memoria de los agentes del equipo. &quot;Sin política&quot; = cada
                agente usa su propio scope. Las lecciones (semantic) viajan a este nivel; lo puntual
                de cada proyecto (episodic) se queda en su proyecto.
              </p>
              <Select
                id="team-memory-scope"
                data-testid="team-memory-scope"
                className="mt-2 max-w-xs"
                value={team.memory_scope ?? ""}
                disabled={isReadOnly || saveMemoryScope.isPending}
                onChange={(e) => saveMemoryScope.mutate(e.target.value || null)}
              >
                <option value="">Sin política (heredar)</option>
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
              Miembros ({team.members.length})
              {isReadOnly && (
                <Badge variant="muted" className="ml-2">
                  Built-in
                </Badge>
              )}
            </h2>
            <Button
              data-testid="add-member-button"
              onClick={() => setDialogOpen(true)}
              disabled={isReadOnly}
              title={
                isReadOnly
                  ? "Los equipos built-in no son editables. Fórkea para personalizar."
                  : undefined
              }
            >
              <Plus className="mr-1 h-4 w-4" /> Añadir miembro
            </Button>
          </div>

          {team.members.length === 0 ? (
            <p
              className="text-muted-foreground py-8 text-center text-sm"
              data-testid="members-empty"
            >
              El equipo no tiene miembros todavía.
            </p>
          ) : (
            <div className="flex flex-col gap-2" data-testid="members-list">
              {team.members.map((m) => {
                const agent = agentsById.get(m.agent_id);
                return (
                  <Card key={m.agent_id} data-testid={`member-${m.agent_id}`} className="p-3">
                    <div className="flex items-center justify-between">
                      <div>
                        <p className="font-medium">{agent?.name ?? "(agente)"}</p>
                        <p className="text-muted-foreground text-xs">
                          {m.role_in_team ?? agent?.role ?? "—"}
                          {agent &&
                            (agent.forked_from_agent_id ? (
                              <Badge
                                variant="warning"
                                className="ml-2"
                                data-testid={`member-forked-${agent.id}`}
                              >
                                Forked
                              </Badge>
                            ) : (
                              <Badge
                                variant="muted"
                                className="ml-2"
                                data-testid={`member-linked-${agent.id}`}
                              >
                                Linked
                              </Badge>
                            ))}
                        </p>
                      </div>
                      <div className="flex items-center gap-2">
                        {m.is_team_leader && (
                          <Badge variant="warning" data-testid="leader-badge">
                            Líder
                          </Badge>
                        )}
                        <Badge variant="muted" data-testid={`member-priority-${m.agent_id}`}>
                          Prioridad {m.assignment_priority}
                        </Badge>
                        {!isReadOnly && (
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => setMemberEditing(m)}
                            data-testid={`member-edit-${m.agent_id}`}
                          >
                            <Pencil className="mr-1 h-4 w-4" />
                            Editar
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

      <Dialog
        open={dialogOpen}
        onOpenChange={(next) => {
          setDialogOpen(next);
          if (!next) resetDialog();
        }}
      >
        <DialogContent data-testid="add-member-dialog">
          <DialogHeader>
            <DialogTitle>Añadir miembro al equipo</DialogTitle>
            <DialogDescription>
              Elige un agente del catálogo y decide si lo añades por referencia (linked) o como una
              copia editable (forked).
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="agent">Agente</Label>
              <select
                id="agent"
                className="border-input bg-background ring-offset-background focus-visible:ring-ring h-10 rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                value={selectedAgentId}
                onChange={(e) => setSelectedAgentId(e.target.value)}
                data-testid="agent-select"
              >
                <option value="">— Selecciona —</option>
                {agents
                  .filter((a) => !team?.members.some((m) => m.agent_id === a.id))
                  .map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.name} ({a.scope})
                    </option>
                  ))}
              </select>
            </div>

            <fieldset className="flex flex-col gap-1.5">
              <legend className="text-sm font-medium">Modo</legend>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="mode"
                  value="linked"
                  checked={mode === "linked"}
                  onChange={() => setMode("linked")}
                  data-testid="mode-linked"
                />
                <span>
                  <strong>Linked</strong> — el equipo usa el agente por referencia. Si el origen
                  evoluciona, el equipo lo ve.
                </span>
              </label>
              <label className="flex items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="mode"
                  value="forked"
                  checked={mode === "forked"}
                  onChange={() => setMode("forked")}
                  data-testid="mode-forked"
                />
                <span>
                  <strong>Forked</strong> — clona el agente en un proyecto como copia editable.
                  Independiente del original.
                </span>
              </label>
            </fieldset>

            {mode === "forked" && (
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="project">Proyecto destino</Label>
                <select
                  id="project"
                  className="border-input bg-background ring-offset-background focus-visible:ring-ring h-10 rounded-md border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-offset-2"
                  value={selectedProjectId}
                  onChange={(e) => setSelectedProjectId(e.target.value)}
                  data-testid="project-select"
                >
                  <option value="">— Selecciona —</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
                {projects.length === 0 && (
                  <p className="text-muted-foreground text-xs">
                    No tienes proyectos creados. Crea uno primero para poder forkear.
                  </p>
                )}
              </div>
            )}

            {submitError && (
              <p
                className="text-danger-soft-foreground bg-danger-soft rounded p-2 text-xs"
                data-testid="add-member-error"
              >
                {submitError}
              </p>
            )}
          </DialogBody>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDialogOpen(false)}
              data-testid="add-member-cancel"
            >
              Cancelar
            </Button>
            <Button
              onClick={() => addMember.mutate()}
              disabled={
                addMember.isPending || !selectedAgentId || (mode === "forked" && !selectedProjectId)
              }
              data-testid="add-member-submit"
            >
              {addMember.isPending && <Spinner className="mr-2 h-4 w-4" />}
              {addMember.isPending ? "Añadiendo…" : "Añadir"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {memberEditing && (
        <MemberEditDialog
          teamId={teamId}
          member={memberEditing}
          agentName={agentsById.get(memberEditing.agent_id)?.name ?? "(agente)"}
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

// ---------------------------------------------------------------------------
// Member metadata edit dialog (Plan 06.17 task_06_17_15, ADR 0053)
//
// La única escritura nueva de la UI a nivel de equipo: invoca el
// `PUT /teams/{id}/members/{agent_id}` ya existente para fijar
// is_team_leader / role_in_team / assignment_priority.
// ---------------------------------------------------------------------------

interface MemberUpdate {
  is_team_leader: boolean;
  role_in_team: string | null;
  assignment_priority: number;
}

function MemberEditDialog({
  teamId,
  member,
  agentName,
  open,
  onOpenChange,
  onSaved,
}: {
  teamId: string;
  member: TeamMember;
  agentName: string;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const [isLeader, setIsLeader] = useState(member.is_team_leader);
  const [roleInTeam, setRoleInTeam] = useState(member.role_in_team ?? "");
  const [priority, setPriority] = useState(String(member.assignment_priority));

  useEffect(() => {
    if (open) {
      setIsLeader(member.is_team_leader);
      setRoleInTeam(member.role_in_team ?? "");
      setPriority(String(member.assignment_priority));
    }
  }, [open, member.is_team_leader, member.role_in_team, member.assignment_priority]);

  const mutation = useMutation<Team, ApiError, MemberUpdate>({
    mutationFn: (payload) =>
      apiFetch<Team>(`/teams/${teamId}/members/${member.agent_id}`, {
        method: "PUT",
        body: payload,
      }),
    onSuccess: onSaved,
  });

  const priorityNum = Number(priority);
  const priorityValid = Number.isInteger(priorityNum) && priorityNum >= 0 && priorityNum <= 1000;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent data-testid="member-edit-dialog">
        <DialogHeader>
          <DialogTitle>Editar miembro</DialogTitle>
          <DialogDescription>
            Metadata de <strong>{agentName}</strong> en este equipo: si es líder, su rol y su
            prioridad de asignación.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={isLeader}
              onChange={(e) => setIsLeader(e.target.checked)}
              data-testid="member-edit-leader"
            />
            <span>Líder del equipo</span>
          </label>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="me-role">Rol en el equipo</Label>
            <Input
              id="me-role"
              value={roleInTeam}
              onChange={(e) => setRoleInTeam(e.target.value)}
              placeholder="p. ej. Tech Lead"
              data-testid="member-edit-role"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="me-priority">Prioridad de asignación (0–1000)</Label>
            <Input
              id="me-priority"
              type="number"
              min={0}
              max={1000}
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              data-testid="member-edit-priority"
            />
          </div>
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="member-edit-error"
            >
              {mutation.error?.body ?? mutation.error?.message ?? "Error al guardar"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            disabled={!priorityValid || mutation.isPending}
            onClick={() =>
              mutation.mutate({
                is_team_leader: isLeader,
                role_in_team: roleInTeam.trim() || null,
                assignment_priority: priorityNum,
              })
            }
            data-testid="member-edit-save"
          >
            {mutation.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Team edit dialog (Plan 06.6 task_06_6_08)
// ---------------------------------------------------------------------------

interface TeamUpdate {
  name?: string;
  description?: string | null;
}

function TeamEditDialog({
  team,
  open,
  onOpenChange,
  onSaved,
}: {
  team: Team;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(team.name);
  const [description, setDescription] = useState(team.description ?? "");

  useEffect(() => {
    if (open) {
      setName(team.name);
      setDescription(team.description ?? "");
    }
  }, [open, team.name, team.description]);

  const mutation = useMutation<Team, ApiError, TeamUpdate>({
    mutationFn: (payload) =>
      apiFetch<Team>(`/teams/${team.id}`, {
        method: "PUT",
        body: payload,
      }),
    onSuccess: onSaved,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Editar equipo</DialogTitle>
          <DialogDescription>
            Cambia el nombre o la descripción. Los miembros se gestionan desde la lista principal.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="te-name">Nombre</Label>
            <Input
              id="te-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="edit-team-name"
              required
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Descripción</Label>
            <MarkdownTextarea
              value={description}
              onChange={setDescription}
              rows={3}
              data-testid="edit-team-description"
            />
          </div>
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="edit-team-error"
            >
              {mutation.error?.message ?? "Error al guardar"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            disabled={!name.trim() || mutation.isPending}
            onClick={() =>
              mutation.mutate({
                name: name.trim(),
                description: description.trim() || null,
              })
            }
            data-testid="edit-team-save"
          >
            {mutation.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Team delete dialog with confirm-by-name (Plan 06.6 task_06_6_09)
// ---------------------------------------------------------------------------

function TeamDeleteDialog({
  team,
  open,
  onOpenChange,
  onDeleted,
}: {
  team: Team;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onDeleted: () => void;
}) {
  const [typed, setTyped] = useState("");
  const matches = typed === team.name;

  const mutation = useMutation<void, ApiError, void>({
    mutationFn: async () => {
      await apiFetch(`/teams/${team.id}`, { method: "DELETE" });
    },
    onSuccess: onDeleted,
  });

  return (
    <Dialog
      open={open}
      onOpenChange={(v) => {
        if (!v) setTyped("");
        onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Borrar equipo</DialogTitle>
          <DialogDescription>
            Esta acción es <strong>irreversible</strong>. Los agentes miembros NO se borran — solo
            desaparece su pertenencia a este equipo.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            Para confirmar, teclea el nombre del equipo:
            <br />
            <code className="bg-muted rounded px-1 py-0.5 text-xs">{team.name}</code>
          </p>
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={team.name}
            data-testid="delete-team-confirm-input"
          />
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="delete-team-error"
            >
              {mutation.error?.message ?? "Error al borrar"}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancelar
          </Button>
          <Button
            variant="destructive"
            disabled={!matches || mutation.isPending}
            onClick={() => mutation.mutate()}
            data-testid="delete-team-confirm"
          >
            {mutation.isPending ? "Borrando…" : "Borrar definitivamente"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
