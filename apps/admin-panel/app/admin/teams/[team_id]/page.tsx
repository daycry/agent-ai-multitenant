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
import { Spinner } from "@/components/ui/spinner";
import { ApiError, apiFetch } from "@/lib/api";

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
  members: TeamMember[];
}

interface Agent {
  id: string;
  name: string;
  role: string;
  scope: string;
  project_id: string | null;
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
                          {agent && (
                            <Badge
                              variant={
                                agent.scope === "global_builtin"
                                  ? "muted"
                                  : agent.scope === "global_tenant_template"
                                    ? "info"
                                    : "primary"
                              }
                              className="ml-2"
                            >
                              {agent.scope === "global_builtin"
                                ? "Linked (built-in)"
                                : agent.scope === "global_tenant_template"
                                  ? "Linked (tenant)"
                                  : "Forked"}
                            </Badge>
                          )}
                        </p>
                      </div>
                      {m.is_team_leader && (
                        <Badge variant="warning" data-testid="leader-badge">
                          Líder
                        </Badge>
                      )}
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
    </div>
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
            <Label htmlFor="te-description">Descripción</Label>
            <textarea
              id="te-description"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              rows={3}
              className="border-input bg-background rounded-md border px-3 py-2 text-sm"
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
