"use client";

/**
 * Panel "Asignaciones" del detalle de KB (Plan 06.9 task_06_9_11).
 *
 * Dialog que muestra los grants actuales de una KB:
 *   - Proyectos con grant (`GET /knowledge-bases/{id}/projects`).
 *   - Agentes con grant (`GET /knowledge-bases/{id}/agents`).
 *
 * Cada fila permite Revoke (`DELETE /knowledge-bases/{id}/projects/{pid}`
 * para proyectos, `DELETE /agents/{aid}/knowledge-bases/{kbid}` para
 * agentes — los endpoints viven en distintos routers pero la UX es la
 * misma).
 *
 * Render: dos secciones consecutivas dentro del mismo dialog. Vacío
 * en ambas → "Esta KB no está granteada a nadie todavía".
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, FolderKanban, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Spinner } from "@/components/ui/spinner";
import { apiFetch } from "@/lib/api";
import { useT } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

interface ProjectGrantRow {
  project_id: string;
  name: string;
  granted_at: string | null;
}

interface AgentGrantRow {
  agent_id: string;
  name: string;
  scope: string;
  role: string;
  granted_at: string | null;
}

interface KbAssignmentsDialogProps {
  kbId: string;
  kbName: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function KbAssignmentsDialog({
  kbId,
  kbName,
  open,
  onOpenChange,
}: KbAssignmentsDialogProps) {
  const t = useT("knowledgeBases");
  const errorText = useErrorText();
  const queryClient = useQueryClient();
  const [actionError, setActionError] = useState<string | null>(null);

  const projectsQuery = useQuery({
    queryKey: ["kb-assignments", kbId, "projects"],
    queryFn: () => apiFetch<ProjectGrantRow[]>(`/knowledge-bases/${kbId}/projects`),
    enabled: open,
    refetchOnWindowFocus: false,
  });

  const agentsQuery = useQuery({
    queryKey: ["kb-assignments", kbId, "agents"],
    queryFn: () => apiFetch<AgentGrantRow[]>(`/knowledge-bases/${kbId}/agents`),
    enabled: open,
    refetchOnWindowFocus: false,
  });

  const revokeProject = useMutation({
    mutationFn: (projectId: string) =>
      apiFetch<void>(`/knowledge-bases/${kbId}/projects/${projectId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      setActionError(null);
      void queryClient.invalidateQueries({ queryKey: ["kb-assignments", kbId] });
    },
    onError: (err) => setActionError(errorText(err)),
  });

  const revokeAgent = useMutation({
    mutationFn: (agentId: string) =>
      apiFetch<void>(`/agents/${agentId}/knowledge-bases/${kbId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      setActionError(null);
      void queryClient.invalidateQueries({ queryKey: ["kb-assignments", kbId] });
    },
    onError: (err) => setActionError(errorText(err)),
  });

  const isLoading = projectsQuery.isLoading || agentsQuery.isLoading;
  const projects = projectsQuery.data ?? [];
  const agents = agentsQuery.data ?? [];
  const isEmpty = !isLoading && projects.length === 0 && agents.length === 0;

  // KB Q4 (propuesta 2026-07-12): la EDICIÓN también vive aquí — antes añadir
  // un grant obligaba a cazar por dos pantallas (proyecto vía KbGrantDialog,
  // agente solo desde la ficha del agente). El alta de agente va plegada bajo
  // «Avanzado» (el caso común es el grant a proyecto).
  const [newProjectId, setNewProjectId] = useState("");
  const [newAgentId, setNewAgentId] = useState("");
  const allProjectsQuery = useQuery<{ id: string; name: string }[]>({
    queryKey: ["projects-for-kb-grant"],
    queryFn: () => apiFetch("/projects"),
    enabled: open,
  });
  const allAgentsQuery = useQuery<{ id: string; name: string; role?: string }[]>({
    queryKey: ["agents-for-kb-grant"],
    queryFn: () => apiFetch("/agents"),
    enabled: open,
  });
  const grantProject = useMutation({
    mutationFn: (projectId: string) =>
      apiFetch(`/knowledge-bases/${kbId}/projects`, {
        method: "POST",
        body: { project_id: projectId },
      }),
    onSuccess: () => {
      setActionError(null);
      setNewProjectId("");
      void queryClient.invalidateQueries({ queryKey: ["kb-assignments", kbId] });
    },
    onError: (err) => setActionError(errorText(err)),
  });
  const grantAgent = useMutation({
    mutationFn: (agentId: string) =>
      apiFetch(`/agents/${agentId}/knowledge-bases`, {
        method: "POST",
        body: { kb_id: kbId },
      }),
    onSuccess: () => {
      setActionError(null);
      setNewAgentId("");
      void queryClient.invalidateQueries({ queryKey: ["kb-assignments", kbId] });
    },
    onError: (err) => setActionError(errorText(err)),
  });
  const grantedProjectIds = new Set(projects.map((p) => p.project_id));
  const grantedAgentIds = new Set(agents.map((a) => a.agent_id));
  const grantableProjects = (allProjectsQuery.data ?? []).filter(
    (p) => !grantedProjectIds.has(p.id),
  );
  const grantableAgents = (allAgentsQuery.data ?? []).filter((a) => !grantedAgentIds.has(a.id));

  return (
    <Dialog open={open} onOpenChange={onOpenChange} size="lg">
      <DialogContent data-testid="kb-assignments-dialog">
        <DialogHeader>
          <DialogTitle>{t("assignmentsDialogTitle", { name: kbName })}</DialogTitle>
        </DialogHeader>
        <DialogBody className="space-y-6">
          {isLoading && (
            <div className="flex justify-center p-4">
              <Spinner />
            </div>
          )}

          {actionError && <p className="text-danger-soft-foreground text-sm">{actionError}</p>}

          {isEmpty && (
            <p className="text-muted-foreground text-sm" data-testid="kb-assignments-empty">
              {t("assignmentsEmpty")}
            </p>
          )}

          {/* --- Proyectos --- */}
          {!isLoading && projects.length > 0 && (
            <section data-testid="kb-assignments-projects">
              <h3 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide">
                <FolderKanban className="h-3.5 w-3.5" /> {t("assignmentsProjects")}
              </h3>
              <ul className="mt-2 space-y-2">
                {projects.map((row) => (
                  <li
                    key={row.project_id}
                    className="flex items-center justify-between gap-3 rounded border p-2 text-sm"
                    data-testid={`kb-grant-project-${row.project_id}`}
                  >
                    <span className="font-medium">{row.name}</span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => revokeProject.mutate(row.project_id)}
                      disabled={revokeProject.isPending}
                      data-testid={`kb-revoke-project-${row.project_id}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {/* --- Agentes --- */}
          {!isLoading && agents.length > 0 && (
            <section data-testid="kb-assignments-agents">
              <h3 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide">
                <Bot className="h-3.5 w-3.5" /> {t("assignmentsAgents")}
              </h3>
              <ul className="mt-2 space-y-2">
                {agents.map((row) => (
                  <li
                    key={row.agent_id}
                    className="flex items-center justify-between gap-3 rounded border p-2 text-sm"
                    data-testid={`kb-grant-agent-${row.agent_id}`}
                  >
                    <span className="flex min-w-0 items-center gap-2">
                      <span className="truncate font-medium">{row.name}</span>
                      <Badge variant="muted">{row.role}</Badge>
                      <Badge variant="info">{row.scope}</Badge>
                    </span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => revokeAgent.mutate(row.agent_id)}
                      disabled={revokeAgent.isPending}
                      data-testid={`kb-revoke-agent-${row.agent_id}`}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  </li>
                ))}
              </ul>
            </section>
          )}
          {/* --- Q4: añadir grant a proyecto (el caso común) --- */}
          {!isLoading && (
            <section className="space-y-2" data-testid="kb-add-project-grant">
              <h3 className="text-sm font-semibold uppercase tracking-wide">
                {t("grantToProject")}
              </h3>
              <div className="flex gap-2">
                <select
                  className="border-input bg-background w-full rounded-md border px-2 py-1.5 text-sm"
                  value={newProjectId}
                  onChange={(e) => setNewProjectId(e.target.value)}
                  data-testid="kb-grant-project-select"
                >
                  <option value="">{t("chooseProject")}</option>
                  {grantableProjects.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
                <Button
                  size="sm"
                  disabled={!newProjectId || grantProject.isPending}
                  onClick={() => grantProject.mutate(newProjectId)}
                  data-testid="kb-grant-project-submit"
                >
                  {t("grantAction")}
                </Button>
              </div>

              {/* --- Q4: grants de agente plegados bajo «Avanzado» --- */}
              <details data-testid="kb-add-agent-grant">
                <summary className="text-muted-foreground cursor-pointer text-xs">
                  {t("advancedAgentGrant")}
                </summary>
                <div className="mt-2 flex gap-2">
                  <select
                    className="border-input bg-background w-full rounded-md border px-2 py-1.5 text-sm"
                    value={newAgentId}
                    onChange={(e) => setNewAgentId(e.target.value)}
                    data-testid="kb-grant-agent-select"
                  >
                    <option value="">{t("chooseAgent")}</option>
                    {grantableAgents.map((a) => (
                      <option key={a.id} value={a.id}>
                        {a.name}
                        {a.role ? ` (${a.role})` : ""}
                      </option>
                    ))}
                  </select>
                  <Button
                    size="sm"
                    variant="outline"
                    disabled={!newAgentId || grantAgent.isPending}
                    onClick={() => grantAgent.mutate(newAgentId)}
                    data-testid="kb-grant-agent-submit"
                  >
                    {t("grantAction")}
                  </Button>
                </div>
              </details>
            </section>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
