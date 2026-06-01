"use client";

/**
 * Hub del proyecto (Plan 06.6 task_06_6_01..03).
 *
 * Página de entrada al detalle de un proyecto. Muestra:
 *   - Cabecera con name + status + descripción + acciones (Editar / Borrar).
 *   - Cards de las 6 sub-secciones del proyecto (chat, plans,
 *     mcp-servers, knowledge-bases, agent-tools-diagnostic,
 *     dep-cache) con icono y link.
 *
 * Edita vía dialog → PUT /projects/{id} (campos básicos:
 *   name, description, status, team_id).
 *
 * Borra vía dialog con confirm-by-name → DELETE /projects/{id}.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Database,
  FolderKanban,
  Layers,
  ListTodo,
  MessageSquare,
  Pencil,
  Plug,
  Terminal,
  Trash2,
  Webhook,
  Workflow,
} from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { Badge, type BadgeVariant } from "@/components/ui/badge";
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
import { MarkdownTextarea } from "@/components/ui/markdown-textarea";
import { Select } from "@/components/ui/select";
import { StateBlock } from "@/components/shared/state-block";
import { ApiError, apiFetch } from "@/lib/api";

type ProjectStatus = "active" | "paused" | "archived";

interface Project {
  id: string;
  name: string;
  description: string | null;
  status: string;
  team_id: string | null;
  is_template: boolean;
}

interface ProjectUpdate {
  name?: string;
  description?: string | null;
  status?: ProjectStatus;
  team_id?: string | null;
}

const STATUS_VARIANT: Record<string, BadgeVariant> = {
  active: "success",
  paused: "warning",
  archived: "muted",
};

const STATUS_OPTIONS: { value: ProjectStatus; label: string }[] = [
  { value: "active", label: "Activo" },
  { value: "paused", label: "Pausado" },
  { value: "archived", label: "Archivado" },
];

// The 8 sub-sections every project always has (some may be empty,
// like dep-cache for a brand-new project, but they're always
// reachable). Tasks lista TODAS las tareas del proyecto (incluye las
// que están fuera de un plan).
const SUBSECTIONS = [
  {
    key: "chat",
    label: "Chat",
    description: "Conversación con los agentes del proyecto.",
    Icon: MessageSquare,
  },
  {
    key: "plans",
    label: "Planes",
    description: "Planes de construcción + Kanban de sus tareas.",
    Icon: Workflow,
  },
  {
    key: "tasks",
    label: "Tasks",
    description: "Todas las tareas del proyecto, incluidas las que no tienen plan.",
    Icon: ListTodo,
  },
  {
    key: "knowledge-bases",
    label: "Knowledge Bases",
    description: "Bases de conocimiento + documentos indexados.",
    Icon: Database,
  },
  {
    key: "mcp-servers",
    label: "MCP servers",
    description: "Servidores MCP a los que se conectan los agentes.",
    Icon: Plug,
  },
  {
    key: "agent-tools-diagnostic",
    label: "Tools por agente",
    description: "Diagnóstico read-only de tools wired a cada agente.",
    Icon: Bot,
  },
  {
    key: "commands",
    label: "Comandos & runtime",
    description: "Comandos autorizados (shell_exec) + runtime por defecto del stack.",
    Icon: Terminal,
  },
  {
    key: "dep-cache",
    label: "Caché de dependencias",
    description: "Invalidar caché de deps por runtime.",
    Icon: Layers,
  },
  {
    key: "incoming-webhooks",
    label: "Webhooks entrantes",
    description: "Eventos de GitHub, Jira, Sentry… que disparan acciones.",
    Icon: Webhook,
  },
] as const;

export default function ProjectHubPage() {
  const params = useParams<{ id: string }>();
  const projectId = params?.id ?? "";
  const router = useRouter();
  const queryClient = useQueryClient();

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const {
    data: project,
    isLoading,
    isError,
    error,
  } = useQuery<Project, ApiError>({
    queryKey: ["project", projectId],
    queryFn: () => apiFetch<Project>(`/projects/${projectId}`),
    enabled: !!projectId,
    refetchOnWindowFocus: false,
  });

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8" data-testid="project-hub">
      <PageHeader
        icon={<FolderKanban className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={project?.name ?? "Proyecto"}
        description={project?.description ?? "Cargando…"}
        actions={
          project && (
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setEditOpen(true)}
                data-testid="project-edit-button"
              >
                <Pencil className="mr-1 h-4 w-4" />
                Editar
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setDeleteOpen(true)}
                data-testid="project-delete-button"
              >
                <Trash2 className="mr-1 h-4 w-4" />
                Borrar
              </Button>
            </div>
          )
        }
      />

      <StateBlock
        isLoading={isLoading}
        loadingSkeleton
        skeletonRows={4}
        loadingTestId="project-loading"
      />

      {isError && (
        <Card className="p-6" data-testid="project-error">
          <p className="text-danger-soft-foreground text-sm">
            No se pudo cargar el proyecto: {error?.message ?? "error desconocido"}.
          </p>
          <div className="mt-3">
            <Button asChild variant="outline" size="sm">
              <Link href="/admin/projects">Volver al listado</Link>
            </Button>
          </div>
        </Card>
      )}

      {project && (
        <>
          {/* Status banner */}
          <div className="mb-6 flex items-center gap-3" data-testid="project-status-row">
            <span className="text-muted-foreground text-sm">Estado:</span>
            <Badge variant={STATUS_VARIANT[project.status] ?? "muted"}>{project.status}</Badge>
            {project.is_template && <Badge variant="info">plantilla</Badge>}
            {project.team_id && (
              <span className="text-muted-foreground text-xs">
                Team: <code>{project.team_id.slice(0, 8)}</code>
              </span>
            )}
          </div>

          {/* Sub-sections grid */}
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Secciones
          </h2>
          <div
            className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
            data-testid="project-subsections"
          >
            {SUBSECTIONS.map(({ key, label, description, Icon }) => (
              <Card
                key={key}
                data-testid={`project-section-${key}`}
                className="hover:border-primary/40 transition-colors"
              >
                <Link
                  href={`/admin/projects/${projectId}/${key}`}
                  className="block p-4"
                  data-testid={`project-section-link-${key}`}
                >
                  <div className="mb-2 flex items-center gap-2">
                    <Icon className="h-5 w-5 text-muted-foreground" />
                    <h3 className="font-semibold">{label}</h3>
                  </div>
                  <p className="text-sm text-muted-foreground">{description}</p>
                </Link>
              </Card>
            ))}
          </div>
        </>
      )}

      {/* Edit dialog */}
      {project && (
        <ProjectEditDialog
          project={project}
          open={editOpen}
          onOpenChange={setEditOpen}
          onSaved={() => {
            void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
            void queryClient.invalidateQueries({ queryKey: ["projects", "tenant"] });
            setEditOpen(false);
          }}
        />
      )}

      {/* Delete dialog */}
      {project && (
        <ProjectDeleteDialog
          project={project}
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          onDeleted={() => {
            setDeleteOpen(false);
            router.push("/admin/projects");
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Edit dialog
// ---------------------------------------------------------------------------

function ProjectEditDialog({
  project,
  open,
  onOpenChange,
  onSaved,
}: {
  project: Project;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description ?? "");
  const [status, setStatus] = useState<ProjectStatus>(
    (project.status as ProjectStatus) ?? "active",
  );

  // Reset form when the dialog reopens with a (possibly stale)
  // project — otherwise an edit, save, reopen still shows the old
  // typed value.
  useEffect(() => {
    if (open) {
      setName(project.name);
      setDescription(project.description ?? "");
      setStatus((project.status as ProjectStatus) ?? "active");
    }
  }, [open, project.name, project.description, project.status]);

  const mutation = useMutation<Project, ApiError, ProjectUpdate>({
    mutationFn: (payload) =>
      apiFetch<Project>(`/projects/${project.id}`, {
        method: "PUT",
        body: payload,
      }),
    onSuccess: onSaved,
  });

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Editar proyecto</DialogTitle>
          <DialogDescription>
            Cambia los campos básicos. La configuración avanzada (MCP, KBs, etc.) se edita desde sus
            respectivas sub-secciones.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-name">Nombre</Label>
            <Input
              id="edit-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="edit-project-name"
              required
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>Descripción</Label>
            <MarkdownTextarea
              value={description}
              onChange={setDescription}
              rows={5}
              data-testid="edit-project-description"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-status">Estado</Label>
            <Select
              id="edit-status"
              value={status}
              onChange={(e) => setStatus(e.target.value as ProjectStatus)}
              data-testid="edit-project-status"
            >
              {STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </Select>
          </div>
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="edit-project-error"
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
                status,
              })
            }
            data-testid="edit-project-save"
          >
            {mutation.isPending ? "Guardando…" : "Guardar"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------
// Delete dialog with confirm-by-name (Plan 06.6 task_06_6_03)
// ---------------------------------------------------------------------------

function ProjectDeleteDialog({
  project,
  open,
  onOpenChange,
  onDeleted,
}: {
  project: Project;
  open: boolean;
  onOpenChange: (v: boolean) => void;
  onDeleted: () => void;
}) {
  const [typed, setTyped] = useState("");
  const matches = typed === project.name;

  const mutation = useMutation<void, ApiError, void>({
    mutationFn: async () => {
      await apiFetch(`/projects/${project.id}`, { method: "DELETE" });
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
          <DialogTitle>Borrar proyecto</DialogTitle>
          <DialogDescription>
            Esta acción es <strong>irreversible</strong>. Borra el proyecto, sus planes, tareas y
            conversaciones. Los repos git en disco NO se tocan.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            Para confirmar, teclea el nombre del proyecto:
            <br />
            <code className="bg-muted rounded px-1 py-0.5 text-xs">{project.name}</code>
          </p>
          <Input
            value={typed}
            onChange={(e) => setTyped(e.target.value)}
            placeholder={project.name}
            data-testid="delete-project-confirm-input"
          />
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="delete-project-error"
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
            data-testid="delete-project-confirm"
          >
            {mutation.isPending ? "Borrando…" : "Borrar definitivamente"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
