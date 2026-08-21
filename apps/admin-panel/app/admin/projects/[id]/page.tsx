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
 *
 * i18n (prod-16 `task_prod16_03`): esta pantalla NO entraba a trozos. Su texto
 * se reparte entre este fichero, seis piezas de `components/projects/` y
 * `lib/project-governance.ts`; migrar sólo el marco daba la pantalla
 * mitad-y-mitad que el plan cierra. El test que lo fija es `i18n.test.tsx`, al
 * lado.
 */

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Brain,
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
import { CapabilityHub } from "@/components/capability/capability-hub";
import { ChatModelSection, type ChatModelConfig } from "@/components/capability/chat-model-section";
import {
  GitConfigSection,
  type GitConfig,
  type GitPolicies,
  type LastGitSync,
} from "@/components/projects/git-config-section";
import { PreviewLauncher } from "@/components/projects/preview-launcher";
import { ProjectGovernanceSection } from "@/components/projects/governance-section";
import { ReviewPreviewSection } from "@/components/projects/review-preview-section";
import { RuntimeServicesSection } from "@/components/projects/runtime-services-section";
import { ApiError, apiFetch } from "@/lib/api";
import { dictionary, useT, type MessageKey } from "@/lib/i18n";
import { useErrorText } from "@/lib/use-error-text";

type ProjectStatus = "active" | "paused" | "archived";

interface Project {
  id: string;
  name: string;
  description: string | null;
  status: string;
  team_id: string | null;
  is_template: boolean;
  // Ola A: modelo por defecto del proyecto (alias JSON `model_config`). {} = hereda.
  model_config: ChatModelConfig;
  // Modelo del CHAT del proyecto (Feature B): proveedor concreto + modelo. {} = hereda.
  chat_model_config: ChatModelConfig;
  // ADR 0072: config git del proyecto (sin secreto). null = sin remoto.
  git_config: GitConfig | null;
  // worker_config.git_policies guarda las políticas del flujo git del plan (ADR 0072).
  worker_config?: Record<string, unknown> | null;
  // repository_config.review_image/review_port: app-preview de validación humana (ADR 0063).
  repository_config?: Record<string, unknown> | null;
  // task_wf_35: límites y gobierno. Los edita `ProjectGovernanceSection`, que
  // recibe el proyecto entero y toma solo sus claves (`GovernanceValue`).
  execution_budgets?: Record<string, unknown> | null;
  guardrails_config?: Record<string, unknown> | null;
  human_task_review_mode?: string | null;
  budget_amount?: string | number | null;
  budget_currency?: string | null;
  budget_period?: string | null;
  budget_period_start_day?: number | null;
  budget_period_length_days?: number | null;
}

interface ProjectUpdate {
  name?: string;
  description?: string | null;
  status?: ProjectStatus;
  team_id?: string | null;
  model_config?: ChatModelConfig;
  chat_model_config?: ChatModelConfig;
}

type HubKey = MessageKey<"projectHub">;

const STATUS_VARIANT: Record<string, BadgeVariant> = {
  active: "success",
  paused: "warning",
  archived: "muted",
};

const STATUS_OPTIONS = [
  { value: "active", labelKey: "statusActive" },
  { value: "paused", labelKey: "statusPaused" },
  { value: "archived", labelKey: "statusArchived" },
] as const satisfies readonly { value: ProjectStatus; labelKey: HubKey }[];

// The 8 sub-sections every project always has (some may be empty,
// like dep-cache for a brand-new project, but they're always
// reachable). Tasks lista TODAS las tareas del proyecto (incluye las
// que están fuera de un plan).
//
// El catálogo guarda las CLAVES del diccionario, no los textos: es un dato de
// UI y traducirlo en el sitio de uso obliga a que las dos caras existan.
const SUBSECTIONS = [
  { key: "chat", labelKey: "sectionChat", descKey: "sectionChatDesc", Icon: MessageSquare },
  { key: "plans", labelKey: "sectionPlans", descKey: "sectionPlansDesc", Icon: Workflow },
  { key: "tasks", labelKey: "sectionTasks", descKey: "sectionTasksDesc", Icon: ListTodo },
  {
    key: "knowledge-bases",
    labelKey: "sectionKbs",
    descKey: "sectionKbsDesc",
    Icon: Database,
  },
  { key: "memories", labelKey: "sectionMemories", descKey: "sectionMemoriesDesc", Icon: Brain },
  { key: "mcp-servers", labelKey: "sectionMcp", descKey: "sectionMcpDesc", Icon: Plug },
  {
    key: "agent-tools-diagnostic",
    labelKey: "sectionToolsDiagnostic",
    descKey: "sectionToolsDiagnosticDesc",
    Icon: Bot,
  },
  {
    key: "commands",
    labelKey: "sectionCommands",
    descKey: "sectionCommandsDesc",
    Icon: Terminal,
  },
  {
    key: "dep-cache",
    labelKey: "sectionDepCache",
    descKey: "sectionDepCacheDesc",
    Icon: Layers,
  },
  {
    key: "incoming-webhooks",
    labelKey: "sectionWebhooks",
    descKey: "sectionWebhooksDesc",
    Icon: Webhook,
  },
] as const satisfies readonly {
  key: string;
  labelKey: HubKey;
  descKey: HubKey;
  Icon: typeof Bot;
}[];

export default function ProjectHubPage() {
  const params = useParams<{ id: string }>();
  const projectId = params?.id ?? "";
  const router = useRouter();
  const queryClient = useQueryClient();
  const t = useT("projectHub");
  const tCommon = useT("common");
  const errorText = useErrorText();

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

  // Ola A-UI: fija el modelo por defecto del proyecto (PUT /projects/{id}).
  const saveModel = useMutation<Project, ApiError, ChatModelConfig>({
    mutationFn: (modelConfig) =>
      apiFetch<Project>(`/projects/${projectId}`, {
        method: "PUT",
        body: { model_config: modelConfig },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
      void queryClient.invalidateQueries({ queryKey: ["projects", "tenant"] });
    },
  });

  // Modelo del CHAT del proyecto (proveedor concreto; PUT /projects/{id}).
  const saveChatModel = useMutation<Project, ApiError, ChatModelConfig>({
    mutationFn: (chatModelConfig) =>
      apiFetch<Project>(`/projects/${projectId}`, {
        method: "PUT",
        body: { chat_model_config: chatModelConfig },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["project", projectId] });
    },
  });

  return (
    <div className="mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 lg:px-8" data-testid="project-hub">
      <PageHeader
        icon={<FolderKanban className="h-6 w-6 sm:h-7 sm:w-7" />}
        title={project?.name ?? t("fallbackTitle")}
        description={project?.description ?? tCommon("loading")}
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
                {t("edit")}
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setDeleteOpen(true)}
                data-testid="project-delete-button"
              >
                <Trash2 className="mr-1 h-4 w-4" />
                {t("delete")}
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
            {t("loadError", { detail: errorText(error) })}
          </p>
          <div className="mt-3">
            <Button asChild variant="outline" size="sm">
              <Link href="/admin/projects">{t("backToList")}</Link>
            </Button>
          </div>
        </Card>
      )}

      {project && (
        <>
          {/* Status banner */}
          <div className="mb-6 flex items-center gap-3" data-testid="project-status-row">
            <span className="text-muted-foreground text-sm">{t("statusLabel")}</span>
            <Badge variant={STATUS_VARIANT[project.status] ?? "muted"}>{project.status}</Badge>
            {project.is_template && <Badge variant="info">{t("templateBadge")}</Badge>}
            {project.team_id && (
              <span className="text-muted-foreground text-xs">
                {t("teamLabel")} <code>{project.team_id.slice(0, 8)}</code>
              </span>
            )}
          </div>

          {/* Plan 06.17 task_06_17_09: Hub de Capacidad del proyecto (SABER +
              RECORDAR; SER no aplica y HACER no restringe a nivel de proyecto). */}
          <div className="mb-6">
            <CapabilityHub entityType="project" entityId={projectId} />
          </div>

          {/* Modelo de EJECUCIÓN del proyecto (ADR 0065): proveedor concreto por nombre,
              uniforme con el del chat y el asistente. Lo heredan los agentes sin modelo.
              `ChatModelSection` recibe el par bilingüe y lo resuelve con `pickLang`
              (su contrato, ADR 0065): se le pasa la entrada del diccionario entera. */}
          <div className="mb-6">
            <ChatModelSection
              value={project.model_config}
              pending={saveModel.isPending}
              idPrefix="project-exec"
              title={dictionary.projectHub.execModelTitle}
              description={dictionary.projectHub.execModelDescription}
              onSave={(cfg) => saveModel.mutate(cfg)}
            />
          </div>

          {/* Modelo del CHAT del proyecto (Feature B): proveedor concreto por nombre. */}
          <div className="mb-6">
            <ChatModelSection
              value={project.chat_model_config}
              pending={saveChatModel.isPending}
              idPrefix="project"
              onSave={(cfg) => saveChatModel.mutate(cfg)}
            />
          </div>

          {/* ADR 0072: configuración del repositorio Git (remoto + PAT/SSH). */}
          <div className="mb-6">
            <GitConfigSection
              projectId={projectId}
              value={project.git_config}
              policies={
                (project.worker_config?.["git_policies"] as GitPolicies | undefined) ?? null
              }
              lastSync={
                (project.repository_config?.["last_git_sync"] as LastGitSync | undefined) ?? null
              }
            />
          </div>

          {/* hallazgo #4: imagen/puerto del app-preview de validación humana (ADR 0063). */}
          <div className="mb-6">
            <ReviewPreviewSection projectId={projectId} value={project.repository_config ?? null} />
          </div>

          {/* ADR 0129: servicios de respaldo + env + imagen de runtime custom. */}
          <div className="mb-6">
            <RuntimeServicesSection
              projectId={projectId}
              value={project.repository_config ?? null}
            />
          </div>

          {/* task_wf_35: presupuestos (por run y de gasto), modo de revisión de
              tareas humanas y guardrails del proyecto — cuatro ajustes que el
              backend acepta y ninguna pantalla ofrecía. */}
          <div className="mb-6">
            <ProjectGovernanceSection projectId={projectId} value={project} />
          </div>

          {/* ADR 0130: levantar la app del proyecto (rama por defecto) en preview 24h. */}
          <div className="mb-6">
            <PreviewLauncher scope="projects" id={projectId} />
          </div>

          {/* Sub-sections grid */}
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            {t("sectionsHeading")}
          </h2>
          <div
            className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3"
            data-testid="project-subsections"
          >
            {SUBSECTIONS.map(({ key, labelKey, descKey, Icon }) => (
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
                    <h3 className="font-semibold">{t(labelKey)}</h3>
                  </div>
                  <p className="text-sm text-muted-foreground">{t(descKey)}</p>
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
  const t = useT("projectHub");
  const errorText = useErrorText();
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description ?? "");
  const [status, setStatus] = useState<ProjectStatus>(
    (project.status as ProjectStatus) ?? "active",
  );
  // Equipo del proyecto: "" = sin equipo. Permite asignar/cambiar el equipo de
  // CUALQUIER proyecto (incluido uno en blanco) — el backend ya lo soporta.
  const [teamId, setTeamId] = useState(project.team_id ?? "");
  const teamsQuery = useQuery({
    queryKey: ["teams", "list"],
    queryFn: () => apiFetch<{ id: string; name: string }[]>("/teams"),
    refetchOnWindowFocus: false,
    enabled: open,
  });

  // Reset form when the dialog reopens with a (possibly stale)
  // project — otherwise an edit, save, reopen still shows the old
  // typed value.
  useEffect(() => {
    if (open) {
      setName(project.name);
      setDescription(project.description ?? "");
      setStatus((project.status as ProjectStatus) ?? "active");
      setTeamId(project.team_id ?? "");
    }
  }, [open, project.name, project.description, project.status, project.team_id]);

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
          <DialogTitle>{t("editTitle")}</DialogTitle>
          <DialogDescription>{t("editDescription")}</DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-name">{t("fieldName")}</Label>
            <Input
              id="edit-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              data-testid="edit-project-name"
              required
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label>{t("fieldDescription")}</Label>
            <MarkdownTextarea
              value={description}
              onChange={setDescription}
              rows={5}
              data-testid="edit-project-description"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-status">{t("fieldStatus")}</Label>
            <Select
              id="edit-status"
              value={status}
              onChange={(e) => setStatus(e.target.value as ProjectStatus)}
              data-testid="edit-project-status"
            >
              {STATUS_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {t(o.labelKey)}
                </option>
              ))}
            </Select>
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-team">{t("fieldTeam")}</Label>
            <Select
              id="edit-team"
              value={teamId}
              onChange={(e) => setTeamId(e.target.value)}
              data-testid="edit-project-team"
            >
              <option value="">{t("noTeam")}</option>
              {(teamsQuery.data ?? []).map((team) => (
                <option key={team.id} value={team.id}>
                  {team.name}
                </option>
              ))}
            </Select>
            <p className="text-muted-foreground text-xs">{t("teamHint")}</p>
          </div>
          {mutation.isError && (
            <p
              className="bg-danger-soft text-danger-soft-foreground rounded p-2 text-xs"
              data-testid="edit-project-error"
            >
              {errorText(mutation.error)}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            {t("cancel")}
          </Button>
          <Button
            disabled={!name.trim() || mutation.isPending}
            onClick={() =>
              mutation.mutate({
                name: name.trim(),
                description: description.trim() || null,
                status,
                team_id: teamId || null,
              })
            }
            data-testid="edit-project-save"
          >
            {mutation.isPending ? t("saving") : t("save")}
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
  const t = useT("projectHub");
  const errorText = useErrorText();
  const [typed, setTyped] = useState("");
  const matches = typed === project.name;

  /**
   * Cerrar SIEMPRE limpia la confirmación tecleada.
   *
   * El botón Cancelar llamaba a `onOpenChange(false)` directamente, saltándose
   * el envoltorio del `<Dialog>` que hacía el reset: al reabrir, el nombre
   * seguía escrito y el botón destructivo estaba HABILITADO de entrada. La
   * confirmación por nombre existe justo para que borrar sea un acto
   * deliberado; si sobrevive a un "Cancelar", el siguiente borrado es un click.
   * Detectado el 2026-08-19 por `project-delete.spec.ts` (el mismo defecto
   * estaba en las cuatro pantallas con confirmación por nombre).
   */
  const closeAndReset = () => {
    setTyped("");
    onOpenChange(false);
  };

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
        if (!v) closeAndReset();
        else onOpenChange(v);
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("deleteTitle")}</DialogTitle>
          <DialogDescription>
            {t("deleteDescriptionIntro")}
            <strong>{t("deleteDescriptionStrong")}</strong>
            {t("deleteDescriptionRest")}
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="space-y-3">
          <p className="text-sm">
            {t("deleteConfirmPrompt")}
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
              {errorText(mutation.error)}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={closeAndReset}>
            {t("cancel")}
          </Button>
          <Button
            variant="destructive"
            disabled={!matches || mutation.isPending}
            onClick={() => mutation.mutate()}
            data-testid="delete-project-confirm"
          >
            {mutation.isPending ? t("deleting") : t("deleteConfirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
